/* reCamera live UDP transport implementation (POSIX, no SDK dependency). */
#include "live_udp.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

namespace recamera_live {
namespace {

constexpr uint32_t kMagic = 0x52435631u; // "RCV1"
constexpr uint8_t kVersion = 1;

enum MessageType : uint8_t {
    kVideo = 1,
    kMeta = 2,
    kHello = 3,
    kLog = 4,
    kSnapshot = 5,
    kStats = 6,
};

constexpr uint16_t kFlagKeyframe = 0x0001;
constexpr size_t kHeaderSize = 32;
constexpr size_t kMetaJsonBudget = 8192; // must match serve_demo.META_MAX_BYTES

struct Runtime {
    Config config;
    Params params;
    std::atomic<bool> running{false};

    int video_sock = -1;
    int control_sock = -1;
    sockaddr_in video_dest{};
    sockaddr_in control_addr{};

    std::mutex send_mutex;
    std::atomic<uint32_t> seq{1};
    std::atomic<uint32_t> last_video_seq{0};

    std::atomic<uint64_t> video_frames{0};
    std::atomic<uint64_t> video_bytes{0};

    std::mutex nal_mutex;
    std::vector<uint8_t> nal_buffer;
    uint32_t nal_count = 0;
    uint32_t nal_next = 0;
    bool nal_active = false;
    bool nal_key = false;
    int64_t nal_ts = 0;

    std::thread control_thread;
    std::thread telemetry_thread;

    std::atomic<uint64_t> stat_frames{0};
    std::atomic<uint64_t> stat_bytes{0};
    std::atomic<int64_t> stat_last_ms{0};
};

Runtime g;

void put_u16(uint8_t* p, uint16_t v) {
    p[0] = static_cast<uint8_t>(v & 0xFF);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
}

void put_u32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v & 0xFF);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
    p[2] = static_cast<uint8_t>((v >> 16) & 0xFF);
    p[3] = static_cast<uint8_t>((v >> 24) & 0xFF);
}

void put_u64(uint8_t* p, uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        p[i] = static_cast<uint8_t>((v >> (8 * i)) & 0xFF);
    }
}

std::string json_escape(const std::string& input) {
    std::string out;
    out.reserve(input.size() + 8);
    for (unsigned char c : input) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out;
}

std::string float_text(float value, int decimals = 5) {
    if (!std::isfinite(value)) {
        value = 0.0f;
    }
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.*f", decimals, static_cast<double>(value));
    return std::string(buf);
}

void append_float(std::string& out, const char* key, float value, int decimals = 5) {
    out += "\"";
    out += key;
    out += "\":";
    out += float_text(value, decimals);
}

void append_int(std::string& out, const char* key, long long value) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%lld", value);
    out += "\"";
    out += key;
    out += "\":";
    out += buf;
}

bool send_message(uint8_t type, const uint8_t* payload, size_t size, uint32_t seq,
                  int64_t ts_ms, uint16_t width, uint16_t height, uint16_t flags) {
    if (!g.running.load() || g.video_sock < 0) {
        return false;
    }
    size_t max_payload = g.config.max_payload;
    if (max_payload < 64) {
        max_payload = 64;
    }
    if (max_payload > 60000) {
        max_payload = 60000;
    }
    size_t count = size == 0 ? 1 : (size + max_payload - 1) / max_payload;
    if (count > 0xFFFF) {
        return false;
    }

    std::lock_guard<std::mutex> lock(g.send_mutex);
    if (!g.running.load() || g.video_sock < 0) {
        return false;
    }
    for (size_t index = 0; index < count; ++index) {
        const size_t offset = index * max_payload;
        const size_t chunk = size == 0 ? 0 : std::min(max_payload, size - offset);
        std::vector<uint8_t> packet(kHeaderSize + chunk);
        uint8_t* h = packet.data();
        put_u32(h + 0, kMagic);
        h[4] = kVersion;
        h[5] = type;
        put_u16(h + 6, flags);
        put_u32(h + 8, seq);
        put_u64(h + 12, static_cast<uint64_t>(ts_ms));
        put_u16(h + 20, width);
        put_u16(h + 22, height);
        put_u16(h + 24, static_cast<uint16_t>(index));
        put_u16(h + 26, static_cast<uint16_t>(count));
        put_u16(h + 28, static_cast<uint16_t>(chunk));
        put_u16(h + 30, 0);
        if (chunk > 0) {
            std::memcpy(packet.data() + kHeaderSize, payload + offset, chunk);
        }
        const ssize_t sent = sendto(g.video_sock, packet.data(), packet.size(), 0,
                                    reinterpret_cast<const sockaddr*>(&g.video_dest),
                                    sizeof(g.video_dest));
        if (sent < 0) {
            return false;
        }
    }
    return true;
}

bool has_start_code(const uint8_t* data, size_t size) {
    if (size >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1) {
        return true;
    }
    return size >= 3 && data[0] == 0 && data[1] == 0 && data[2] == 1;
}

