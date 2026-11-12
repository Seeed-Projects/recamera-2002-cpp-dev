# reCamera Overview and Device Operations

This file consolidates the operating procedures for reCamera access, service management, the C++ build and deployment baseline, and receiver-side recording.

## Wiki scope

This skill is distilled from the official reCamera wiki: it keeps only operational essentials, path indexes and original summaries, does not copy wiki body text, and does not distribute the raw wiki markdown with the skill. When you encounter a new wiki page, read that page directly and then apply this skill's workflow.

Source and license: see `NOTICE` at the skill root. The upstream wiki repository is GPL-3.0 while this skill's code is Apache-2.0; the skill does not package upstream document text, and page paths serve only as references. If you want to redistribute the content of this file, first confirm the upstream license, or keep only factual information such as paths and commands.

Upstream addresses:

- Wiki rendered site: `https://wiki.seeedstudio.com`
- Wiki source repository: `https://github.com/Seeed-Studio/wiki-documents`, branch `docusaurus-version`, reCamera directory `sites/en/docs/Edge/reCamera/`
- Demo examples repository (C++ solutions source): `https://github.com/Seeed-Studio/sscma-example-sg200x` (default branch `main`)

Node-RED-style demos are not treated as runnable recipes for now; they are background reading only when the user explicitly asks.

### Wiki original page mapping

Snapshot date: 2026-09-18, verified against the `docusaurus-version` branch. Upstream reorganization in 2026-08/09: the former top-level `reCamera_2002_Series/`, `reCamera_HQ_POE/`, `reCamera_Gimbal/`, `software_documents/` and `ai_model_deployment/` have been merged into `reCamera_Basic/`, and `guides/` and `modules/` were added. `reCamera_Pro/` (the RKNN/RV1126B line, covered by the procode skill) is out of scope for this skill and is not listed in this table. Before running a key demo, follow step 1 of the workflow and use the GitHub API to check whether the page has new commits.

The table below is only a **page index**: the second column is the upstream path, the third column is this skill's original one-line summary; it does not copy wiki body text. The full page URL is
`https://github.com/Seeed-Studio/wiki-documents/blob/docusaurus-version/sites/en/docs/Edge/reCamera/<path>`.

