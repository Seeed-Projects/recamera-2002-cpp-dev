# reCamera Capabilities and Demo Experience Records

This file ships with the skill and contains the **capability catalog** (what each capability does and how to verify it) and the **record format**.
The capability catalog is original summary material, used only to judge "can this device do it, what hardware is needed, how to verify";
it does not copy wiki body text. For upstream details, defer to the wiki pages (see "Wiki scope" in `overview.md`).

Personal/community records measured on device do not go into the installed skill directory: `record_demo.py` writes by default to the user data
directory `~/.local/share/recamera-2002-cpp-dev/demo-records.md`, so a skill upgrade never overwrites or conflicts with them.
`examples/community-records.md` contains a few public example records (not an official baseline).

## Usage

- **Before starting**: search this file's capability catalog by demo name, model or task type to see whether a similar capability exists;
  consult the record file under the user data directory when you need historical experience.
- **After a run**: append one entry with `record_demo.py` following the record format (workflow step 13; when step 9 judges the run failed,
  append it as well with status ❌). Writes default to the user data directory; `--records` can specify a path explicitly.

## Record format

```markdown
### <demo_name> (<YYYY-MM-DD>)
- Status: ✅ success / ❌ failed
- Capability: <one line>
- Model files: <files or "none">
- Dependencies: <deps or "none">
- Build issues: <issue and fix, or "none">
- Verification: <actual observed result>
- Lessons: <reusable conclusion or "none">
- Wiki issues: <optional>
```

## Demos run so far

The version distributed with the skill contains no personal records measured on device. See
`examples/community-records.md` for example entries (marked unofficial); your own records are written by default to
`~/.local/share/recamera-2002-cpp-dev/demo-records.md`.

## Capability catalog (capabilities and verification guidance)

Snapshot date: 2026-09-18 (verified against the `docusaurus-version` branch); entry paths are relative to `sites/en/docs/Edge/reCamera/`. Upstream pages may change; before running a key demo, follow step 1 of the workflow and use the GitHub API to check that page for new commits. `reCamera_Pro/` (the RKNN/RV1126B line, see the procode skill) is out of scope for this skill and is not cataloged. Each entry is only an original one-line capability/verification summary and does not retell wiki body text; follow the upstream page for concrete steps.

### Application demos

`reCamera_Basic/application/AI_Human_Detection_Meshtastic_Broadcast.md`
- Capability: person detection plus Meshtastic broadcast and UDP preview.
- Verification: do local vision/UDP first; Meshtastic needs radio hardware and serial configuration. Score vision and radio integration separately.

`reCamera_Basic/application/AI_Remote_Wireless_Monitor_System.md`
- Capability: remote monitoring workflow.
- Verification: network path, cloud/remote prerequisites, camera stream and credential clarity.

`reCamera_Basic/application/AI_Voice_Assistant.md`
- Capability: pose-recognition-triggered STT→LLM→TTS voice conversation (iFlytek Spark LLM).
- Verification: audio input/output hardware, iFlytek credentials, evidence of the full conversation chain. Missing credentials = blocked.

`reCamera_Basic/application/Car_parking_management.md`
- Capability: parking detection / application workflow.
- Verification: required models/assets, camera scene, detection results and output channels.

`reCamera_Basic/application/Getting_started_for_Home_Assistant.md`
- Capability: Home Assistant integration.
- Verification: HA instance/version, endpoints, tokens, imported entities, camera/event visibility. Missing HA credentials means `blocked`, not a direct wiki failure.

`reCamera_Basic/application/Getting_started_for_n8n.md`
- Capability: n8n workflow integration.
- Verification: n8n account/self-hosted instance, webhook, workflow import, test events.

`reCamera_Basic/application/Getting_started_in_Telegram.md`
- Capability: Telegram bot integration.
- Verification: bot token/chat ID and message/image delivery. Missing credentials means `blocked`.

`reCamera_Basic/application/Getting_started_in_Wechat_work.md`
- Capability: WeCom (WeChat Work) integration.
- Verification: enterprise app credentials/webhook and message delivery. Missing credentials means `blocked`.

`reCamera_Basic/application/Integration_of_real-time_heat_map_with_Grafana_data_dashboard.md`
- Capability: Grafana dashboard integration.
- Verification: data source, dashboard import, real-time data feed and heatmap updates.