void append_annex_b(std::vector<uint8_t>& out, const uint8_t* data, size_t size) {
    if (size == 0) {
        return;
    }
    if (!has_start_code(data, size)) {
        out.push_back(0);
        out.push_back(0);
        out.push_back(0);
        out.push_back(1);
    }
    out.insert(out.end(), data, data + size);
}

void params_json(std::string& out) {
    out += "\"params\":{";
    append_float(out, "confidence", g.params.confidence.load(), 3);
    out += ",";
    append_float(out, "nms", g.params.nms.load(), 3);
    out += ",";
    append_int(out, "max_det", g.params.max_det.load());
    out += ",";
    append_float(out, "lane_thr", g.params.lane_thr.load(), 3);
    out += ",";
    append_float(out, "roi_top", g.params.roi_top.load(), 3);
    out += ",";
    append_int(out, "min_lane_px", g.params.min_lane_px.load());
    out += ",";
    append_float(out, "lane_ema", g.params.lane_ema.load(), 3);
    out += ",";
    append_int(out, "max_lane_pts", g.params.max_lane_pts.load());
    out += ",\"show_roi\":";
    out += g.params.show_roi.load() ? "true" : "false";
    out += ",\"show_lanes\":";
    out += g.params.show_lanes.load() ? "true" : "false";
    out += ",\"show_boxes\":";
    out += g.params.show_boxes.load() ? "true" : "false";
    for (const auto& kv : g.params.extras_snapshot()) {
        out += ",";
        out += "\"" + json_escape(kv.first) + "\":";
        const std::string& value = kv.second;
        char* end = nullptr;
        const double number = std::strtod(value.c_str(), &end);
        if (end != value.c_str() && end && *end == '\0' && std::isfinite(number)) {
            out += float_text(static_cast<float>(number), 6);
        } else if (value == "true" || value == "false") {
            out += value;
        } else {
            out += "\"" + json_escape(value) + "\"";
        }
    }
    out += "}";
}

const char* shape_kind_name(int kind) {
    switch (kind) {
        case kPolygon: return "polygon";
        case kPoint: return "point";
        case kText: return "text";
        case kPolyline:
        default: return "polyline";
    }
}

void request_control_ack(const sockaddr_in& peer, socklen_t peer_len, uint32_t seq,
                         bool ok, const std::string& applied, const std::string& ignored) {
    std::string line = "RCC1 ACK seq=" + std::to_string(seq) + " ok=" + (ok ? "1" : "0");
    line += " applied=" + applied;
    line += " ignored=" + ignored;
    line += "\n";
    sendto(g.control_sock, line.data(), line.size(), 0,
           reinterpret_cast<const sockaddr*>(&peer), peer_len);
}

void handle_control_line(const std::string& line, const sockaddr_in& peer, socklen_t peer_len) {
    std::istringstream stream(line);
    std::string magic;
    std::string command;
    stream >> magic >> command;
    if (magic != "RCC1") {
        return;
    }
    if (command == "PING") {
        const char* pong = "RCC1 PONG\n";
        sendto(g.control_sock, pong, std::strlen(pong), 0,
               reinterpret_cast<const sockaddr*>(&peer), peer_len);
        return;
    }
    if (command != "SET") {
        return;
    }

    uint32_t seq = 0;
    std::string applied;
    std::string ignored;
    std::string token;
    while (stream >> token) {
        const size_t eq = token.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string key = token.substr(0, eq);
        const std::string value = token.substr(eq + 1);
        if (key == "seq") {
            seq = static_cast<uint32_t>(std::strtoul(value.c_str(), nullptr, 10));
            continue;
        }
        if (g.params.apply(key, value, &applied)) {
            continue;
        }
        if (!ignored.empty()) {
            ignored += ",";
        }
        ignored += key;
    }
    request_control_ack(peer, peer_len, seq, !applied.empty() && ignored.empty(), applied, ignored);
}

void control_loop() {
    char buffer[2048];
    while (g.running.load()) {
        sockaddr_in peer{};
        socklen_t peer_len = sizeof(peer);
        const ssize_t n = recvfrom(g.control_sock, buffer, sizeof(buffer) - 1, 0,
                                   reinterpret_cast<sockaddr*>(&peer), &peer_len);
        if (n <= 0) {
            continue;
        }
        buffer[n] = '\0';
        handle_control_line(std::string(buffer), peer, peer_len);
    }
}

