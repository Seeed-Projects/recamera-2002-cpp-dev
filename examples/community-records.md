# Community records measured on device (unofficial)

This file keeps the conclusions of demos that individuals/the community have run on real devices. **It is not an official baseline** and does not represent Seeed's test results. Device model, OS version, dataset and metrics apply only to the single run stated in each entry; do not treat these numbers as an official guarantee or as performance data reproducible across versions.

The knowledge base shipped with the skill (`knowhubs/reCamera_KnowHub/demo-records.md`) keeps only the capability catalog and the record format. New records measured on device are written to the user data directory by default (`~/.local/share/recamera-2002-cpp-dev/demo-records.md`, overridable with `record_demo.py --records`) and are not written back into the installed skill directory.

## Demos run so far (measured on device)

### UDP_Face_Analysis (2026-06-05)
- Status: ✅ success (passed after fixes, scored 6/10)
- Capability: C++ face detection with age/gender/race/emotion attributes, UDP-streamed to a receiver.
- Model files: `yolo-face_mixfp16.cvimodel`, `age_gender_race_bf16.cvimodel`, `emotion_bf16.cvimodel`
- Dependencies: external asset `face_udp`, official receiver `udp_receiver.py`
- Build issues: none
- Verification: UDP video frames received successfully; face plus age/gender/race/emotion outputs worked normally
- Lessons: the wiki lacks `sudo` and `LD_LIBRARY_PATH` instructions; the `yolo-face_mixfp16` model must use `single`, not `multi`; for far-distance/profile-face scenes `single 0.5 1` is more stable
- Wiki issues (personal observation): missing `sudo`, missing `LD_LIBRARY_PATH`, unclear `single` vs `multi`, incomplete receiver/headless instructions, risk of model filename spelling errors
- Working command template (see `overview.md`, section "UDP receiver IP", for how to obtain `RECEIVER_IP`):

```bash
sudo env LD_LIBRARY_PATH=/mnt/system/lib:/mnt/system/usr/lib:/mnt/system/usr/lib/3rd:$LD_LIBRARY_PATH \
  ./face_udp yolo-face_mixfp16.cvimodel age_gender_race_bf16.cvimodel emotion_bf16.cvimodel \
  single 0.7 1 "$RECEIVER_IP" 5001 20
```

### yolo_benchmark (2026-06-05)
- Status: ✅ success (passed after fixes, scored 7/10)
- Capability: YOLO11n detection/segmentation benchmark with a UDP receiver.
- Model files: `yolo11n_detection_cv181x_int8.cvimodel` (a segmentation variant also exists: `yolo11n_segment_cv181x_int8.cvimodel`)
- Dependencies: external asset `recamera_benchmark`, official receiver `yolo_udp.py` (listens on UDP port `5001`, fixed)
- Build issues: none
- Verification: detection latency about 49-58 ms, consistent with the wiki's 50 ms / 20 FPS claim
- Lessons: `recamera_benchmark` hardcodes UDP port `5001`, so the receiver must listen on 5001; Linux/headless receivers cannot use `cv2.imshow` and need a save-frame receiver; the receiver IP must be re-checked after a reCamera reboot or USB reconnection
- Wiki issues (personal observation): external asset files not named in the text, missing `sudo`, missing `LD_LIBRARY_PATH`, fixed port `5001` documented unclearly, missing Linux/headless receiver path, receiver IP must be re-checked after reboot
- Working command template (see `overview.md`, section "UDP receiver IP", for how to obtain `RECEIVER_IP`):

```bash
sudo env LD_LIBRARY_PATH=/mnt/system/lib:/mnt/system/usr/lib:/mnt/system/usr/lib/3rd:$LD_LIBRARY_PATH \
  ./recamera_benchmark ./yolo11n_detection_cv181x_int8.cvimodel "$RECEIVER_IP"
```

### lane_direction (2026-10-23)
- Status: ✅ success
- Capability: ROI classification of road directional arrows: forward / left turn / right turn; pure C++ geometric features + 9-16-3 MLP, no OpenCV or NPU dependency; full-frame localization is out of scope for this version
- Model files: models/arrow_mlp.json (6842 B, sha256 aa4ad33a01a47d701d51aa3c1f1c9a2dbc958a3e8755b9d9e9c5eade6841ce31)
- Dependencies: device side: C++17 standard library; host training/reference: Python 3 + numpy + opencv + scikit-learn
- Build issues: none; the RISC-V musl toolchain Xuantie-900 V2.6.1 (gcc 10.2.0) compiled directly, ELF64 RISC-V validation passed
- Verification: lab reCamera 2002 (IP omitted, OS 0.4.14): install/start/verify PASS, 140 frames device-run, forward 89 / left 24 / right 27 / unknown 0, 637 ms (219.8 fps); device vs label top1=0.9857; device vs Python reference=1.0000; CeyMo grouped cross-validation 87.14%
- Lessons: CeyMo's LA/RA labels are mirrored relative to image orientation (a front-facing camera must map them by image orientation); tight arrow crops + arrow-shaft PCA features are clearly better than full-frame connected components; full-frame localization at 640x360 reaches only about 37% IoU hit rate, so this version does ROI classification only
- Wiki issues (personal observation): none; this record does not depend on a wiki demo
