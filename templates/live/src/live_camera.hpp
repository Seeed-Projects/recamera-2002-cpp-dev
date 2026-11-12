#ifndef RECAMERA_LIVE_CAMERA_HPP
#define RECAMERA_LIVE_CAMERA_HPP

/* Latest-frame relay for ma::Camera.
 *
 * Why this exists
 * ---------------
 * ma::Camera's per-channel queue is a FIFO whose depth is the channel fps
 * (ma_camera_sg200x.cpp: `new MessageBox(param.fps)`), `fetch()` returns the
 * oldest frame, and a full queue makes `post()` wait one frame interval and
 * then DROP the new frame.  A consumer that is slower than the channel fps
 * therefore spends all of its time reading old frames: measured on SG2002 with
 * CH0 at 15 fps and a ~5 fps consumer, frames were 2.9-3.2 s old; at CH0=5 fps
 * the same consumer saw 0.08-0.20 s.  The inference cost did not change, only
 * the queue.
 *
 * FrameRelay runs one lightweight thread per channel that drains the queue at
 * the channel cadence and keeps only the newest frame in a single slot.  The
 * consumer blocks for the next fresh frame instead of fetching the queue head,
 * so a slow model never accumulates a backlog.  The consumer owns the frame it
 * takes and must call camera->returnFrame() when done; the relay returns the
 * frames it supersedes.
 *
 * Copy mode (kPhysical=0) is required, as with any cross-thread frame use.
 */

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>

#include <sscma.h>

#include "live_udp.h"

namespace recamera_live {

struct LatestFrame {
    ma_img_t img{};          /* owned by the consumer until returnFrame() */
    int64_t acquired_ms = 0; /* wall clock when the relay dequeued it */
    int age_ms = 0;          /* VPSS callback -> relay dequeue, modulo 4.29 s */
    uint64_t sequence = 0;   /* monotonically increasing relay counter */
};

class FrameRelay {
   public:
    FrameRelay() = default;
    ~FrameRelay() { stop(); }

    FrameRelay(const FrameRelay&) = delete;
    FrameRelay& operator=(const FrameRelay&) = delete;

    void start(ma::Camera* camera, ma_pixel_format_t format) {
        stop();
        camera_ = camera;
        format_ = format;
        running_.store(true, std::memory_order_release);
        thread_ = std::thread([this] { run(); });
    }

    void stop() {
        if (!running_.exchange(false, std::memory_order_acq_rel)) {
            if (thread_.joinable()) thread_.join();
            return;
        }
        cv_.notify_all();
        if (thread_.joinable()) thread_.join();

        ma_img_t stale{};
        bool had = false;
        {
            std::lock_guard<std::mutex> lock(mu_);
            if (has_) {
                stale = slot_;
                has_ = false;
                had = true;
            }
        }
        if (had && camera_) camera_->returnFrame(stale);
    }

    /* Block until a frame newer than the last one taken is available.
     * timeout_ms < 0 waits forever.  Returns false on timeout or shutdown. */
    bool wait(LatestFrame& out, int timeout_ms = -1) {
        std::unique_lock<std::mutex> lock(mu_);
        auto ready = [this] {
            return !running_.load() || (has_ && sequence_ > last_taken_);
        };
        if (timeout_ms < 0) {
            cv_.wait(lock, ready);
        } else if (!cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms), ready)) {
            return false;
        }
        return take_locked(out);
    }

    /* Non-blocking variant.  Returns true if a newer frame was moved out. */
    bool take(LatestFrame& out) {
        std::lock_guard<std::mutex> lock(mu_);
        return take_locked(out);
    }

    /* Number of frames the relay has dequeued so far. */
    uint64_t generation() {
        std::lock_guard<std::mutex> lock(mu_);
        return sequence_;
    }

   private:
    bool take_locked(LatestFrame& out) {
        if (!has_ || sequence_ <= last_taken_) return false;
        out.img = slot_;
        out.acquired_ms = acquired_ms_;
        out.age_ms = age_ms_;
        out.sequence = sequence_;
        last_taken_ = sequence_;
        has_ = false;
        return true;
    }

    void run() {
        while (running_.load(std::memory_order_acquire)) {
            ma_img_t frame{};
            if (camera_ == nullptr ||
                camera_->retrieveFrame(frame, format_) != MA_OK) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
                continue;
            }

            /* ma_img_t::timestamp is the low 32 bits of Tick::current()
             * (CLOCK_MONOTONIC nanoseconds).  The unsigned difference is the
             * frame age modulo 4.29 s, which is unambiguous for the sub-second
             * ages a draining relay sees. */
            const uint32_t age_ms =
                (static_cast<uint32_t>(ma::Tick::current()) - frame.timestamp) /
                1000000u;

            ma_img_t previous{};
            bool had_previous = false;
            {
                std::lock_guard<std::mutex> lock(mu_);
                if (has_) {
                    previous = slot_;
                    had_previous = true;
                }
                slot_ = frame;
                acquired_ms_ = now_ms();
                age_ms_ = static_cast<int>(age_ms);
                ++sequence_;
                has_ = true;
            }
            if (had_previous) camera_->returnFrame(previous);
            cv_.notify_all();
        }
    }

    ma::Camera* camera_ = nullptr;
    ma_pixel_format_t format_ = MA_PIXEL_FORMAT_RGB888;

    std::mutex mu_;
    std::condition_variable cv_;
    ma_img_t slot_{};
    int64_t acquired_ms_ = 0;
    int age_ms_ = 0;
    uint64_t sequence_ = 0;
    uint64_t last_taken_ = 0;
    bool has_ = false;

    std::atomic<bool> running_{false};
    std::thread thread_;
};

}  // namespace recamera_live

#endif  // RECAMERA_LIVE_CAMERA_HPP