| Area | Wiki | Distilled Use |
|---|---|---|
| Quick start | `reCamera_Basic/getting_start/reCamera_2002_series/quick_start_guide.md` | Networking, Web UI routes, USB/AP/Ethernet modes |
| Warranty | `reCamera_Basic/getting_start/reCamera_2002_series/reCamera_warranty.md` | Warranty/reference, not a runnable demo |
| Hardware/spec | `reCamera_Basic/hardware_and_specs/reCamera_2002_series.md`, `reCamera_gimbal.md`, `reCamera_hq_poe.md` | Device and accessory spec constraints |
| HQ PoE | `reCamera_Basic/getting_start/reCamera_hq_poe/reCamera_hq_poe_start.md`, `reCamera_hq_poe_microscope_demo.md` | Requires HQ PoE hardware; otherwise hardware-blocked |
| Gimbal | `reCamera_Basic/getting_start/reCamera_gimbal/reCamera_gimbal.md`, `gimbal_development_c.md`, `gimbal_node_red.md`, `PID_adjustment.md` | Gimbal getting started, CAN development (bitrate 100000), Node-RED flows, PID tuning; requires gimbal hardware, verify with motion evidence |
| Wi-Fi HaLow | `modules/wifi_halow/wifi_halow_qucik_start.md` | HaLow module unboxing/assembly, hardware interfaces, quick start (the `qucik` spelling in the filename is upstream; do not change it) |
| ONVIF | `reCamera_Basic/guides/getting_started_onvif.md` | Deploying ONVIF on reCamera |
| OS structure | `reCamera_Basic/software_documents/os_structure.md` | Buildroot, supervisor, Node-RED/SSCMA services |
| Linux basics | `reCamera_Basic/software_documents/linux_fundamentals.md` | Device-side shell basics |
| Static IP | `reCamera_Basic/software_documents/configure_static_ip_on_recamera.md` | Network configuration; high risk: back up the config, keep an SSH recovery path, apply only on request |
| OS version control | `reCamera_Basic/software_documents/os_version_control.md` | OTA/manual updates; high risk: do not upgrade unless requested |
| C/C++ dev | `reCamera_Basic/software_documents/develop_with_c_cpp.md` | Toolchain, SDK paths, build/deploy/run baseline (see "C++ build and deployment baseline" in this file) |
| C++ YOLO HTTP | `reCamera_Basic/software_documents/Real_time_YOLO_object_detection_using_reCamera_based_on_Cpp.md` | Build/deploy the model detector, verify `http://192.168.42.1/modeldetector` JSON |
| Auto start | `reCamera_Basic/software_documents/Make_the_Cpp_program_auto_start_on_boot.md` | SysVinit auto-start; only when the demo is stable and the user asks for it |
| Node-RED dev | `reCamera_Basic/software_documents/develop_with_nodered.md` | Node-RED UI, flow import/export, service restarts (background reading) |
| XIAO HTTP | `reCamera_Basic/software_documents/reCamera_connects_to_Xiao_via_HTTP.md` | HTTP integration with XIAO; requires XIAO hardware |
| On-device models | `reCamera_Basic/ai_model_deployment/on_device_model.md` | Official `.cvimodel` download links and category table |
| Model conversion | `reCamera_Basic/ai_model_deployment/model_conversion_guide.md`, `Custom_model_conversion.md`, `model_export_cvimodel_online.md` | YOLO/ONNX→CVI conversion planning (methodology in `model-conversion.md`); `model_export_cvimodel_online.md` covers exporting custom YOLO detection models online to cvimodel via the Web UI |
| FAQ | `reCamera_Basic/faqs/faqs.md` | Troubleshooting/reference; verify against the real device when relevant |
| Demo (examples) | `reCamera_Basic/application/UDP_Face_Analysis.md`, `yolo_benchmark.md` | Unofficial examples measured on device: see `examples/community-records.md` |
| Demo (voice/VLM) | `reCamera_Basic/application/AI_Voice_Assistant.md` | Pose-recognition-triggered STT→LLM→TTS voice conversation (iFlytek Spark) |
| Demo (voice/VLM) | `reCamera_Basic/application/recamera_jetson_vl.md` | reCamera YOLOv8 person detection + Jetson Qwen3-VL recognizing red-clothed people, WebSocket event gateway (two stages) |
| Demo (streaming) | `reCamera_Basic/application/RTSP_to_WebRTC.md` | Push RTSP from reCamera to a PC, convert to WebRTC, low-latency playback in the browser |
| Demo (vision/NPU) | `reCamera_Basic/application/deploy_monocular_depth_fastdepth.md` | FastDepth monocular depth INT8 (TPU-MLIR conversion), about 54 FPS on device in parallel with YOLO11n |
| Demo (vision/NPU) | `reCamera_Basic/application/hand_gesture.md` | MediaPipe hand suite ported to SG200X: palm detection→keypoints→embedding→classification, UDP stream visualization |
| Demo (vision/NPU) | `reCamera_Basic/application/qrcode_rec.md` | Real-time QR code recognition, UDP streaming of frames + results to a PC |
| Demo (vision/NPU) | `reCamera_Basic/application/reCamera_Motion_Detection.md` | Region-based motion detection (2002 series), includes a Windows rendering interface |
| Demo (vision/NPU) | `reCamera_Basic/application/Use_the_body-sensing_function.md` | Body sensing; first determine whether it uses the built-in Node-RED model flow or the C++ path |
| Demo (peripherals/gimbal) | `reCamera_Basic/application/OpenClaw_reCamera-Gimbal.md` | OpenClaw gimbal control; requires gimbal/gripper hardware, motion evidence and safety |
| Demo (peripherals/gimbal) | `reCamera_Basic/application/picoclaw_gimbal.md` | Picoclaw deployed locally on reCamera |
| Demo (peripherals/gimbal) | `reCamera_Basic/application/Using_Stream_Deck_to_control_reCamera_Gimbal.md` | Stream Deck controls the gimbal; requires Stream Deck and gimbal hardware |
| Demo (peripherals/audio) | `reCamera_Basic/application/reCamera_reSpeaker.md` | reSpeaker integration; requires reSpeaker hardware, audio device detection |
| Demo (platform integration) | `reCamera_Basic/application/Getting_started_in_Telegram.md`, `Getting_started_in_Wechat_work.md`, `Getting_started_for_n8n.md`, `Getting_started_for_Home_Assistant.md`, `Integration_of_real-time_heat_map_with_Grafana_data_dashboard.md` | Telegram/WeCom (WeChat Work)/n8n/HA/Grafana integrations; missing credentials = blocked, not a wiki failure |
| Demo (Meshtastic/remote) | `reCamera_Basic/application/AI_Human_Detection_Meshtastic_Broadcast.md`, `recamera_meshtastic_ha_security_system.md`, `AI_Remote_Wireless_Monitor_System.md` | Person detection + LoRa broadcast / HA security system, remote monitoring workflow; requires Meshtastic radios or cloud/remote prerequisites |
| Demo (application workflow) | `reCamera_Basic/application/Car_parking_management.md` | Parking detection application; model assets, camera scene, detection results and output channels |

