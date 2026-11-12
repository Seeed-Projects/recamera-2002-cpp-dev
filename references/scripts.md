# Script reference

The host-side Python code depends only on the 3.10+ standard library; `convert_model.py` additionally
needs an external TPU-MLIR environment (`inspect` needs `onnx`), the RTSP/WebUI modes of
`capture_evidence.py` need `ffmpeg` / Chrome, and cross-compiling needs Xuantie GCC + the SG200X SDK.
By default `demo.json` is read from `~/reCamera_demo/...` or from `--demo-dir`.
Shared implementations live in `scripts/devkit/`; `scripts/demo_lib.py` remains as a compatibility entry point.

## Common conventions

- Device password: `export RECAMERA_PASSWORD=...`. Scripts default to OpenSSH `SSH_ASKPASS`
  (the password exists only in the child-process environment, never in argv, the process list, or reports);
  `DEMO_SSH_AUTH=sshpass` switches back to `sshpass -e`.
- Use `--no-password` when an SSH key is configured; the scripts then use plain `ssh`.
- Host keys: `StrictHostKeyChecking=accept-new` by default; `--insecure-host-key` may be added in test environments.
- Exit codes: `0` pass; `1` validation/quality-gate failure; `2` precondition failure (arguments, SSH,
  input files, etc.). `smoke_demo.py` uses `2` to mean `not_run` when there is no `smoke` section.
- `--report <path>` writes machine-readable JSON; `deploy_demo.py` defaults to `dist/device/<action>-report.json` when not specified.
- Reports from newer commands use the six-stage structure; see `schemas/report.schema.json` for the schema.

## devkit modules

| Module | Contents |
|---|---|
| `devkit/util.py` | JSON, hashing, redaction, time, JSONL |
| `devkit/contracts.py` | `demo.json` validation, contract loading, secret scanning, ELF checks |
| `devkit/transport.py` | SSH/SCP, passwords never in argv |
| `devkit/artifacts.py` | Toolchain discovery, deterministic tar packaging, file collection |
| `devkit/reports.py` | Six-stage reports |
| `devkit/schema.py` | Standard-library JSON Schema subset validation |

## new_demo.py

```bash
python3 scripts/new_demo.py --name <name> --kind cpp|cmake|script|prebuilt --task <task> \
  --out ~/reCamera_demo [--artifact <riscv-binary>] [--force] [--live udp]
```

Generates the workspace from `templates/` and automatically runs `validate_demo.py --mode demo`.
`--live udp` additionally overlays `templates/live/` (H.264 + detection UDP sender, parameter
listener, host smoke test) and only supports `cpp` / `cmake`. The generated `live.implemented=false`;
set it to `true` only after wiring up a real camera/detection pipeline.
See [tools/scaffold.md](tools/scaffold.md) and [tools/serve.md](tools/serve.md).

## fetch_models.py

```bash
python3 scripts/fetch_models.py --list
python3 scripts/fetch_models.py --name yolo11n --out <demo>/models [--force] [--dry-run]
```

Downloads per `configs/model-assets.lock.json` and verifies size/sha256.
See [tools/fetch-models.md](tools/fetch-models.md).

## convert_model.py

```bash
python3 scripts/convert_model.py check-env [--tpu-mlir-root <dir>]
python3 scripts/convert_model.py inspect model.onnx [--report inspect.json]
python3 scripts/convert_model.py convert --onnx m.onnx --recipe recipe.json \
  --quant fp|i8 --out build/model-i8 [--dataset calibration.txt] [--sample held-out.jpg] [--dry-run]
```

Outputs: `<out>/<name>.cvimodel`, `conversion-report.json`, `manifest.fragment.json`.
See [tools/convert.md](tools/convert.md).

## package_demo.py

```bash
python3 scripts/package_demo.py --demo-dir <dir> [--mode package|publish] [--out <dist>]
```

Produces a deterministic `.tar.gz` + `files.sha256` + `package-report.json`.
See [tools/package.md](tools/package.md).

## smoke_demo.py

```bash
python3 scripts/smoke_demo.py --demo-dir <dir> [--fixture <path>] [--timeout 15]
python3 scripts/smoke_demo.py --archive dist/<name>-<version>.tar.gz
```

Runs the host smoke test from the `smoke` section of `demo.json`; with no hook it records `not_run` (exit 2).
`--archive` safely unpacks and then validates the final artifact.
See [tools/smoke.md](tools/smoke.md).

## probe_device.py

Read-only inventory of the device, compared against `configs/device-contract.json`.

```bash
python3 scripts/probe_device.py --host 192.168.42.1 --report dist/probe.json
python3 scripts/probe_device.py --host 192.168.42.1 --expect-mac <device-mac>
python3 scripts/probe_device.py --host 192.168.42.1 --no-password
```

Collects: hostname/kernel/arch, `/etc/os-release`, `/api/version`, uptime, MAC, storage, tools, paths, init services, processes, `.cvimodel` inventory.

