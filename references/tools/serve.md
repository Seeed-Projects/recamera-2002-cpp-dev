# serve_demo.py — host-side live viewer

Shows the reCamera 2002 demo's video, detection results, and logs live in the **host browser**,
and lets you tune model parameters from the browser. The device needs no Web UI, Node-RED,
or Supervisor.

> The fixed UI covers "live view + model parameters + device log" only. Device operations such as
> install/start/stop go through the command line uniformly (`deploy_demo.py` / `launch_demo.py`) and no longer
> appear on the page; the backend `/api/actions` remains for script calls.

> For a "one command to deploy + get a URL", use `launch_demo.py` from [launch.md](launch.md).
> `serve_demo.py` only handles the host-side UI; device deployment is still done by
> `deploy_demo.py` or `launch_demo.py`.

## Why it exists

The SG2002 struggles to run the Web UI and a demo at the same time. This design puts the UI on a LAN host:

```text
reCamera 2002                         Host (Windows / Linux / macOS)
┌──────────────────┐   UDP 5000       ┌──────────────────────────────┐
│ demo (C++)       │ ───────────────> │ serve_demo.py                │
│  H.264 1080p30   │                  │  - UDP fragment reassembly   │
│  det JSON        │                  │  - session store / evidence  │
│  log / snapshot  │ <─────────────── │  - control-plane forwarding  │
│  params 5001     │   UDP 5001       │  - HTTP / SSE / chunked video│
└──────────────────┘                  └──────────────┬───────────────┘
                                                     │ browser
                                                     ▼
                                              ┌────────────────┐
                                              │ Fixed UI       │
                                              │ video+box+tune │
                                              └────────────────┘
```

Video uses **H.264 (hardware VENC)**, not a JPEG sequence: 1080p30 H.264 is about 4–8 Mbps;
1080p30 JPEG would be about 36–72 Mbps, more than the SG2002 or the host can handle. Detection runs on 640×640, and
the browser overlays the coordinates on the 1080p frame via the inverse of `preprocess`.

## Starting it

```bash
# Local access (default 127.0.0.1:8090)
python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/my_demo \
  --device-host 192.168.42.1 --open

# LAN access: a token is required
python3 scripts/serve_demo.py --bind 0.0.0.0 --token 'change-me' \
  --demo-dir ~/reCamera_demo/my_demo --device-host 192.168.42.1 --open

# Let /api/actions execute install/start/stop/verify/capture (needs ssh on PATH)
python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/my_demo \
  --device-host 192.168.42.1 --allow-actions --open
```

`/api/actions` executes through `deploy_demo.py`; the password still goes through the `RECAMERA_PASSWORD` environment
variable (or a configured SSH key) and never appears in argv, reports, or the page. The fixed UI no longer renders those buttons.

Pass the host IP when starting the demo on the device:

```bash
# On the device (run by deploy_demo.py start, or manually)
./my_demo --peer 192.168.42.2 --port 5000 --control-port 5001
```

`--peer` is the address of the host running `serve_demo.py`. Over a USB direct connection it is usually
the host's address on the `192.168.42.x` subnet; on a LAN use the host's LAN IP. The device's hello/state
is resent every 10 seconds, so starting the device first and the host-side viewer afterwards still connects.

When "Start" is triggered from `/api/actions`, `serve_demo.py` picks the local NIC address matching `--device-host`,
and lets `deploy_demo.py` auto-inject `--peer` via the `LIVE_PEER` environment variable — no manual edit of
`demo.json` needed. When running `deploy_demo.py start` manually you can also
`export LIVE_PEER=<host-ip>` yourself.

## Parameters

```text
--demo-dir DIR        demo.json's live.params decides the UI controls; also the workspace for actions
--device-host IP      target device for parameter control and deploy actions
--bind ADDR           HTTP bind address, default 127.0.0.1
--http-port N         default 8090
--udp-bind ADDR       UDP bind address, default 0.0.0.0
--udp-port N          default 5000
--control-port N      default 5001
--token TOKEN         recommended/auto-generated for non-local binds
--allow-actions       allow /api/actions to call deploy_demo.py (the fixed UI exposes no buttons)
--session-dir DIR     default <demo-dir>/eval/live/<timestamp>
--record-video        additionally save the raw H.264 stream (~1 MB/s)
--ui-dir DIR          override the static UI during development
--open                open the default browser
```