## Device access

- USB/direct-connect IP: `192.168.42.1`
- Web UI: `http://192.168.42.1`, key routes `/#/init`, `/#/workspace`, `/#/network`, `/#/security`, `/#/terminal`, `/#/system`, `/#/power`
- Raw Node-RED UI: `http://192.168.42.1:1880`
- SSH: user `recamera`; read the password only from an environment variable and never put it in scripts, argv or documentation. The skill's scripts default to OpenSSH `SSH_ASKPASS` (the password lives only in the child process environment) and also support `sshpass -e` (`DEMO_SSH_AUTH=sshpass`).

### SSH to reCamera

```bash
export RECAMERA_PASSWORD='<device password>'
# automation (default SSH_ASKPASS; the password never reaches argv)
python3 scripts/probe_device.py --host 192.168.42.1 --report dist/probe.json

# manual login (enter the password interactively)
ssh -o StrictHostKeyChecking=accept-new recamera@192.168.42.1 'hostname; whoami'
```

Or simply `ssh recamera@192.168.42.1` (enter the password interactively). For automation, prefer `--password-env` in `scripts/probe_device.py` / `deploy_demo.py`.
For `sshpass` compatibility mode, set `DEMO_SSH_AUTH=sshpass`.

### Copying files to reCamera

```bash
scp -o StrictHostKeyChecking=accept-new \
  local-file recamera@192.168.42.1:/home/recamera/
```

### UDP receiver IP

The reCamera UDP demos take a receiver-host IP as a parameter. Find the IP on the receiver host and pass it to the reCamera. If the receiver host and the reCamera are on the same USB network (`192.168.42.0/24`), use the receiver's IP on that subnet. The IP can change after a reCamera reboot or USB reconnect, so read it again before starting the sender.

If the receiver host is connected directly to the reCamera over USB, find this machine's IP on the `192.168.42.0/24` subnet:

```bash
ip -4 -brief addr | awk '/192[.]168[.]42[.]/{sub(/\/.*$/, "", $3); print $3; exit}'
```

### Tunnel and screenshots

When you need screenshots of the reCamera Web UI, use SSH local forwarding:

```bash
ssh -N -L 18080:192.168.42.1:80 recamera@192.168.42.1
```

Then open `http://127.0.0.1:18080` in a browser or Playwright, and save screenshots to `/tmp/recamera-*`.

## OS and services

The reCamera OS is based on Buildroot and uses SysVinit scripts under `/etc/init.d`. Common services:

- `S03node-red`: Node-RED
- `S91sscma-node`: camera/model service used by Node-RED
- `S93sscma-supervisor`: supervisor and web/backend services
- `S98ttyd`: web terminal
- `S50sshd`: SSH

C++ camera demos usually need `S03node-red`, `S91sscma-node` and `S93sscma-supervisor` stopped so the camera is not occupied:

```bash
ssh recamera@192.168.42.1 \
  'sudo /etc/init.d/S03node-red stop 2>/dev/null || true; \
    sudo /etc/init.d/S91sscma-node stop 2>/dev/null || true; \
    sudo /etc/init.d/S93sscma-supervisor stop 2>/dev/null || true'
```

When `sudo` asks for a password, use your own reCamera password (`RECAMERA_PASSWORD`). Do not delete init scripts unless explicitly asked.

## C++ build and deployment baseline

The reCamera has no local C/C++ build environment; cross-compile on a Linux host and copy the executable to the device. The demo sources live in a local repository (e.g. `sscma-example-sg200x`); the exact directory follows the user's local repository, and `<demo source dir>` in the commands below refers to that demo's source directory.

The default workspace for a new demo is `~/reCamera_demo/<demo_name>` (changeable via `new_demo.py --out` / `--demo-dir` in the scripts); this is a tool convention, not a fixed layout on any particular host.

Pass the SDK and toolchain locations via environment variables (`$SDK_ROOT` = SG200X SDK, `$TOOLCHAIN_BIN` = bin directory of the RISC-V musl cross toolchain); do not hardcode absolute paths:

```bash
export SG200X_SDK_PATH=$SDK_ROOT
export PATH=$TOOLCHAIN_BIN:$PATH
```