void telemetry_loop() {
    int64_t last_hello = 0;
    int64_t last_stats = now_ms();
    while (g.running.load()) {
        const int64_t now = now_ms();
        if (last_hello == 0 || now - last_hello >= g.config.hello_interval_ms) {
            send_hello();
            last_hello = now;
        }
        if (now - last_stats >= g.config.stats_interval_ms) {
            send_stats();
            last_stats = now;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
}

bool send_overlay_field(const char* field, const char* payload) {
    if (!field || !*field) {
        return false;
    }
    if (!payload) {
        payload = "[]";
    }
    const uint32_t seq = g.seq.fetch_add(1);
    const int64_t ts = now_ms();
    std::string json;
    json.reserve(320 + std::strlen(payload));
    json += "{";
    append_int(json, "seq", static_cast<long long>(seq));
    json += ",";
    append_int(json, "ts_ms", static_cast<long long>(ts));
    json += ",";
    append_int(json, "video_seq", static_cast<long long>(g.last_video_seq.load()));
    json += ",";
    append_int(json, "frame_w", g.config.video_width);
    json += ",";
    append_int(json, "frame_h", g.config.video_height);
    json += ",";
    append_int(json, "model_w", g.config.model_width);
    json += ",";
    append_int(json, "model_h", g.config.model_height);
    json += ",\"preprocess\":\"" + json_escape(g.config.preprocess) + "\"";
    json += ",";
    append_float(json, "scale", g.config.scale, 6);
    json += ",";
    append_int(json, "pad_x", g.config.pad_x);
    json += ",";
    append_int(json, "pad_y", g.config.pad_y);
    json += ",";
    params_json(json);
    json += ",\"dets\":[],\"";
    json += field;
    json += "\":";
    json += payload;
    json += "}";
    if (json.size() > kMetaJsonBudget) {
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            send_log("warn", "meta budget exceeded; reduce shapes/points");
        }
        return false;
    }
    return send_message(kMeta, reinterpret_cast<const uint8_t*>(json.data()), json.size(), seq, ts,
                        static_cast<uint16_t>(g.config.model_width),
                        static_cast<uint16_t>(g.config.model_height), 0);
}

} // namespace

// ---------------------------------------------------------------------------
// Params
// ---------------------------------------------------------------------------

bool Params::apply(const std::string& key, const std::string& value, std::string* applied) {
    auto mark = [&]() {
        if (applied) {
            if (!applied->empty()) {
                *applied += ",";
            }
            *applied += key;
        }
        version.fetch_add(1);
        return true;
    };

    if (key == "confidence" || key == "nms") {
        char* end = nullptr;
        const float parsed = std::strtof(value.c_str(), &end);
        if (end == value.c_str() || (end && *end != '\0') || !std::isfinite(parsed) ||
            parsed < 0.001f || parsed > 1.0f) {
            return false;
        }
        if (key == "confidence") {
            confidence.store(parsed);
        } else {
            nms.store(parsed);
        }
        return mark();
    }
    if (key == "max_det") {
        char* end = nullptr;
        const long parsed = std::strtol(value.c_str(), &end, 10);
        if (end == value.c_str() || (end && *end != '\0') || parsed < 1 || parsed > 1000) {
            return false;
        }
        max_det.store(static_cast<int>(parsed));
        return mark();
    }
    if (key == "force_idr") {
        if (value == "1" || value == "true" || value == "yes") {
            force_idr.store(true);
        } else if (value == "0" || value == "false" || value == "no") {
            force_idr.store(false);
        } else {
            return false;
        }
        return mark();
    }
    if (key == "lane_thr" || key == "roi_top" || key == "lane_ema") {
        char* end = nullptr;
        const float parsed = std::strtof(value.c_str(), &end);
        if (end == value.c_str() || (end && *end != '\0') || !std::isfinite(parsed)) {
            return false;
        }
        if (key == "lane_thr") {
            if (parsed < 0.05f || parsed > 0.95f) return false;
            lane_thr.store(parsed);
        } else if (key == "roi_top") {
            if (parsed < 0.0f || parsed > 0.9f) return false;
            roi_top.store(parsed);
        } else {
            if (parsed < 0.0f || parsed > 0.95f) return false;
            lane_ema.store(parsed);
        }
        return mark();
    }
    if (key == "min_lane_px" || key == "max_lane_pts") {
        char* end = nullptr;
        const long parsed = std::strtol(value.c_str(), &end, 10);
        if (end == value.c_str() || (end && *end != '\0')) {
            return false;
        }
        if (key == "min_lane_px") {
            if (parsed < 20 || parsed > 1080) return false;
            min_lane_px.store(static_cast<int>(parsed));
        } else {
            if (parsed < 10 || parsed > 80) return false;
            max_lane_pts.store(static_cast<int>(parsed));
        }
        return mark();
    }
    if (key == "show_roi" || key == "show_lanes" || key == "show_boxes") {
        bool parsed = false;
        if (value == "1" || value == "true" || value == "yes" || value == "on") {
            parsed = true;
        } else if (value == "0" || value == "false" || value == "no" || value == "off") {
            parsed = false;
        } else {
            return false;
        }
        if (key == "show_roi") {
            show_roi.store(parsed);
        } else if (key == "show_lanes") {
            show_lanes.store(parsed);
        } else {
            show_boxes.store(parsed);
        }
        return mark();
    }
    // Generic demo tunables: keep a bounded set of simple identifiers so a
    // lane demo can read e.g. lane_thr without changing this header.
    if (key.size() < 2 || key.size() > 32) {
        return false;
    }
    if (!(key[0] >= 'a' && key[0] <= 'z')) {
        return false;
    }
    for (char c : key) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (!ok) {
            return false;
        }
    }
    if (value.empty() || value.size() > 32 || value.find_first_of(" \t\r\n") != std::string::npos) {
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(extras_mutex_);
        const auto it = extras_.find(key);
        if (it == extras_.end() && extras_.size() >= 16) {
            return false;
        }
        extras_[key] = value;
    }
    return mark();
}