## HTTP/SSE endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Fixed UI |
| `/api/health` | GET | health check (no token needed) |
| `/api/state` | GET | snapshot of device, video, parameters, logs |
| `/api/params` | GET/POST | read/push parameters (validated against `live.params`) |
| `/api/events` | GET | SSE: `state` / `meta` / `hello` / `log` / `params` / `action` |
| `/api/video` | GET | chunked binary: `[u32 len][u64 ts][Annex-B]` |
| `/api/actions` | POST | `{"action":"install|start|stop|restart|verify|collect|capture"}` |
| `/api/mark` | POST | write a marker to the session |
| `/api/keyframe` | POST | request the device force an IDR (`force_idr=1`) |
| `/api/session` | GET | current session directory |
| `/api/shutdown` | POST | stop the service |

For non-local binds, all `/api/*` (except `/api/health`) require
`?token=...` or the `X-Live-Token` header. The UI reads the token from the URL and attaches it automatically.
`POST` must carry `Content-Type: application/json`; cross-origin `Origin` / non-IP
`Host` (DNS rebinding) is rejected. With `--allow-actions` enabled a token is mandatory.

The UDP data plane has no authentication: any LAN device that can reach the host can inject video/detections/logs.
Use only on trusted LANs; do not expose the UDP ports directly to the public internet.

## Device protocol

The full definition and Python implementation are in `scripts/devkit/live.py`; the C++ implementation is in
`templates/live/src/live_udp.cpp`.

32-byte header (little-endian): magic `RCV1`, version, message type, flags, seq,
ts_ms, width, height, frag_idx, frag_count, payload_len, reserved.

| Type | Content |
|---|---|
| `video` | H.264 Annex-B access unit (SPS/PPS/IDR/slices) |
| `meta` | detection JSON (normalized center coordinates, frame/model sizes, preprocess, elapsed time, `frame_ts_ms`, `age_ms`) |
| `hello` | device/model/parameters/labels, resent every 10 seconds |
| `log` | structured log `{"level","msg"}` |
| `snapshot` | JPEG snapshot (for evidence, default 1 fps) |
| `stats` | send fps, bitrate, frame count |

`meta` may also carry `shapes` (`annotations` is a compatibility alias): `kind` is
`polyline` / `polygon` / `point` / `text`, `pts` is an array of normalized `[x,y]`,
and `space` is `frame` (frame space) or `model` (model space; the UI applies the inverse `preprocess`).
Lane marking detection should send polylines, not boxes. On the device use
`recamera_live::Shape` + `send_meta(..., shapes, n)`, or
`send_shapes_json(...)`. Budgets: meta ≤ 8 KB, ≤ 16 items, ≤ 80 points each;
both the host and the UI truncate.

The control plane is ASCII lines:

```text
RCC1 SET seq=12 confidence=0.60 nms=0.45 max_det=20
RCC1 ACK seq=12 ok=1 applied=confidence,nms
```

`force_idr=1` requests a keyframe; the demo consumes it via
`recamera_live::force_keyframe_requested()`.

Common parameters: `confidence`, `nms`, `max_det`, `force_idr`; lane demos can also use
`lane_thr` (0.05–0.95), `roi_top` (0.0–0.9), `min_lane_px` (20–1080),
`lane_ema` (0.0–0.95), `max_lane_pts` (10–80), `show_roi`,
`show_lanes`, `show_boxes`. UI controls are generated automatically from `demo.json`'s `live.params`,
and the demo reads them from `recamera_live::params()`; custom keys declared in `demo.json`
also enter the device's bounded extras table (≤16), read with `params().get_number("key", &v)` /
`params().get_bool("key", &b)`. Known keys (confidence/nms/max_det/
force_idr/lane_* etc.) still use strongly-typed atomics.