Checks: architecture, OS version, required tools, paths, `/userdata` space, MAC identity, presence of a local compiler (informational). Any error-level check failure exits `1`; SSH failure exits `2`.

## validate_demo.py

Offline validation; does not connect to the device.

```bash
python3 scripts/validate_demo.py --demo-dir ~/reCamera_demo/<demo> --report dist/validate.json
python3 scripts/validate_demo.py --demo-dir ~/reCamera_demo/<demo> --mode deliver
```

Checks: manifest fields, existence of source/model/extra files, model SHA-256, evaluation task and thresholds, shell metacharacters in run args, env keys, service-control allowlist, secret patterns, artifact architecture (if built).

## build_demo.py

```bash
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo>
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo> --check-only
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo> --strict-toolchain
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --sdk-root ~/sg2002_recamera_emmc --toolchain-bin ~/host-tools/gcc/riscv64-linux-musl-x86_64/bin
```

Toolchain resolution order: `--toolchain-bin` → `TOOLCHAIN_BIN` → `$SG200X_SDK_PATH/host-tools/gcc/riscv64-linux-musl-x86_64/bin` → `~/host-tools/...` → `PATH`.
`configs/toolchain.lock.json` records the verified minimum GCC version; `--strict-toolchain` returns `2` on a mismatch.

Outputs: `dist/<binary>`, `dist/build-report.json` (compiler version, source hashes, artifact hashes, architecture, toolchain-lock match status). Missing toolchain exits `2`.

## deploy_demo.py

```bash
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action upload
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action install --replace
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action start
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action verify
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action collect
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action stop
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action clean --yes
```

Actions:

| Action | Behavior |
|---|---|
| `upload` | Uploads to `/userdata/.demo-stage/<name>-<ts>` with per-file SHA-256 verification |
| `install` | Uploads and copies to `/userdata/demos/<name>`; when it already exists, `--replace` is required and backs up to `.bak-<ts>` first |
| `start` | Runs in the foreground (`long_running=false`) or in the background via `setsid` + `run.pid`; uses `sudo -S` when needed |
| `stop` | Kills only the process in `run.pid`; restores `services_start` per the manifest |
| `status` | Verifies installed-file hashes, PID, and log tail |
| `verify` | `status` plus `runtime.expect_stdout` marker matching; outputs `evidence_level: device-run` |
| `collect` | Pulls `logs/` and the remote `demo.json` back to `dist/device/` |
| `clean` | Deletes the remote demo directory and staged uploads; requires `--yes` |

`--allow-service-stop` is the required authorization to stop services declared in the manifest; without it `start` fails outright. `--no-restore` prevents `stop` from restoring services.

When the manifest has a `live` section, setting `LIVE_PEER=<host-ip>` makes `start` automatically append
`--peer <host-ip>` (not duplicated if `runtime.args` already has `--peer`). `LIVE_ARGS` can append
`--port` / `--control-port` (existing flags are not duplicated). `serve_demo.py`'s
UI actions pick the host NIC address from `--device-host` and set it automatically.

The remote directory must be under `/userdata/demos/`, which keeps `--remote-dir` from pointing at arbitrary paths.

## launch_demo.py

One command deploys an implemented live demo to the device, starts the fixed UI in the background on the host, and finally prints a
clickable `LIVE_URL`:

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host 192.168.42.1 --bind 0.0.0.0 --allow-actions --allow-service-stop
python3 scripts/launch_demo.py --demo-dir <dir> --status
python3 scripts/launch_demo.py --demo-dir <dir> --stop
```

By default it binds `127.0.0.1`, browser actions are disabled, and no service-stop authorization is granted;
LAN viewing, UI actions, and stopping Node-RED/Supervisor require `--bind 0.0.0.0`, `--allow-actions`,
and `--allow-service-stop` respectively.

Default sequence: validate → build (when the artifact is missing) → smoke → start `serve_demo.py` (detached)
→ `probe/install/start` → wait for the first frame. `--no-deploy` only starts the host UI; `--json` outputs
machine-readable results. Statuses: `ready` / `placeholder` / `degraded` / `reused` / `stopped`.
After the first frame it also collects `live.perf` samples for `--perf-wait` (default 3 s); when the result age or update rate
exceeds thresholds the status is `degraded`, and it outputs `LIVE_RESULT_AGE_MS`, `LIVE_DETECT_FPS`,
`LIVE_PERF=ok|degraded|unmeasured` (`--no-perf-gate` skips this).
See [tools/launch.md](tools/launch.md).

## serve_demo.py

Host-side live-view server: receives the demo's UDP H.264/detection/log stream, serves a fixed browser UI,
and relays parameter changes and control-plane traffic to the device.

```bash
python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/my_demo \
  --device-host 192.168.42.1 --open
python3 scripts/serve_demo.py --bind 0.0.0.0 --token secret \
  --demo-dir ~/reCamera_demo/my_demo --device-host 192.168.42.1 --allow-actions
