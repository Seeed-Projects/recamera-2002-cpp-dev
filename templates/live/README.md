# Live UDP skeleton (reCamera 2002)

This directory is overlaid by `new_demo.py --live udp` onto the generated demo. It connects the demo's H.264 video, detection results, logs and parameter control to `scripts/serve_demo.py` on the host; the device side **needs no Web UI, Node-RED or Supervisor**.

## What it does

| Direction | Content | Port |
|---|---|---|
| device → host | H.264 Annex-B access units (1080p30) | `5000/udp` |
| device → host | detection JSON, logs, JPEG snapshots, statistics | `5000/udp` |
| host → device | `RCC1 SET ...` parameter control | `5001/udp` |

`--peer` is the address of the host running `serve_demo.py`. When the manifest contains a `live` section and the environment variable `LIVE_PEER` exists, `deploy_demo.py start` automatically appends `--peer $LIVE_PEER`; the UI actions in `serve_demo.py` set it automatically from `--device-host`. When running manually, just pass `--peer <host-ip>` yourself.

The protocol header is 32 bytes and the default fragment size is 1200 bytes; the full definition is in `scripts/devkit/live.py`. The control plane is ASCII lines, so the device side needs no JSON library.

## Hooking it up to your own camera pipeline

SSCMA's `ma::Camera` already supports an H.264 channel natively. A typical wiring:

```cpp
#include <sscma.h>
#include <video.h>
#include "live_udp.h"

recamera_live::Config cfg;
cfg.host = "<IP of the host running serve_demo.py>";
cfg.demo_name = "my_demo";
cfg.video_width = 1920;
cfg.video_height = 1080;
cfg.video_fps = 30;
cfg.model_width = 640;
cfg.model_height = 640;
cfg.preprocess = "stretch";   // or "letterbox" / "crop"
recamera_live::start(cfg);

auto* camera = /* the Camera from Device::getInstance()->getSensors() */;
camera->init(0);              // preset 0 = 1920x1080 @ 30fps

ma::Camera::CtrlValue val;
// inference channel: 640x640
val.i32 = 0;
camera->commandCtrl(ma::Camera::CtrlType::kChannel, ma::Camera::CtrlMode::kWrite, val);
val.u16s[0] = 640;
val.u16s[1] = 640;
camera->commandCtrl(ma::Camera::CtrlType::kWindow, ma::Camera::CtrlMode::kWrite, val);

// video channel: H.264 1080p30
val.i32 = 2;
camera->commandCtrl(ma::Camera::CtrlType::kChannel, ma::Camera::CtrlMode::kWrite, val);
val.u16s[0] = 1920;
val.u16s[1] = 1080;
camera->commandCtrl(ma::Camera::CtrlType::kWindow, ma::Camera::CtrlMode::kWrite, val);
val.i32 = 30;
camera->commandCtrl(ma::Camera::CtrlType::kFps, ma::Camera::CtrlMode::kWrite, val);

camera->startStream(ma::Camera::StreamMode::kRefreshOnReturn);

// video thread: one retrieveFrame call returns one NAL (SPS/PPS/IDR/slice)
while (running) {
    ma_img_t frame;
    if (camera->retrieveFrame(frame, MA_PIXEL_FORMAT_H264) == MA_OK) {
        recamera_live::send_nal(frame.data, frame.size,
                                frame.index, frame.count,
                                frame.key, recamera_live::now_ms());
        camera->returnFrame(frame);
    }
}

// inference thread: do not call retrieveFrame directly (the queue is FIFO,
// a slow consumer would read frames from seconds ago).
// Use FrameRelay from live_camera.hpp to keep grabbing frames and retain only the latest:
#include "live_camera.hpp"

recamera_live::FrameRelay relay;
relay.start(camera, MA_PIXEL_FORMAT_RGB888);

recamera_live::LatestFrame f;
while (running) {
    if (!relay.wait(f, 1000)) continue;
    run_model(f.img.data, f.img.width, f.img.height);   // 640x640 inference
    camera->returnFrame(f.img);
    const int age_ms = f.age_ms + recamera_live::now_ms() - f.acquired_ms;
    recamera_live::send_meta(dets, n, recamera_live::now_ms(), -1,
                             infer_ms, fps, 0, nullptr, 0,
                             f.acquired_ms, age_ms);
}
```

