# Capability Assessment and Preconditions

Before writing code or deploying, first judge: **can this demo actually be
done** on the current device and hardware, what preconditions it needs, and
what missing pieces should yield a `blocked` verdict rather than a failure.

## Assessment order

1. Device model and OS: use `probe_device.py` to confirm reCamera 2002,
   RISC-V, OS >= 0.2.4.
2. Whether the camera/NPU is occupied by the official services:
   `S03node-red`, `S91sscma-node`, `S93sscma-supervisor`.
3. Model and runtime: does the `.cvimodel` exist, is it compatible with
   cv181x, is ION memory sufficient?
4. Peripherals: are the gimbal, reSpeaker, Meshtastic, Stream Deck, XIAO,
   HQ PoE, Jetson, etc. actually connected?
5. Credentials/network: are Telegram, WeCom, n8n, HA, Grafana, iFlytek, etc.
   provided?
6. Evaluation inputs: can the model's original dataset or trustworthy
   open-source data be found?

The conclusion can only be:

- **Supported**: all preconditions confirmed.
- **Supported with preconditions**: the code path exists, but the
  device/model/hardware/credentials are unverified.
- **Blocked**: missing hardware, credentials, or network path — not a code
  failure.
- **Not supported through this skill**: reCamera Pro/RV1126B, pure Node-RED
  flows, solutions that require modifying firmware.

## Route table

| Goal | Feasibility | Route | Preconditions |
|---|---|---|---|
| Official wiki C++ vision demo | Supported with preconditions | `sscma-example-sg200x` + cross-compile + UDP/HTTP output | Camera services stopped, model files, receiver |
| Custom-model NPU demo | Supported with preconditions | TPU-MLIR → `.cvimodel` → C++ `EngineCVI` | Conversion environment, calibration data, ION budget, ONNX/CVI consistency verification |
| Online model export | Supported with preconditions | Web UI online export | Supported YOLO detection model, ONNX requirements |
| HTTP/JSON output demo | Supported | C++ service + `curl` verification | Port free, service reachable |
| UDP video demo | Supported with preconditions | Device streaming + official receiver | Receiver IP, port (commonly 5001), headless rendering approach |
| Line/lane-marking/ROI-type detection | Supported with preconditions | `meta.shapes` polylines/polygons + Live UI rendering | The demo computes and sends the geometry itself; no verified lane model/dataset — accuracy must be evaluated separately |
| Host-side live viewing (replacing the device Web UI) | Supported with preconditions | H.264 VENC + UDP + host `serve_demo.py` | Host/device network reachability, UDP 5000/5001, 1080p bitrate and CPU measured |
| RTSP/WebRTC | Supported with preconditions | Device RTSP + PC-to-WebRTC conversion | RTSP service/port, browser |
| Gimbal/gripper | Blocked without hardware | CAN/serial control | Gimbal hardware, CAN bitrate 100000, motion safety |
| reSpeaker/audio | Blocked without hardware | ALSA/official demo | Sound card, `/dev/snd` permissions, microphone/speaker |
| Meshtastic/LoRa | Blocked without hardware | Serial + mesh | Radio, network setup, serial permissions |
| Jetson VLM two-stage | Blocked without hardware | reCamera + Jetson Qwen3-VL | Jetson, model, WebSocket gateway |
| Telegram/WeCom/n8n/HA/Grafana | Blocked without credentials | Official integrations | Tokens/webhook/instance address |
| Node-RED built-in flows | Background reading | Node-RED UI/flow import | Not a runnable recipe unless the user explicitly asks for it |
| Modifying firmware/kernel/drivers | Not supported | — | Outside this skill's boundaries — use the firmware repository's process |

## Common "false successes"

- The process came up, but the camera is occupied by an official service, so no frames actually flow.
- The model loaded successfully, but the input layout/quantization mismatches and the output is all noise.
- The UDP receiver is not listening on the right port/IP, and this is misread as the device not streaming.
- The WebUI screenshot caught a login page or stale cache, not demo output.
- The baseline did not use the same batch of inputs, so the score is meaningless.
- A hardware demo only verified that the command returned 0, with no motion/audio evidence.
- Treating "the Live UI page opens" as the demo being done; with `LIVE_STATUS=placeholder` the page shows only logs.
- Claiming detections/lane markings are visible when `LIVE_STATUS=placeholder` / `degraded`, or passing detection boxes off as lane markings.

## Assessment template

```text
Goal:
Device/OS:
Verdict: Supported / Supported with preconditions / Blocked / Not supported
Preconditions:
- [ ] Device reachable (probe)
- [ ] Camera service arbitration
- [ ] Model/runtime
- [ ] Peripherals/credentials
- [ ] Fixed evaluation inputs
Unverified items:
Next step:
```

## Boundaries

- reCamera Pro / RV1126B / RKNN / App Center: use `procode` or `recamera-pysdk`.
- Do not modify firmware, Buildroot, drivers, or device identity.
- Do not stop `sshd`, networking, or dhcp; stopping `S93sscma-supervisor` takes the Web UI offline and must be restored.
- Do not write "missing hardware/missing credentials" up as a wiki error or a code error.