bool Params::get_number(const std::string& key, float* out) const {
    if (!out) {
        return false;
    }
    std::lock_guard<std::mutex> lock(extras_mutex_);
    const auto it = extras_.find(key);
    if (it == extras_.end()) {
        return false;
    }
    const char* start = it->second.c_str();
    char* end = nullptr;
    const float parsed = std::strtof(start, &end);
    if (end == start || (end && *end != '\0') || !std::isfinite(parsed)) {
        return false;
    }
    *out = parsed;
    return true;
}

bool Params::get_bool(const std::string& key, bool* out) const {
    if (!out) {
        return false;
    }
    std::lock_guard<std::mutex> lock(extras_mutex_);
    const auto it = extras_.find(key);
    if (it == extras_.end()) {
        return false;
    }
    const std::string& value = it->second;
    if (value == "1" || value == "true" || value == "yes" || value == "on") {
        *out = true;
        return true;
    }
    if (value == "0" || value == "false" || value == "no" || value == "off") {
        *out = false;
        return true;
    }
    return false;
}

std::map<std::string, std::string> Params::extras_snapshot() const {
    std::lock_guard<std::mutex> lock(extras_mutex_);
    return extras_;
}

void Params::clear_extras() {
    std::lock_guard<std::mutex> lock(extras_mutex_);
    extras_.clear();
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

Params& params() {
    return g.params;
}

bool running() {
    return g.running.load();
}

bool force_keyframe_requested() {
    return g.params.take_force_idr();
}

bool start(const Config& config) {
    if (g.running.load()) {
        return true;
    }
    if (config.host.empty() || config.video_port == 0 || config.control_port == 0) {
        return false;
    }

    g.config = config;
    g.params.confidence.store(0.60f);
    g.params.nms.store(0.45f);
    g.params.max_det.store(20);
    g.params.force_idr.store(false);
    g.params.lane_thr.store(0.55f);
    g.params.roi_top.store(0.45f);
    g.params.min_lane_px.store(150);
    g.params.lane_ema.store(0.60f);
    g.params.max_lane_pts.store(40);
    g.params.show_roi.store(true);
    g.params.show_lanes.store(true);
    g.params.show_boxes.store(true);
    g.params.clear_extras();
    g.params.version.store(0);
    if (g.config.max_payload < 64) {
        g.config.max_payload = 64;
    }
    if (g.config.max_payload > 60000) {
        g.config.max_payload = 60000;
    }

    g.video_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (g.video_sock < 0) {
        return false;
    }
    std::memset(&g.video_dest, 0, sizeof(g.video_dest));
    g.video_dest.sin_family = AF_INET;
    g.video_dest.sin_port = htons(config.video_port);
    if (inet_pton(AF_INET, config.host.c_str(), &g.video_dest.sin_addr) != 1) {
        close(g.video_sock);
        g.video_sock = -1;
        return false;
    }

    g.control_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (g.control_sock < 0) {
        close(g.video_sock);
        g.video_sock = -1;
        return false;
    }
    int reuse = 1;
    setsockopt(g.control_sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    std::memset(&g.control_addr, 0, sizeof(g.control_addr));
    g.control_addr.sin_family = AF_INET;
    g.control_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    g.control_addr.sin_port = htons(config.control_port);
    if (bind(g.control_sock, reinterpret_cast<sockaddr*>(&g.control_addr),
             sizeof(g.control_addr)) != 0) {
        close(g.control_sock);
        close(g.video_sock);
        g.control_sock = -1;
        g.video_sock = -1;
        return false;
    }
    timeval timeout{};
    timeout.tv_sec = 0;
    timeout.tv_usec = 500000;
    setsockopt(g.control_sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    g.seq.store(1);
    g.last_video_seq.store(0);
    g.video_frames.store(0);
    g.video_bytes.store(0);
    g.stat_frames.store(0);
    g.stat_bytes.store(0);
    g.stat_last_ms.store(now_ms());
    g.running.store(true);

    g.control_thread = std::thread(control_loop);
    g.telemetry_thread = std::thread(telemetry_loop);
    send_hello();
    return true;
}

void stop() {
    if (!g.running.exchange(false)) {
        return;
    }
    if (g.control_thread.joinable()) {
        g.control_thread.join();
    }
    if (g.telemetry_thread.joinable()) {
        g.telemetry_thread.join();
    }
    if (g.control_sock >= 0) {
        close(g.control_sock);
        g.control_sock = -1;
    }
    {
        std::lock_guard<std::mutex> lock(g.send_mutex);
        if (g.video_sock >= 0) {
            close(g.video_sock);
            g.video_sock = -1;
        }
    }
    {
        std::lock_guard<std::mutex> lock(g.nal_mutex);
        g.nal_buffer.clear();
        g.nal_active = false;
        g.nal_count = 0;
        g.nal_next = 0;
    }
}

bool send_access_unit(const uint8_t* data, size_t size, int64_t ts_ms, bool keyframe) {
    if (!data || size == 0) {
        return false;
    }
    std::vector<uint8_t> prefixed;
    const uint8_t* payload = data;
    size_t payload_size = size;
    if (!has_start_code(data, size)) {
        prefixed.reserve(size + 4);
        append_annex_b(prefixed, data, size);
        payload = prefixed.data();
        payload_size = prefixed.size();
    }
    const uint32_t seq = g.seq.fetch_add(1);
    const bool ok = send_message(kVideo, payload, payload_size, seq, ts_ms == 0 ? now_ms() : ts_ms,
                                 static_cast<uint16_t>(g.config.video_width),
                                 static_cast<uint16_t>(g.config.video_height),
                                 keyframe ? kFlagKeyframe : 0);
    if (ok) {
        g.last_video_seq.store(seq);
        g.video_frames.fetch_add(1);
        g.video_bytes.fetch_add(payload_size);
    }
    return ok;
}

bool send_nal(const uint8_t* data, size_t size, uint32_t frame_index, uint32_t frame_count,
              bool keyframe, int64_t ts_ms) {
    if (!data || size == 0) {
        return false;
    }
    if (frame_count <= 1) {
        return send_access_unit(data, size, ts_ms, keyframe);
    }

    std::vector<uint8_t> complete;
    bool send_now = false;
    bool key = false;
    {
        std::lock_guard<std::mutex> lock(g.nal_mutex);
        if (frame_index == 0 || !g.nal_active || frame_count != g.nal_count) {
            g.nal_buffer.clear();
            g.nal_count = frame_count;
            g.nal_next = 0;
            g.nal_active = true;
            g.nal_key = false;
            g.nal_ts = ts_ms;
        }
        if (frame_index != g.nal_next) {
            // A dropped pack cannot be repaired; start clean at this NAL.
            g.nal_buffer.clear();
            g.nal_next = frame_index;
        }
        append_annex_b(g.nal_buffer, data, size);
        g.nal_key = g.nal_key || keyframe;
        g.nal_next = frame_index + 1;
        if (g.nal_next >= g.nal_count) {
            complete.swap(g.nal_buffer);
            key = g.nal_key;
            g.nal_key = false;
            g.nal_active = false;
            g.nal_count = 0;
            g.nal_next = 0;
            send_now = true;
        }
    }
    if (!send_now) {
        return true;
    }
    return send_access_unit(complete.data(), complete.size(),
                            ts_ms == 0 ? g.nal_ts : ts_ms, key);
}

bool send_meta(const Detection* detections, size_t count, int64_t ts_ms, int64_t video_seq,
               float infer_ms, float fps, int dropped,
               const Shape* shapes, size_t n_shapes,
               int64_t frame_ts_ms, int age_ms) {
    const uint32_t seq = g.seq.fetch_add(1);
    std::string json;
    json.reserve(256 + count * 96 + n_shapes * 256);
    json += "{";
    append_int(json, "seq", static_cast<long long>(seq));
    json += ",";
    append_int(json, "ts_ms", static_cast<long long>(ts_ms == 0 ? now_ms() : ts_ms));
    json += ",";
    append_int(json, "video_seq", static_cast<long long>(video_seq < 0
                                                              ? g.last_video_seq.load()
                                                              : video_seq));
    json += ",";
    append_float(json, "infer_ms", infer_ms, 2);
    json += ",";
    append_float(json, "fps", fps, 2);
    json += ",";
    if (frame_ts_ms > 0) {
        append_int(json, "frame_ts_ms", static_cast<long long>(frame_ts_ms));
        json += ",";
    }
    if (age_ms >= 0) {
        append_int(json, "age_ms", age_ms);
        json += ",";
    }
    append_int(json, "frame_w", g.config.video_width);
    json += ",";
    append_int(json, "frame_h", g.config.video_height);
    json += ",";
    append_int(json, "model_w", g.config.model_width);
    json += ",";
    append_int(json, "model_h", g.config.model_height);
    json += ",\"preprocess\":\"" + json_escape(g.config.preprocess) + "\"";
    json += ",";
    append_float(json, "scale", g.config.scale, 6);
    json += ",";
    append_int(json, "pad_x", g.config.pad_x);
    json += ",";
    append_int(json, "pad_y", g.config.pad_y);
    json += ",";
    append_int(json, "dropped", dropped);
    json += ",";
    params_json(json);
    json += ",\"dets\":[";
    const size_t det_count = std::min<size_t>(count, 200);
    for (size_t i = 0; i < det_count; ++i) {
        if (json.size() + 96 > kMetaJsonBudget) {
            break;
        }
        if (i) {
            json += ",";
        }
        const Detection& d = detections[i];
        json += "{";
        append_float(json, "x", d.x, 5);
        json += ",";
        append_float(json, "y", d.y, 5);
        json += ",";
        append_float(json, "w", d.w, 5);
        json += ",";
        append_float(json, "h", d.h, 5);
        json += ",";
        append_float(json, "score", d.score, 4);
        json += ",";
        append_int(json, "target", d.target);
        json += "}";
    }

    json += "],\"shapes\":[";
    size_t emitted = 0;
    if (shapes && n_shapes) {
        const size_t limit = std::min<size_t>(n_shapes, kMaxShapes);
        for (size_t i = 0; i < limit; ++i) {
            const Shape& shape = shapes[i];
            if (!shape.pts || shape.n_pts == 0) {
                continue;
            }
            int kind = shape.kind;
            if (kind < kPolyline || kind > kText) {
                kind = kPolyline;
            }
            const size_t n_pts = std::min<size_t>(shape.n_pts, kMaxShapePoints);
            const bool closed = shape.closed >= 0 ? shape.closed != 0 : kind == kPolygon;
            if (kind == kPolygon && n_pts < 3) {
                continue;
            }
            if (kind == kPolyline && n_pts < 2) {
                continue;
            }
            if (json.size() + 160 + n_pts * 16 > kMetaJsonBudget) {
                break;
            }
            if (emitted) {
                json += ",";
            }
            json += "{\"kind\":\"";
            json += shape_kind_name(kind);
            json += "\",\"space\":\"";
            json += shape.space == kModelSpace ? "model" : "frame";
            json += "\",\"pts\":[";
            for (size_t j = 0; j < n_pts; ++j) {
                if (j) {
                    json += ",";
                }
                json += "[";
                json += float_text(shape.pts[j * 2], 4);
                json += ",";
                json += float_text(shape.pts[j * 2 + 1], 4);
                json += "]";
            }
            json += "]";
            if (shape.label && *shape.label) {
                json += ",\"label\":\"" + json_escape(shape.label) + "\"";
            }
            if (shape.color && *shape.color) {
                json += ",\"color\":\"" + json_escape(shape.color) + "\"";
            }
            if (shape.target >= 0) {
                json += ",";
                append_int(json, "target", shape.target);
            }
            if (shape.score > 0.0f) {
                json += ",";
                append_float(json, "score", shape.score, 4);
            }
            if (shape.width > 0.0f) {
                json += ",";
                append_float(json, "width", shape.width, 2);
            }
            if (shape.closed >= 0) {
                json += ",\"closed\":";
                json += closed ? "true" : "false";
            }
            json += "}";
            emitted++;
        }
    }
    json += "]}";
    if (json.size() > kMetaJsonBudget) {
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            send_log("warn", "meta budget exceeded; reduce detections/shapes/points");
        }
        return false;
    }

    return send_message(kMeta, reinterpret_cast<const uint8_t*>(json.data()), json.size(), seq,
                        ts_ms == 0 ? now_ms() : ts_ms,
                        static_cast<uint16_t>(g.config.model_width),
                        static_cast<uint16_t>(g.config.model_height), 0);
}

bool send_meta_json(const char* json) {
    if (!json || !*json) {
        return false;
    }
    if (std::strlen(json) > kMetaJsonBudget) {
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            send_log("warn", "meta_json budget exceeded; reduce payload");
        }
        return false;
    }
    const uint32_t seq = g.seq.fetch_add(1);
    const int64_t ts = now_ms();
    return send_message(kMeta, reinterpret_cast<const uint8_t*>(json), std::strlen(json), seq, ts,
                        static_cast<uint16_t>(g.config.model_width),
                        static_cast<uint16_t>(g.config.model_height), 0);
}

bool send_annotations_json(const char* annotations_json) {
    return send_overlay_field("annotations", annotations_json);
}

bool send_shapes_json(const char* shapes_json) {
    return send_overlay_field("shapes", shapes_json);
}

bool send_snapshot(const uint8_t* jpeg, size_t size, int width, int height, int64_t ts_ms) {
    if (!jpeg || size == 0) {
        return false;
    }
    const uint32_t seq = g.seq.fetch_add(1);
    return send_message(kSnapshot, jpeg, size, seq, ts_ms == 0 ? now_ms() : ts_ms,
                        static_cast<uint16_t>(width > 0 ? width : g.config.video_width),
                        static_cast<uint16_t>(height > 0 ? height : g.config.video_height), 0);
}

bool send_log(const char* level, const char* fmt, ...) {
    char message[1024];
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(message, sizeof(message), fmt, args);
    va_end(args);

    std::string json = "{\"level\":\"";
    json += json_escape(level ? level : "info");
    json += "\",\"msg\":\"";
    json += json_escape(message);
    json += "\"}";
    const int64_t ts = now_ms();
    const uint32_t seq = g.seq.fetch_add(1);
    return send_message(kLog, reinterpret_cast<const uint8_t*>(json.data()), json.size(), seq, ts,
                        0, 0, 0);
}

bool send_hello() {
    std::string json;
    json.reserve(512 + g.config.labels.size() * 24);
    json += "{\"v\":1,\"device\":\"reCamera 2002\",\"demo\":\"";
    json += json_escape(g.config.demo_name);
    json += "\",\"video\":{";
    json += "\"codec\":\"h264\",";
    append_int(json, "width", g.config.video_width);
    json += ",";
    append_int(json, "height", g.config.video_height);
    json += ",";
    append_int(json, "fps", g.config.video_fps);
    json += "},\"model\":{";
    append_int(json, "width", g.config.model_width);
    json += ",";
    append_int(json, "height", g.config.model_height);
    json += ",\"preprocess\":\"" + json_escape(g.config.preprocess) + "\",";
    append_float(json, "scale", g.config.scale, 6);
    json += ",";
    append_int(json, "pad_x", g.config.pad_x);
    json += ",";
    append_int(json, "pad_y", g.config.pad_y);
    json += "},";
    params_json(json);
    json += ",\"params_schema\":{";
    json += "\"confidence\":{\"type\":\"number\",\"min\":0.05,\"max\":0.95,\"step\":0.05,\"default\":0.60},";
    json += "\"nms\":{\"type\":\"number\",\"min\":0.05,\"max\":0.95,\"step\":0.05,\"default\":0.45},";
    json += "\"max_det\":{\"type\":\"integer\",\"min\":1,\"max\":100,\"step\":1,\"default\":20},";
    json += "\"lane_thr\":{\"type\":\"number\",\"min\":0.05,\"max\":0.95,\"step\":0.05,\"default\":0.55},";
    json += "\"roi_top\":{\"type\":\"number\",\"min\":0.0,\"max\":0.9,\"step\":0.05,\"default\":0.45},";
    json += "\"min_lane_px\":{\"type\":\"integer\",\"min\":20,\"max\":1080,\"step\":10,\"default\":150},";
    json += "\"lane_ema\":{\"type\":\"number\",\"min\":0.0,\"max\":0.95,\"step\":0.05,\"default\":0.60},";
    json += "\"max_lane_pts\":{\"type\":\"integer\",\"min\":10,\"max\":80,\"step\":10,\"default\":40},";
    json += "\"show_roi\":{\"type\":\"boolean\",\"default\":true},";
    json += "\"show_lanes\":{\"type\":\"boolean\",\"default\":true},";
    json += "\"show_boxes\":{\"type\":\"boolean\",\"default\":true}";
    json += "},\"overlay\":{\"kinds\":[\"polyline\",\"polygon\",\"point\",\"text\"],";
    json += "\"spaces\":[\"model\",\"frame\"],\"max_shapes\":16,\"max_pts\":80}";
    json += ",\"labels\":[";
    for (size_t i = 0; i < g.config.labels.size(); ++i) {
        if (i) {
            json += ",";
        }
        json += "\"" + json_escape(g.config.labels[i]) + "\"";
    }
    json += "]}";

    const uint32_t seq = g.seq.fetch_add(1);
    return send_message(kHello, reinterpret_cast<const uint8_t*>(json.data()), json.size(), seq,
                        now_ms(), static_cast<uint16_t>(g.config.video_width),
                        static_cast<uint16_t>(g.config.video_height), 0);
}

bool send_stats() {
    const int64_t now = now_ms();
    const int64_t last = g.stat_last_ms.exchange(now);
    const uint64_t frames = g.video_frames.load();
    const uint64_t bytes = g.video_bytes.load();
    const uint64_t prev_frames = g.stat_frames.exchange(frames);
    const uint64_t prev_bytes = g.stat_bytes.exchange(bytes);
    const int64_t elapsed = now - last;
    const double seconds = elapsed > 0 ? elapsed / 1000.0 : 1.0;
    const double fps = static_cast<double>(frames - prev_frames) / seconds;
    const double kbps = static_cast<double>(bytes - prev_bytes) * 8.0 / static_cast<double>(elapsed > 0 ? elapsed : 1);

    std::string json = "{";
    append_float(json, "fps", static_cast<float>(fps), 2);
    json += ",";
    append_float(json, "bitrate_kbps", static_cast<float>(kbps), 1);
    json += ",";
    append_int(json, "frames", static_cast<long long>(frames));
    json += ",";
    append_int(json, "bytes", static_cast<long long>(bytes));
    json += ",";
    json += "\"codec\":\"h264\"}";
    const uint32_t seq = g.seq.fetch_add(1);
    return send_message(kStats, reinterpret_cast<const uint8_t*>(json.data()), json.size(), seq,
                        now, 0, 0, 0);
}

// ---------------------------------------------------------------------------
// Self test
// ---------------------------------------------------------------------------

namespace {

uint16_t ephemeral_port() {
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        return 0;
    }
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;
    if (bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        close(sock);
        return 0;
    }
    socklen_t len = sizeof(addr);
    if (getsockname(sock, reinterpret_cast<sockaddr*>(&addr), &len) != 0) {
        close(sock);
        return 0;
    }
    const uint16_t port = ntohs(addr.sin_port);
    close(sock);
    return port;
}

uint16_t read_u16(const uint8_t* p) {
    return static_cast<uint16_t>(p[0] | (p[1] << 8));
}

uint32_t read_u32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

} // namespace