Build:

```bash
cd <demo source dir>
rm -rf build && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-std=c++17" ..
make -j$(nproc)
```

Verify that the artifact's architecture is RISC-V/musl rather than x86_64:

```bash
file <executable>
```

### Deployment mode

This is the manual wiki-baseline procedure. The scripts in this toolkit install demos under `/userdata/demos/<demo>/` (see `configs/device-contract.json`); `/home/recamera/<demo>/` is only a comparison location for upstream wiki examples.

1. Create `/home/recamera/<demo>` on the reCamera.
2. Copy the executable, models and scripts (scp: see above).
3. `chmod +x` the executable.
4. Stop the services that occupy the camera (see above).
5. Run with an explicit UDP target or HTTP port, always with `sudo` and the runtime library path:

```bash
sudo env LD_LIBRARY_PATH=/mnt/system/lib:/mnt/system/usr/lib:/mnt/system/usr/lib/3rd:$LD_LIBRARY_PATH \
  ./<binary> <args>
```

6. Collect logs and evidence.
7. Restart the services after testing unless the user asks the demo to stay resident.

### Recovery by reboot

After aggressive process cleanup, recover by rebooting:

```bash
ssh recamera@192.168.42.1 'sudo reboot'
```

After the reboot, verify the device is ready:

```bash
curl -s --max-time 5 http://192.168.42.1/api/version
```

## Receiver-side recording

For quality gates and evidence collection, prefer the official wiki/repository receiver scripts over hand-written renderers. Record on the receiver host with Xvfb + ffmpeg x11grab.

Start the virtual display:

```bash
nohup Xvfb :99 -screen 0 1280x720x24 >/tmp/recamera_xvfb.log 2>&1 & echo $! >/tmp/recamera_xvfb.pid
```

Run the official receiver:

```bash
cd <directory containing the receiver script> && DISPLAY=:99 python3 -u ./udp_receiver.py
```

First check the actual window size/position with `xwininfo -root -tree` (with `DISPLAY=:99`), then record that region:

```bash
DISPLAY=:99 xwininfo -root -tree
ffmpeg -y -f x11grab -draw_mouse 0 -video_size 640x700 -framerate 24 -i :99.0+0,0 -t 30 out.mp4
```

For the OpenCV Qt windows tested here, `640x700+0+0` works. When visual evidence is needed, provide the MP4 path and one representative frame.

## Host-side live viewer (recommended; avoids the device Web UI)

When the SG2002 struggles to run the Web UI and the demo at the same time, use a fixed UI on the host. One command:

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host 192.168.42.1 --bind 0.0.0.0 --allow-actions --allow-service-stop
# LIVE_URL=http://<host-ip>:8090/?token=...
```

Or start the pieces separately:

```bash
python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --device-host 192.168.42.1 --open
```

- On the device, the C++ module generated with `new_demo.py --live udp` pushes **H.264 1080p30**
  (hardware VENC, about 4–8 Mbps) plus 640×640 detection JSON; do not push a 1080p JPEG sequence anymore.
- On the host, `serve_demo.py` receives the UDP stream with the standard library and serves the browser UI (jmuxer built in);
  video/logs/parameters all live host-side, and the device only runs the demo itself.
- At deployment, `runtime.services_stop` declares stopping `S03node-red`, `S91sscma-node` and
  `S93sscma-supervisor`, and pass `--allow-service-stop` explicitly; services are restored by default after being stopped.
- Live video is not evidence; use `capture_evidence.py --source live --session <dir>`
  to turn device snapshots/logs/event streams into standard evidence.

## Model knowledge

The reCamera uses Sophgo CV181x/SG200X `.cvimodel` files. Model downloads provided by the official wiki:

- `yolo11n_cv181x_int8.cvimodel`: `https://files.seeedstudio.com/wiki/reCamera/models/yolo11n_cv181x_int8.cvimodel`
- `yolov8n_cv181x_int8.cvimodel`
- `person_cv181x_int8.cvimodel`
- `gender_cv181x_int8.cvimodel`
- `gesture_cv181x_int8.cvimodel`
- `digital_meter_cv181x_int8.cvimodel`
- `yolo11n_drone_int8_sym.cvimodel`

## Evidence expectations

When asked to "run" a demo, collect evidence according to the demo type:

- HTTP demo: response body + server logs
- UDP video demo: PNG/JPG frames saved by the receiver + sender statistics
- Web UI demo: screenshots through a local tunnel (see above)
- Hardware demo: command logs + hardware/credential blocking notes (if any)