`reCamera_Basic/application/OpenClaw_reCamera-Gimbal.md`
- Capability: reCamera Gimbal plus OpenClaw control.
- Verification: gimbal/gripper hardware, command path, motion evidence and safety.

`reCamera_Basic/application/RTSP_to_WebRTC.md`
- Capability: push the reCamera RTSP stream to a PC, convert to WebRTC PC-side, low-latency real-time playback in the browser.
- Verification: RTSP stream pullable, WebRTC conversion chain, browser-side latency and picture.

`reCamera_Basic/application/Use_the_body-sensing_function.md`
- Capability: body sensing function.
- Verification: determine whether it is the built-in Node-RED/model flow or the C++ path; verify event output and UI/stream evidence.

`reCamera_Basic/application/Using_Stream_Deck_to_control_reCamera_Gimbal.md`
- Capability: Stream Deck controls the gimbal.
- Verification: Stream Deck hardware/software, gimbal hardware, action mapping and motion evidence.

`reCamera_Basic/application/deploy_monocular_depth_fastdepth.md`
- Capability: FastDepth monocular depth estimation INT8 deployment (TPU-MLIR conversion), about 54 FPS in parallel with YOLO11n.
- Verification: ONNX→cvimodel conversion, depth-map output quality, FPS claim. See `model-conversion.md` for methodology.

`reCamera_Basic/application/hand_gesture.md`
- Capability: full MediaPipe hand-suite port (palm detection→keypoints→embedding→classification), UDP-streamed to a PC for visualization.
- Verification: all model files for the multi-stage pipeline present, UDP receiver, gesture classification accuracy.

`reCamera_Basic/application/picoclaw_gimbal.md`
- Capability: Picoclaw local deployment on reCamera.
- Verification: requires Picoclaw hardware; deployment flow, motion/gripper evidence. Missing hardware = blocked.

`reCamera_Basic/application/qrcode_rec.md`
- Capability: real-time QR code recognition, UDP streaming of video frames and recognition results to a PC.
- Verification: receiver display, recognition rate across different QR code samples.

`reCamera_Basic/application/reCamera_Motion_Detection.md`
- Capability: region-based motion detection on reCamera 2002.
- Verification: region configuration, motion event triggering; the rendering interface depends on Windows, so a Linux receiver needs an alternative or should be marked blocked.

`reCamera_Basic/application/reCamera_reSpeaker.md`
- Capability: reSpeaker integration.
- Verification: reSpeaker hardware, audio device detection, command/audio path.

`reCamera_Basic/application/recamera_jetson_vl.md`
- Capability: reCamera YOLOv8 person detection + NVIDIA Jetson Qwen3-VL recognizing red-clothed people, publishing WebSocket events (two-stage vision application).
- Verification: requires Jetson hardware and a Qwen3-VL environment; event stream and recognition evidence. Missing hardware = blocked.

`reCamera_Basic/application/recamera_meshtastic_ha_security_system.md`
- Capability: reCamera + Meshtastic LoRa mesh, a remote security system that wirelessly pushes AI detection alerts to Home Assistant.
- Verification: Meshtastic radio networking, HA instance/credentials; verify vision and radio links separately. Missing hardware/credentials = blocked.

### AI model deployment

`reCamera_Basic/ai_model_deployment/Custom_model_conversion.md`
- Capability: convert custom models to `.cvimodel`.
- Verification: host tools, model format, input size, calibration data, conversion commands, final deployment and inference on reCamera. Do not claim the conversion was replicated unless it actually ran.
- See `model-conversion.md` for methodology.

`reCamera_Basic/ai_model_deployment/model_conversion_guide.md`
- Capability: general model conversion reference.
- Verification: at least one complete conversion path before scoring it runnable. Check that the calibration, quantization and deployment steps are exact.
- See `model-conversion.md` for methodology.

`reCamera_Basic/ai_model_deployment/model_export_cvimodel_online.md`
- Capability: convert/export custom YOLO detection models to `.cvimodel` online via the reCamera Web UI.
- Verification: ONNX preparation requirements, supported scope, Web UI export flow, exported model loadable for inference.

`reCamera_Basic/ai_model_deployment/on_device_model.md`
- Capability: deploy/use models on the device.
- Verification: upload path, UI/Node-RED model selection, service restarts and inference output. See `overview.md` for the official model list.

### Hardware and product documentation

`reCamera_Basic/hardware_and_specs/reCamera_2002_series.md` (also `reCamera_gimbal.md`, `reCamera_hq_poe.md`)
- Capability: hardware/spec reference.
- Verification: content consistency with the actual device, ports, storage, camera and OS endpoints.