```

Defaults: HTTP `127.0.0.1:8090`, UDP `0.0.0.0:5000`, control `5001`.
`--demo-dir` supplies the `live.params` controls and the deployment workspace; only with `--allow-actions`
may the UI call `deploy_demo.py` (which needs `ssh` on PATH). `--ui-dir` can override the static UI
(useful when debugging the UI). The `pipeline` block in `/api/state` reports `result_age_ms` / `detect_fps` / `video_fps`
over the last 10 s and judges `ok` against `live.perf`.
Sessions are written to `<demo-dir>/eval/live/<timestamp>/`; convert them into
standard evidence with `capture_evidence.py --source live --session <dir>`.
See [tools/serve.md](tools/serve.md).

## capture_evidence.py

```bash
# RTSP: video + single frame
python3 scripts/capture_evidence.py --source rtsp --url rtsp://192.168.42.1:554/live \
  --out eval/recamera/evidence --duration 20 --probe-report dist/probe.json

# Web UI: headless Chrome screenshot + DOM
python3 scripts/capture_evidence.py --source webui --url http://192.168.42.1/ \
  --out eval/recamera/evidence --wait-ms 8000

# UDP demo: use the official receiver script
python3 scripts/capture_evidence.py --source udp \
  --receiver "python3 udp_receiver.py --port 5001" \
  --out eval/recamera/evidence --duration 30

# existing files
python3 scripts/capture_evidence.py --source file --file out.mp4 --file frame.jpg \
  --out eval/recamera/evidence

# host-side live session (the directory written by serve_demo.py)
python3 scripts/capture_evidence.py --source live \
  --session ~/reCamera_demo/my_demo/eval/live/<timestamp> \
  --out eval/recamera/evidence
```

Any zero-byte artifact fails; `evidence.json` records the source, commands, tool versions, hashes, device info, and contract drift.

## evaluate_demo.py

```bash
python3 scripts/evaluate_demo.py \
  --baseline eval/baseline/baseline.jsonl \
  --device eval/recamera/recamera.jsonl \
  --task detection \
  --dataset-manifest eval/input/manifest.json \
  --out-dir eval \
  --critical-class person
```

- `--task` is read from the `--demo-dir` manifest when omitted.
- `--threshold KEY=VALUE` is repeatable and overrides `pass_score`, `iou_threshold`, `confidence_threshold`, `max_missing_frames`, `min_samples`, `pck_ratio`, `per_class_recall`.
- `--allow-small-sample` is for debugging only.
- `--strict` treats "missing dataset provenance" and "no threshold" as failures.

Outputs: `qa_report.json`, `qa_report.md`, `failures.json`.

## record_demo.py

```bash
python3 scripts/record_demo.py --result result.json --dry-run
python3 scripts/record_demo.py --result result.json \
  --report-dir ~/reCamera_demo
```

`result.json` fields:

```json
{
  "name": "hello_cpp",
  "date": "<YYYY-MM-DD>",
  "status": "success",
  "capability": "One line describing what the demo does",
  "model_files": "none",
  "dependencies": "none",
  "build_issues": "none",
  "verification": "Device started and passed with the hello marker",
  "lessons": "When sudo needs a password, pass it via stdin, not argv",
  "wiki_issues": "wiki lacks the LD_LIBRARY_PATH note",
  "evidence": {"qa_report": "eval/qa_report.json"}
}
```

The record file is backed up before writing; duplicate records with the same name and date are rejected; `--report-dir` additionally generates `DEPLOY_REPORT.md`.

The default record file is `~/.local/share/recamera-2002-cpp-dev/demo-records.md` in the user data directory
(changeable via `XDG_DATA_HOME` / `RECAMERA_DATA_DIR`); on first run it initializes from the template shipped
with the skill and never writes into the installed skill directory. Use `--records <path>` to specify a file.

## diagnose_demo.py

Read-only diagnosis for installed demos that fail to start.

```bash
python3 scripts/diagnose_demo.py --demo-dir ~/reCamera_demo/<demo> --host 192.168.42.1 \
  --report dist/diagnose.json
```

Collects: remote directory, `demo.json`, `run.pid`, recent logs, related processes, `/dev/video*`, `/dev/snd/*`, memory/disk, kernel tail. Outputs `findings` and `hints`; common conclusions:

- Missing directory/file → reinstall.
- Unreadable log → restart (it chmods the log) or read it with sudo.
- Missing library/file → check `LD_LIBRARY_PATH` and uploaded files.
- SIGSEGV/SIGILL → verify the RISC-V musl ABI.
- ION/OOM → reduce model input/output memory or stop competing services.
- Camera service occupied + `requires_camera` → declare `services_stop` and authorize explicitly.
- Full root partition / low memory → use `/userdata` or shrink the model.

## check_knowledge.py

```bash
python3 scripts/check_knowledge.py
python3 scripts/check_knowledge.py --update-lock
python3 scripts/check_knowledge.py --online
```

Checks required files, config JSON, record fields, Markdown links, script syntax, secret patterns, and the wiki lock. `--online` only checks the upstream branch HEAD; `--online-full` checks page by page (subject to GitHub rate limits).