## Performance gate: result age and update rate

The UI's "result age" comes from the device's `meta.age_ms` (frame capture → result sent), not host clock minus device
clock — the latter measures clock skew and cannot stand in for latency. The device must use `FrameRelay` from
`live_camera.hpp` to fetch inference frames and fill `age_ms` into `send_meta`.

`serve_demo.py` aggregates over a trailing 10 s window:

- `pipeline.result_age_ms`: median of `age_ms`
- `pipeline.result_age_p90_ms`
- `pipeline.detect_fps`: meta arrival intervals
- `pipeline.video_fps`: device `stats.fps`

`demo.json`'s `live.perf` defines the thresholds (disabled when absent):

```json
"perf": {"max_result_age_ms": 500, "min_detect_fps": 1, "min_video_fps": 15}
```

With fewer than 10 samples `pipeline.ok` is `null` (`unmeasured`), launch reports
`LIVE_PERF=unmeasured`, which must not be treated as passing. When thresholds are exceeded, `launch_demo.py`'s
`LIVE_STATUS` becomes `degraded` and it prints `LIVE_WARNING=perf ...`.

## Sessions and evidence

Each run writes:

```text
<demo-dir>/eval/live/<timestamp>/
├── session.json      # device, video, parameters, frame hashes, log hashes
├── events.jsonl      # hello/meta(1Hz)/log/snapshot/params/action
├── logs/device.log   # device log
├── frames/*.jpg      # device JPEG snapshots
└── video/stream.h264 # only with --record-video
```

The live UI is **not evidence**. Use:

```bash
python3 scripts/capture_evidence.py --source live \
  --session ~/reCamera_demo/my_demo/eval/live/<timestamp> \
  --out ~/reCamera_demo/my_demo/eval/recamera/evidence
```

`capture_evidence.py` requires at least one device snapshot, a device log, the event stream, and
`session.json`, otherwise it fails. The quality gate still only trusts `evaluate_demo.py`.

## Cross-platform

- `serve_demo.py` uses only the Python standard library; it runs on Windows 10/11, macOS, and Linux.
- The player is the bundled jmuxer (MIT); no CDN or npm dependency.
- The browser needs MSE + H.264 support (Chrome/Edge/Safari/Firefox all work).
- On first run Windows may prompt the firewall; allow Python inbound UDP 5000 and TCP 8090.
- The backend's deployment actions (`/api/actions`) need `ssh` on PATH (Windows optional feature "OpenSSH Client").
  Without ssh, live viewing and parameter tuning still work; only the actions endpoint errors out.
- The device-side module uses POSIX sockets/threads, targeting RISC-V musl; the host smoke test runs on
  Linux/macOS.

## Troubleshooting

| Symptom | Check |
|---|---|
| Page loads but video is stuck on "waiting for keyframe" | Verify the device's `--peer` points at the host, the host firewall allows UDP 5000, and the demo really sends H.264 |
| Video but no detection boxes | Check whether `meta` arrives; whether `preprocess/frame_w/frame_h` match the demo |
| Results visibly lag the picture | Look at the UI's "result age" and `pipeline.result_age_ms`; above 500 ms, first check whether the inference thread calls `retrieveFrame` directly (queue depth = fps, FIFO takes the oldest), switch to `FrameRelay`, and lower CH0 fps to the consume rate |
| Parameter tuning keeps saying "push failed" | Is the device control port 5001 occupied; is `--device-host` correct; is an ack returned |
| Dropped frames / corrupted video | Lower bitrate/resolution; prefer wired or USB direct; check `fragment_drops` |
| `/api/actions` returns 403 | Add `--allow-actions` at startup; check `--demo-dir`, `--device-host` |
| ssh not found on Windows | Install the OpenSSH client, or use only viewing/parameter tuning |
| Session has no frames | The demo never sends `snapshot`; evidence capture will fail (this is intentional) |