Set CH0's fps to the rate inference can actually sustain (5–8 for the CPU decoder, 10–15 for the NPU). Setting it above the consumption rate does not make the model faster: the queue backs up — measured, a 15 fps channel with a 5 fps consumer pushed result-frame age to 2.9–3.2 s. The relay in `live_camera.hpp` guarantees "you get the latest frame"; `age_ms` feeds the UI's "result age" and the `live.perf` performance gate.

The 1 fps JPEG snapshot for evidence is optional, but `capture_evidence --source live` needs at least one. Note the JPEG channel must be enabled separately (`kChannel=1` + `kFormat=MA_PIXEL_FORMAT_JPEG`), otherwise `retrieveFrame` returns `MA_EPERM`:

```cpp
// JPEG thread: pull the JPEG channel separately; do not block the video thread
ma_img_t jpeg;
if (camera->retrieveFrame(jpeg, MA_PIXEL_FORMAT_JPEG) == MA_OK) {
    recamera_live::send_snapshot(jpeg.data, jpeg.size,
                                 jpeg.width, jpeg.height,
                                 recamera_live::now_ms());
    camera->returnFrame(jpeg);
}
```

## Lane marking / general annotations (polylines / polygons / points / text)

Beyond detection boxes, the UI also supports general annotations. The demo only computes the geometry; the UI draws it. Coordinates default to **frame-space normalized** (`space:"frame"`); if the geometry is computed in 640×640 model space, use `space:"model"` and the UI inverse-transforms it to 1080p according to `preprocess`.

```cpp
// lane markings: frame-space normalized coordinates, 4 decimal places, max 16 shapes / 80 points each
float left_pts[] = {0.12f, 0.92f, 0.38f, 0.55f, 0.46f, 0.42f};
float right_pts[] = {0.88f, 0.92f, 0.62f, 0.55f, 0.54f, 0.42f};
recamera_live::Shape lanes[2];
lanes[0] = {recamera_live::kPolyline, recamera_live::kFrameSpace,
            left_pts, 3, 0.0f, 0, "left", "#3ddc97", 5.0f, -1};
lanes[1] = {recamera_live::kPolyline, recamera_live::kFrameSpace,
            right_pts, 3, 0.0f, 1, "right", "#4cc2ff", 5.0f, -1};
recamera_live::send_meta(nullptr, 0, recamera_live::now_ms(), -1,
                         infer_ms, fps, 0, lanes, 2);

// ROI polygon (optional)
float roi_pts[] = {0.0f, 0.45f, 1.0f, 0.45f, 1.0f, 1.0f, 0.0f, 1.0f};
recamera_live::Shape roi = {recamera_live::kPolygon, recamera_live::kFrameSpace,
                            roi_pts, 4, 0.0f, -1, "ROI", "#ffc857", 2.0f, 1};
recamera_live::send_meta(nullptr, 0, recamera_live::now_ms(), -1,
                         infer_ms, fps, 0, &roi, 1);
```

`Shape` fields: `kind` (`kPolyline`/`kPolygon`/`kPoint`/`kText`), `space` (`kModelSpace`/`kFrameSpace`), `pts` (interleaved `x,y`, normalized), `n_pts`, `score`, `target`, `label`, `color`, `width`, `closed`. The module clips to 16 shapes / 80 points and serializes with 4 decimal places; the host also rejects meta larger than 8 KB. For hand-built JSON use `send_shapes_json(...)` (`send_annotations_json` is a compatibility alias); for fully custom meta use `send_meta_json(...)`.

> Lane detection itself is not part of this template: you need a model or classical CV to compute the lines. Example demos such as directional-arrow classification are not lane detection and must not be passed off as this demo. Once the real pipeline is implemented, set `live.implemented` in `demo.json` to `true`; only then does the UI hide the "demo skeleton" notice.

Key points:

- **Use hardware VENC for H.264**, about 4–8 Mbps at 1080p30; do not switch to a 1080p JPEG sequence — neither bandwidth nor CPU can handle it. `live.video.bitrate_kbps` is only a declared value; the actual bitrate is determined by the SDK's VENC configuration (`setupVideo` / `venc` parameters); lower it in the demo if necessary.
- `send_nal` merges multiple NALs into one access unit according to `frame.index` / `frame.count`; if you already have a complete access unit yourself, use `send_access_unit` directly. The module expects **Annex-B** (`00 00 00 01` start codes); if the VENC outputs length-prefixed NALs, convert to Annex-B first.
- `Config.video_width/height/fps` must match the actual H.264 channel (e.g. the 1920×1080@30 from `init(0)`); the module does not auto-detect.
- Detection coordinates stay as **normalized center points in the model input space** (`x,y,w,h` ∈ [0,1]). The host UI inverse-transforms them to 1080p according to `preprocess`; no box drawing on the device is needed. Common mappings:

  | SSCMA raw channel | `preprocess` | `scale` | `pad_x/pad_y` |
  |---|---|---|---|
  | full frame stretched to 640×640 | `stretch` | 1.0 | 0 / 0 |
  | 16:9 letterbox to 640×640 | `letterbox` | 0.3333 | 0 / 140 |
  | center crop 1080×1080 then downscale to 640 | `crop` | 0.5926 | — |

  If unsure, start with `stretch` and check whether the boxes align, then switch according to the actual preprocessing.
- Read parameter changes from `recamera_live::params()`; outside `send_nal` do not block the video thread.
- When `force_keyframe_requested()` is true, force an IDR on the next encode if possible; otherwise the host waits for the next natural keyframe.

## Parameters

| Parameter | Range | Default | Description |
|---|---|---|---|
| `confidence` | 0.05–0.95 | 0.60 | detection threshold |
| `nms` | 0.05–0.95 | 0.45 | NMS IoU |
| `max_det` | 1–1000 | 20 | maximum detection count |
| `force_idr` | 0/1 | — | request a keyframe |
| `lane_thr` | 0.05–0.95 | 0.55 | lane pixel/segmentation confidence |
| `roi_top` | 0.0–0.9 | 0.45 | lane ROI upper boundary (frame-normalized y) |
| `min_lane_px` | 20–1080 | 150 | drop lanes that are too short |
| `lane_ema` | 0.0–0.95 | 0.60 | cross-frame smoothing factor |
| `max_lane_pts` | 10–80 | 40 | max points sent per lane |
| `show_roi` | true/false | true | whether to send the ROI polygon |
| `show_lanes` | true/false | true | whether to send lane polylines |
| `show_boxes` | true/false | true | whether to send detection boxes |

The demo side only needs to read these atomic variables before inference; e.g.
`detector->setConfig(MA_MODEL_CFG_OPT_THRESHOLD, recamera_live::params().confidence.load())`.
Extra keys declared in `demo.json` (up to 16 simple identifiers) go into a bounded extras table, read with
`params().get_number("my_thr", &v)` / `params().get_bool("my_flag", &b)`;
these keys also appear in the hello/meta `params`, so the UI can restore the last values after a page refresh.

## Evidence

`serve_demo.py` writes the session to `eval/live/<timestamp>/`: `session.json`, `events.jsonl`, `logs/device.log`, `frames/*.jpg` (the device sends 1 fps snapshots on demand). The live UI itself is not evidence; use `capture_evidence.py --source live --session <dir>` to generate a standard `evidence.json`, then hand it to `evaluate_demo.py`.

## Platforms

- The device-side module depends only on POSIX socket/thread; the target is RISC-V musl, and the host smoke test runs on Linux/macOS. Linking needs `-lpthread -latomic` (CMake: `Threads::Threads` + `atomic`), otherwise `std::atomic<bool>` will miss `__atomic_exchange_1`.
- The host-side viewer `serve_demo.py` is pure Python standard library and runs on Windows/Linux/macOS; the browser player bundles jmuxer (MIT).
