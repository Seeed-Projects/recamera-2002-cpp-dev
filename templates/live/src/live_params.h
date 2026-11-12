/* Runtime-tunable parameters shared by the demo and the control listener.
 *
 * The control plane is intentionally textual so the device does not need a
 * JSON library: "RCC1 SET seq=12 confidence=0.60 nms=0.45 max_det=20".
 */
#pragma once

#include <atomic>
#include <map>
#include <mutex>
#include <string>

namespace recamera_live {

struct Params {
    std::atomic<float> confidence{0.60f};
    std::atomic<float> nms{0.45f};
    std::atomic<int> max_det{20};
    std::atomic<bool> force_idr{false};

    /* Lane/overlay defaults. Non-lane demos may ignore them. */
    std::atomic<float> lane_thr{0.55f};
    std::atomic<float> roi_top{0.45f};
    std::atomic<int> min_lane_px{150};
    std::atomic<float> lane_ema{0.60f};
    std::atomic<int> max_lane_pts{40};
    std::atomic<bool> show_roi{true};
    std::atomic<bool> show_lanes{true};
    std::atomic<bool> show_boxes{true};

    std::atomic<unsigned> version{0};

    /* Apply one key=value pair. Returns true when the key is known and the
     * value is valid; appends the key to ``applied`` on success. Unknown keys
     * that look like simple identifiers are kept in a bounded extras map so a
     * demo can read its own tunables via get_number()/get_bool(). */
    bool apply(const std::string& key, const std::string& value, std::string* applied);

    bool get_number(const std::string& key, float* out) const;
    bool get_bool(const std::string& key, bool* out) const;
    std::map<std::string, std::string> extras_snapshot() const;
    void clear_extras();

    /* Consume a pending "force an IDR" request. */
    bool take_force_idr() { return force_idr.exchange(false); }

private:
    mutable std::mutex extras_mutex_;
    std::map<std::string, std::string> extras_;
};

} // namespace recamera_live