bool selftest() {
    const uint16_t video_port = ephemeral_port();
    const uint16_t control_port = ephemeral_port();
    if (video_port == 0 || control_port == 0) {
        std::printf("live self-test failed: cannot allocate ports\n");
        return false;
    }

    int receiver = socket(AF_INET, SOCK_DGRAM, 0);
    if (receiver < 0) {
        std::printf("live self-test failed: receiver socket\n");
        return false;
    }
    int reuse = 1;
    setsockopt(receiver, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    sockaddr_in recv_addr{};
    recv_addr.sin_family = AF_INET;
    recv_addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    recv_addr.sin_port = htons(video_port);
    if (bind(receiver, reinterpret_cast<sockaddr*>(&recv_addr), sizeof(recv_addr)) != 0) {
        close(receiver);
        std::printf("live self-test failed: receiver bind\n");
        return false;
    }
    timeval timeout{};
    timeout.tv_sec = 2;
    setsockopt(receiver, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    Config config;
    config.host = "127.0.0.1";
    config.video_port = video_port;
    config.control_port = control_port;
    config.demo_name = "selftest";
    config.video_width = 1920;
    config.video_height = 1080;
    config.model_width = 640;
    config.model_height = 640;
    config.stats_interval_ms = 60000;
    config.hello_interval_ms = 60000;
    if (!start(config)) {
        close(receiver);
        std::printf("live self-test failed: start\n");
        return false;
    }

    const uint8_t access_unit[] = {0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x84, 0x21, 0xA0};
    send_access_unit(access_unit, sizeof(access_unit), now_ms(), true);
    float shape_pts[] = {0.1f, 0.9f, 0.4f, 0.5f, 0.7f, 0.9f};
    Shape shape;
    shape.kind = kPolyline;
    shape.space = kFrameSpace;
    shape.pts = shape_pts;
    shape.n_pts = 3;
    shape.label = "selftest";
    shape.color = "#3ddc97";
    shape.width = 4.0f;
    send_meta(nullptr, 0, now_ms(), -1, 1.0f, 10.0f, 0, &shape, 1);

    bool got_video = false;
    bool got_keyframe = false;
    bool got_shapes = false;
    for (int i = 0; i < 32; ++i) {
        uint8_t buffer[2048];
        const ssize_t n = recvfrom(receiver, buffer, sizeof(buffer), 0, nullptr, nullptr);
        if (n < static_cast<ssize_t>(kHeaderSize)) {
            continue;
        }
        if (read_u32(buffer) != kMagic) {
            continue;
        }
        if (buffer[5] == kVideo) {
            got_video = true;
            got_keyframe = (read_u16(buffer + 6) & kFlagKeyframe) != 0;
        } else if (buffer[5] == kMeta) {
            std::string text(reinterpret_cast<char*>(buffer), static_cast<size_t>(n));
            if (text.find("\"shapes\":[{\"kind\":\"polyline\"") != std::string::npos) {
                got_shapes = true;
            }
        }
        if (got_video && got_shapes) {
            break;
        }
    }

    // Control plane: set a parameter and ask for an IDR.
    bool control_ok = false;
    bool custom_ok = false;
    int control_client = socket(AF_INET, SOCK_DGRAM, 0);
    if (control_client >= 0) {
        setsockopt(control_client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        sockaddr_in ctrl{};
        ctrl.sin_family = AF_INET;
        ctrl.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        ctrl.sin_port = htons(control_port);
        const char* line = "RCC1 SET seq=7 confidence=0.71 force_idr=1\n";
        sendto(control_client, line, std::strlen(line), 0,
               reinterpret_cast<sockaddr*>(&ctrl), sizeof(ctrl));
        char ack[512];
        const ssize_t n = recvfrom(control_client, ack, sizeof(ack) - 1, 0, nullptr, nullptr);
        if (n > 0) {
            ack[n] = '\0';
            control_ok = std::strstr(ack, "RCC1 ACK") != nullptr &&
                         std::strstr(ack, "ok=1") != nullptr &&
                         std::strstr(ack, "confidence") != nullptr;
        }
        const char* custom_line = "RCC1 SET seq=8 lane_custom=0.42 custom_flag=1\n";
        sendto(control_client, custom_line, std::strlen(custom_line), 0,
               reinterpret_cast<sockaddr*>(&ctrl), sizeof(ctrl));
        char custom_ack[512];
        const ssize_t n2 = recvfrom(control_client, custom_ack, sizeof(custom_ack) - 1,
                                    0, nullptr, nullptr);
        float custom_number = 0.0f;
        bool custom_flag = false;
        if (n2 > 0) {
            custom_ack[n2] = '\0';
            custom_ok = std::strstr(custom_ack, "lane_custom") != nullptr &&
                        g.params.get_number("lane_custom", &custom_number) &&
                        std::fabs(custom_number - 0.42f) < 0.001f &&
                        g.params.get_bool("custom_flag", &custom_flag) &&
                        custom_flag;
        }
        close(control_client);
    }

    const bool params_ok = std::fabs(g.params.confidence.load() - 0.71f) < 0.001f;
    const bool idr_ok = force_keyframe_requested();
    stop();
    close(receiver);

    const bool ok = got_video && got_keyframe && got_shapes && control_ok &&
                    custom_ok && params_ok && idr_ok;
    if (ok) {
        std::printf("live self-test ok\n");
    } else {
        std::printf("live self-test failed: video=%d keyframe=%d shapes=%d control=%d "
                    "custom=%d params=%d idr=%d\n",
                    got_video, got_keyframe, got_shapes, control_ok, custom_ok,
                    params_ok, idr_ok);
    }
    return ok;
}

} // namespace recamera_live