`reCamera_Basic/getting_start/reCamera_2002_series/quick_start_guide.md`
- Capability: first boot/setup.
- Verification: power, USB/network, Web UI initialization, SSH and the version endpoint.

`reCamera_Basic/getting_start/reCamera_2002_series/reCamera_warranty.md`
- Capability: warranty/reference.
- Verification: content clarity and links; not a runnable demo.

`reCamera_Basic/getting_start/reCamera_gimbal/reCamera_gimbal.md`
- Capability: gimbal setup (formerly `get_started`).
- Verification: requires gimbal hardware; verify UI/control motion and service state.

`reCamera_Basic/getting_start/reCamera_gimbal/gimbal_development_c.md`
- Capability: C gimbal CAN development.
- Verification: CAN interface, bitrate `100000`, frame format, command logs and motion.

`reCamera_Basic/getting_start/reCamera_gimbal/gimbal_node_red.md`
- Capability: gimbal Node-RED flow.
- Verification: flow import and motion (if the hardware exists).

`reCamera_Basic/getting_start/reCamera_gimbal/PID_adjustment.md`
- Capability: gimbal tuning.
- Verification: hardware present, safe test procedure, parameter changes, motion results.

`reCamera_Basic/getting_start/reCamera_hq_poe/reCamera_hq_poe_start.md`
- Capability: HQ PoE quick start.
- Verification: only on HQ PoE hardware; otherwise hardware-blocked.

`reCamera_Basic/getting_start/reCamera_hq_poe/reCamera_hq_poe_microscope_demo.md`
- Capability: microscope demo.
- Verification: requires HQ PoE and a microscope setup; verify image stream/sample capture.

`modules/wifi_halow/wifi_halow_qucik_start.md`
- Capability: Wi-Fi HaLow module unboxing/assembly, hardware interfaces and quick start (the `qucik` spelling in the filename is upstream).
- Verification: requires HaLow module hardware; assembly, interface identification, network connectivity. Missing hardware = blocked.

### Software documentation

`reCamera_Basic/software_documents/develop_with_c_cpp.md`
- Capability: C/C++ development baseline.
- Verification: toolchain, SDK paths, example build, deployment, runtime commands.
- See "C++ build and deployment baseline" in `overview.md` for the operating procedures.

`reCamera_Basic/software_documents/Real_time_YOLO_object_detection_using_reCamera_based_on_Cpp.md`
- Capability: C++ YOLO HTTP/API demo.
- Verification: build/deploy the model detector, stop services, run with the environment set, verify `http://192.168.42.1/modeldetector` JSON.

`reCamera_Basic/software_documents/Make_the_Cpp_program_auto_start_on_boot.md`
- Capability: SysVinit auto-start.
- Verify only when the demo is stable and the user asks. Check script ordering, permissions, reboot behavior.

`reCamera_Basic/software_documents/configure_static_ip_on_recamera.md`
- Capability: network configuration.
- High risk. Back up the configuration, verify the current network, keep an SSH recovery path, apply only on request.

`reCamera_Basic/software_documents/develop_with_nodered.md`
- Capability: Node-RED development.
- Verification: Node-RED UI, flow import/export, required nodes, service restarts.

`reCamera_Basic/software_documents/linux_fundamentals.md`
- Capability: basic Linux usage.
- Verification: content QA, or run commands on request.

`reCamera_Basic/software_documents/os_structure.md`
- Capability: reCamera OS structure.
- Verification: filesystem paths, services, model locations, overlay/userdata behavior.

`reCamera_Basic/software_documents/os_version_control.md`
- Capability: OS update/versioning.
- High risk. Verify the version endpoint and update paths; do not upgrade unless requested.

`reCamera_Basic/software_documents/reCamera_connects_to_Xiao_via_HTTP.md`
- Capability: HTTP integration with XIAO.
- Verification: XIAO hardware/network, endpoints, request format, receiving action.

`reCamera_Basic/guides/getting_started_onvif.md`
- Capability: deploy ONVIF on reCamera, standard-protocol interop (NVR/client stream pulling).
- Verification: an ONVIF client can discover the device and pull the stream; dependent components and service state.

### FAQ

`reCamera_Basic/faqs/faqs.md`
- Capability: troubleshooting/reference.
- Verify claims against the real device when relevant.
