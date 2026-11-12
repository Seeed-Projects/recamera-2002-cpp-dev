/* reCamera live UDP transport.
 *
 * Drop this module into a C++ demo that already has a camera pipeline. It
 * sends H.264 access units, detection JSON, JPEG snapshots and log lines to a
 * host running scripts/serve_demo.py, and listens for parameter changes.
 *
 * It has no SDK dependency: the demo feeds it NAL units from
 * ma::Camera::retrieveFrame(frame, MA_PIXEL_FORMAT_H264) or from a raw VENC.
 */
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "live_params.h"

namespace recamera_live {

struct Config {
    std::string host;                    // host running serve_demo.py (required)
    uint16_t video_port = 5000;          // video / meta / log / snapshot
    uint16_t control_port = 5001;        // parameters (device listens)
    uint16_t max_payload = 1200;         // UDP payload budget per fragment
    std::string demo_name = "demo";

    int video_width = 1920;
    int video_height = 1080;
    int video_fps = 30;

    int model_width = 640;
    int model_height = 640;
    std::string preprocess = "stretch"; // stretch | letterbox | crop
    float scale = 1.0f;                 // letterbox scale, crop scale
    int pad_x = 0;
    int pad_y = 0;

    std::vector<std::string> labels;

    int hello_interval_ms = 10000;
    int stats_interval_ms = 2000;
};

struct Detection {
    float x;      // normalized center x [0,1], model input space
    float y;      // normalized center y
    float w;      // normalized width
    float h;      // normalized height
    float score;
    int32_t target;
};

enum ShapeKind : int {
    kPolyline = 1,
    kPolygon = 2,
    kPoint = 3,
    kText = 4,
};

enum ShapeSpace : int {
    kModelSpace = 0,
    kFrameSpace = 1,
};

constexpr uint32_t kMaxShapes = 16;
constexpr uint32_t kMaxShapePoints = 80;

/* Generic overlay annotation. Points are interleaved x,y pairs normalized to
 * [0,1]; ``space`` selects model-input space or frame space. The arrays are
 * borrowed for the duration of send_meta() only. */
struct Shape {
    int kind = kPolyline;
    int space = kFrameSpace;
    const float* pts = nullptr;
    uint32_t n_pts = 0;
    float score = 0.0f;
    int32_t target = -1;
    const char* label = nullptr;
    const char* color = nullptr;
    float width = 0.0f; // 1080p-baseline pixels; 0 = demo/UI default
    int closed = -1;    // -1 = kind default; 0/1 explicit
};

bool start(const Config& config);
void stop();
bool running();
Params& params();

/* Send one complete Annex-B access unit. If ``data`` has no start code the
 * module adds one. ``keyframe`` sets the IDR hint consumed by the host. */
bool send_access_unit(const uint8_t* data, size_t size, int64_t ts_ms, bool keyframe);

/* Feed NAL units one at a time as SSCMA delivers them. ``frame_count`` is
 * ma_img_t::count and ``frame_index`` is ma_img_t::index; the module groups
 * them into one access unit. */
bool send_nal(const uint8_t* data, size_t size, uint32_t frame_index, uint32_t frame_count,
              bool keyframe, int64_t ts_ms);

/* Detection metadata for the overlay. Coordinates stay in model-input space;
 * the host/browser applies the model->frame transform declared in hello. */
bool send_meta(const Detection* detections, size_t count, int64_t ts_ms, int64_t video_seq,
               float infer_ms, float fps, int dropped = 0,
               const Shape* shapes = nullptr, size_t n_shapes = 0,
               int64_t frame_ts_ms = 0, int age_ms = -1);

/* Send a complete meta JSON object. The caller owns the schema; include at
 * least ts_ms/frame_w/frame_h if you want the overlay to align. */
bool send_meta_json(const char* json);

/* Send only an annotations array (lane polylines, polygons, points, text).
 * The module adds the standard frame/model metadata around it. */
bool send_annotations_json(const char* annotations_json);

/* Same as send_annotations_json but uses the canonical "shapes" key. */
bool send_shapes_json(const char* shapes_json);

/* Optional 1 fps JPEG snapshot for evidence and thumbnails. */
bool send_snapshot(const uint8_t* jpeg, size_t size, int width, int height, int64_t ts_ms);

/* Structured log line shown in the host UI (and saved in the session). */
bool send_log(const char* level, const char* fmt, ...);

bool send_hello();
bool send_stats();

/* True after the host asked for a key frame through the control plane. The
 * demo should force an IDR on its next encode (or wait for the next GOP). */
bool force_keyframe_requested();

int64_t now_ms();

/* Loopback test used by smoke_demo.py. Sends a synthetic access unit and
 * exercises the control plane. Returns true on success. */
bool selftest();

} // namespace recamera_live