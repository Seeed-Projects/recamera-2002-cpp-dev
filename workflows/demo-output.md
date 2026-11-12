# Demo Output Workflow

> First check [references/doc-router.md](../references/doc-router.md) and pick documents per task; do not read every reference at once.

## Purpose

Deploy, run and **verify the effect** of an idea or open-source project on a reCamera; output reproducible evidence, quality-gate conclusions and knowledge-base records.

## Fast path (when the user wants a "clickable URL")

If the user's goal is a live experience rather than a full quality acceptance:

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host <device-ip> --bind 0.0.0.0 --allow-actions --allow-service-stop
# LIVE_URL=http://<host-ip>:8090/?token=...
```

- Start the final reply with `LIVE_URL`; `LIVE_STATUS=placeholder` / `degraded` must be stated plainly.
- The live UI is not evidence; `task-quality` still goes through steps 0-13 below and `evaluate_demo.py`.
- The demo must already have a `live` section and real camera/detection code; with only the transport scaffold you must not claim detection results are visible.

## Inputs

- A project idea, an open-source project URL, or an existing demo directory
- Target device (default `192.168.42.1`, or an IP in the same subnet)
- Whether app packaging, long-term residency, or a one-off verification is needed

## Mandatory rules

**Acceptance tasks** run step by step; skipping steps is not allowed. Record "executed / not executed / unverified" at every step, and never fill not-executed items with guesses.

> Routing: if the user only wants a "click and watch" live UI (no evidence/score/acceptance/records requested),
> go to [live-demo.md](live-demo.md); this workflow does not force it. But never write up a live-demo URL
> delivery as completed acceptance. When the user later asks for evidence or acceptance, return to step 0 and
> backfill the missing stages.

- Visual demos must have fixed evaluation inputs and a baseline comparison; a live camera "looks fine" is not enough.
- Device evidence must come from actual commands and artifacts; the existence of a PID/socket/log does not prove task correctness.
- When the quality gate fails, do not write a success record; the failure must still be recorded with status `❌`.
- Credentials are passed only through the `RECAMERA_PASSWORD` environment variable, never into scripts, argv, reports or the knowledge base.

## 0. Read the knowledge base

Before starting, read:

- [demo-records.md](../knowhubs/reCamera_KnowHub/demo-records.md): search the Capability catalog and past lessons by demo name, model and task type.
- [model-conversion.md](../knowhubs/reCamera_KnowHub/model-conversion.md): custom-model conversion methods and tested results.
- [overview.md](../knowhubs/reCamera_KnowHub/overview.md): device access, services, the C++ build/deploy baseline, receiver-side recording, wiki mapping.

## 1. Check upstream and history

- Use the GitHub API to check whether wiki pages have new commits after the snapshot date:

```bash
curl -s "https://api.github.com/repos/Seeed-Studio/wiki-documents/commits?sha=docusaurus-version&per_page=5&path=sites/en/docs/Edge/reCamera/<page-relative-path>"
```

- Scan `~/reCamera_demo/*/DEPLOY_REPORT.md` to reuse local conclusions and evidence.
- Start the device with a read-only inventory:

```bash
python3 scripts/probe_device.py --host <device-ip> --report ~/reCamera_demo/<demo>/dist/probe.json
```

If the OS version is below `os.minimum` in `configs/device-contract.json`, record the drift first, then decide whether to continue.

## 2. Create the demo workspace and validate offline

Prefer the scaffold over hand-copying the structure:

```bash
python3 scripts/new_demo.py --name <demo_name> --task <task> --out ~/reCamera_demo
```

Manual setup also works:

```text
~/reCamera_demo/<demo_name>/
├── demo.json
├── src/
├── models/            # optional
├── assets/            # optional
└── dist/
```

`demo.json` fields are in [demo-contract.md](../references/demo-contract.md); the machine schema is in
`schemas/demo.schema.json`. Validate immediately after generating:

```bash
python3 scripts/validate_demo.py --demo-dir ~/reCamera_demo/<demo> --report dist/validate.json
```

Every error must be fixed; warnings and `runtime_preconditions_unverified` go onto the later verification list.

## 3. Identify models and datasets

Must be recorded:

- Model source: GitHub, Hugging Face, paper, README, model card, training script, `data.yaml`.
- Training/validation/test dataset names, versions, label tables, license.
- Official recommended input size, preprocessing, postprocessing and evaluation metrics.

The shared cache is fixed at `~/reCamera_demo/datasets/`; deduplicate before downloading. Accepted sources are official sites, links stated in the project README, Hugging Face datasets, official Kaggle mirrors or trusted open-source mirrors.

For official models take the locked assets directly (no manual URL hunting):

```bash
python3 scripts/fetch_models.py --list
python3 scripts/fetch_models.py --name yolo11n --out ~/reCamera_demo/<demo>/models
```

Self-trained models go through the conversion chain:

```bash
python3 scripts/convert_model.py inspect model.onnx
python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
  --quant i8 --dataset calibration.txt --out build/model-i8 --sample held-out.jpg
```

Conversion success only proves compilation passed; in `conversion-report.json` the
`numerical_validation` and `device_validation` fields are independent and must not substitute for each other.

When the dataset cannot be confirmed, mark the report "evaluated on a non-original dataset" and explain why.

## 4. Prepare fixed evaluation inputs

```text
eval/input/
├── images/
├── video/input.mp4
└── manifest.json
```

`manifest.json` records at least source, version, license, sample count, selection rationale and cache path. Samples cover positive/negative/boundary cases; the same input batch must be used for both the baseline and the device.

Code keeps three input modes: live camera, local video, local images.

## 5. Run the official script on the baseline host

On an NVIDIA GPU host, run the official Python example over the same input batch, outputting:

```text
eval/baseline/{frames/,visualized/,baseline.jsonl,baseline.mp4}
```

Requirements:

- Run the original official code first; do not rewrite a simplified inference.
- Record the Python environment, dependency versions, model files, commands and key parameters.
- `baseline.jsonl` has a stable structure with unique frame numbers for aligning with device output.
- Spot-check multimodally; if the baseline is clearly wrong on trusted inputs, check the official script/weights/label map/thresholds first.

The JSONL structure is in [evaluation.md](../references/evaluation.md).

## 6. Build, smoke and package

```bash
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo> --strict-toolchain
python3 scripts/smoke_demo.py --demo-dir ~/reCamera_demo/<demo>
python3 scripts/package_demo.py --demo-dir ~/reCamera_demo/<demo>
```

`build_demo.py` will:

- Resolve the RISC-V musl toolchain (or pass `--sdk-root` / `--toolchain-bin` explicitly).
- Check the GCC lower bound against `configs/toolchain.lock.json`; `--strict-toolchain` fails on a mismatch.
- Compile/stage artifacts to `dist/<binary>`.
- Validate ELF64 RISC-V (`e_machine=243`).
- Write `dist/build-report.json` (compiler version, source hash, artifact hash, toolchain lock).

`smoke_demo.py` runs pure logic on the host per the `smoke` section of `demo.json`; with no hook it records `not_run`,
which must not be written as a pass.

`package_demo.py` produces a deterministic `.tar.gz`, `files.sha256` and `package-report.json`;
repackaging the same content must yield the same hash. Use `--mode publish` for releases.

Never treat x86 artifacts or unverified prebuilt files as valid artifacts.

## 7. Deploy and run

```bash
export RECAMERA_PASSWORD='<password>'

python3 scripts/deploy_demo.py --demo-dir ~/reCamera_demo/<demo> --host <device-ip> \
  --action install --replace
python3 scripts/deploy_demo.py --demo-dir ~/reCamera_demo/<demo> --host <device-ip> \
  --action start
```

Requirements:

- Verify SHA-256 per file after upload; installing over an existing directory requires a backup first.
- When the camera service must be stopped, the manifest declares the service and `--allow-service-stop` is passed explicitly; restore it after testing.
- Runs use `sudo` and `LD_LIBRARY_PATH` (defaults come from the device contract).
- Record the launch command, model path, parameters and log path.

## 8. Run the device on the same input batch

- Image/video inputs must be exactly the same files as the baseline.
- Export `eval/recamera/recamera.jsonl` with fields aligned to the baseline; detection boxes are restored to original image coordinates.
- Save logs, model path, thresholds, NMS, input size and label map.
- The live camera is supplementary evidence and does not replace the fixed inputs.

## 9. Scoring and quality gates

```bash
python3 scripts/evaluate_demo.py \
  --baseline eval/baseline/baseline.jsonl \
  --device eval/recamera/recamera.jsonl \
  --task detection \
  --dataset-manifest eval/input/manifest.json \
  --out-dir eval \
  --critical-class <critical-class>
```

Default quality gates (overridable in the manifest or CLI):

- Detection: F1 ≥ 0.60, critical-class recall ≥ 0.60.
- Classification: top-1 agreement ≥ 0.60.
- OCR: text similarity ≥ 0.60.
- Segmentation: mIoU ≥ 0.60.
- Pose: PCK ≥ 0.60.
- Custom/depth: metrics and thresholds must be defined explicitly.

On failure, fix following the troubleshooting order in [evaluation.md](../references/evaluation.md), then re-run the affected steps.
After repeated failed fixes, stop the release flow and record the failure cause with reproducible evidence.

## 10. Multimodal review

Read and compare:

- `eval/baseline/baseline.mp4`
- `eval/recamera/recamera.mp4`
- `eval/qa_report.md`
- failure frames under `eval/failures/`

Judge whether the output matches the demo goal. If the program ran clean but results are wrong, go back to step 9.

## 10.5 Host-side live review (optional)

When device compute is weak and you do not want the Web UI, use the host fixed UI to watch the live picture and logs and tune parameters online:

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host <device-ip> --bind 0.0.0.0 --allow-actions --allow-service-stop
# For cross-machine viewing use the printed LIVE_URL (includes token); add --open for local debugging.
```

- The device side is generated by `new_demo --live udp`: H.264 1080p30 + 640×640 detection metadata.
- In the browser confirm the picture, boxes/polylines, FPS/packet loss, logs and parameter acks.
- The live picture is not evidence and must not justify `device-run` or `task-quality`;
  for evidence still use step 11's `--source live`.
- Note: when `runtime.services_stop` declares stopping the Node-RED/sscma services,
  `deploy_demo.py start` must pass `--allow-service-stop` explicitly; services are restored by default after the stop.
- When only the URL is wanted and no acceptance is required, go to [live-demo.md](live-demo.md).

## 11. Capture final evidence

```bash
python3 scripts/capture_evidence.py --source rtsp --url rtsp://<device-ip>:554/live \
  --out ~/reCamera_demo/<demo>/eval/recamera/evidence --duration 20
```

- HTTP demo: response body + service logs.
- UDP video demo: frames saved by the official receiver + sender statistics.
- Web UI demo: `--source webui` screenshots + DOM.
- Host session: `--source live --session <serve_demo session dir>`, requiring a device JPEG snapshot.
- Hardware demo: command logs + hardware/credential blocking notes.

Evidence must contain a readable picture (OSD/detection boxes/data overlay); logs alone do not count as visual evidence. Artifact rules are in [evidence.md](../references/evidence.md).

## 12. User review

Open the final video/screenshots and let the user confirm the content, the effect and whether adjustments are needed. Without review, never mark "accepted".

## 13. Record the conclusion

Only executable after steps 0-12 are complete. Prepare `result.json` (fields in [scripts.md](../references/scripts.md)), then:

```bash
python3 scripts/record_demo.py --result result.json --dry-run
python3 scripts/record_demo.py --result result.json --report-dir ~/reCamera_demo
```

The script will:

- Validate fields and status.
- Back up, then append to the records file in the user data directory (default `~/.local/share/recamera-2002-cpp-dev/demo-records.md`, initialized from the skill template on first run); it never writes back into the installed skill directory.
- Generate `~/reCamera_demo/<demo>/DEPLOY_REPORT.md`.

Finally run:

```bash
python3 scripts/check_knowledge.py
```

## Outputs

- Demos run on the device and how they were started/stopped
- `dist/build-report.json`, `dist/device/*-report.json`
- `eval/qa_report.md`, `eval/qa_report.json`, `eval/failures.json`
- `eval/recamera/evidence/evidence.json` plus videos/screenshots
- `~/reCamera_demo/<demo>/DEPLOY_REPORT.md`
- Knowledge-base record

## Failure handling

- Inputs or baseline untrusted: return to steps 3/4/5.
- Build failure: check toolchain, SDK, architecture; do not switch to x86 artifacts.
- Device start failure: run read-only diagnostics first, then decide whether to reinstall:

```bash
python3 scripts/diagnose_demo.py --demo-dir ~/reCamera_demo/<demo> --host <device-ip> \
  --report dist/diagnose.json
```

  Diagnostics cover file hashes, PID, logs, service occupancy, `/dev/video*`, `/dev/snd`, memory/disk and kernel tail; fix according to `hints`.
- Quality-gate failure: troubleshoot per step 9; if it still fails, record `❌` and stop.
- Missing hardware/credentials: mark `blocked`; do not write it up as a wiki or code failure.
