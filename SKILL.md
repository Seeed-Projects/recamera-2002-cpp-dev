---
name: recamera-2002-cpp-dev
description: "Developer toolkit for reCamera 2002 (SG200X): scaffold a demo workspace, fetch Seeed-hosted models, convert ONNX to .cvimodel, cross-compile SDK-free demos, run host smoke tests, package a reproducible archive, deploy over SSH, capture evidence, and score output against a baseline. Includes an optional host-side viewer (UDP H.264 + detections + parameter tuning). Requires a reCamera 2002 device, SSH access, and optional TPU-MLIR/ONNX and Xuantie RISC-V toolchain for model/build stages. Not for reCamera Pro / RV1126B."
license: Apache-2.0
compatibility: "Host: Linux x86_64 for the full build/deploy chain; macOS for host-side validation/viewing/deploy (SDK build and TPU-MLIR need Docker/VM); Windows via WSL2 for the full chain. Device: reCamera 2002 (SG200X), OS >= 0.2.4."
---

# reCamera 2002 C++ Dev Toolkit

This skill turns an idea or upstream demo into a **reproducible developer
workspace**: declarations live in `demo.json`, every stage is a command, and
offline stages must pass before a device is involved.

## Installation and environment

This skill works with both **Claude Code** and **Codex** (and other agents
compatible with the Agent Skills spec). Copy or symlink the whole directory to
the matching location, keeping the directory name `recamera-2002-cpp-dev`:

- Claude Code: `~/.claude/skills/recamera-2002-cpp-dev`
- Codex: `~/.codex/skills/recamera-2002-cpp-dev`
- Gemini CLI: `~/.gemini/skills/recamera-2002-cpp-dev`

All locations share the same `SKILL.md` (the frontmatter only depends on
`name` + `description`); the Codex display name, invocation prompt, and policy
live in `agents/openai.yaml`. `scripts/`, `templates/`, and `references/` in
commands resolve relative to this file's directory, so the skill runs directly
whether installed from any of those locations or symlinked.

In Codex, explicit invocation via `$recamera-2002-cpp-dev` is required by
default (`allow_implicit_invocation: false`), because deploying, stopping
services, and SSH are side-effecting operations; change
`agents/openai.yaml` if you want automatic triggering.

Prerequisites:

- Python 3.10+ (standard library only; see below for the model/evidence/build
  stages).
- SSH access to the reCamera 2002; the USB direct-connect default address is
  `192.168.42.1`.
- Cross-compiling: **Linux x86_64** (native, or Docker/WSL2 on
  macOS/Windows) + Xuantie GCC 10.2.0 + the SG200X SDK
  (`SG200X_SDK_PATH`).
- Model conversion (optional): a TPU-MLIR environment + the `onnx` Python
  package.
- Evidence capture (optional): `ffmpeg`; WebUI screenshots require
  Chrome/Chromium.
- The device password goes only in the `RECAMERA_PASSWORD` environment
  variable — never in scripts, argv, or reports.

## One-command live delivery (recommended experience)

When the user says "make a demo of X", the recommended deliverable is **a
clickable URL** (local-only by default; add `--bind 0.0.0.0` when LAN viewing
is needed):

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host <device-ip> --bind 0.0.0.0 --allow-actions --allow-service-stop
# LIVE_URL=http://<host-ip>:8090/?token=...
```

Only `--bind 0.0.0.0` exposes the UI to the LAN; only `--allow-actions` lets
the browser trigger install/start/stop; `--allow-service-stop` is the explicit
authorization to stop Node-RED/Supervisor. All three are off by default — add
them as needed.

Flow: offline validate/build/smoke → device install/start (stops
Node-RED/Supervisor, requires `--allow-service-stop`) → start `serve_demo.py`
in the background on the host → wait for the first frame → collect
`live.perf` samples → print the URL. The final reply must start with
`LIVE_URL`; when the status is `placeholder` / `degraded`, say so plainly —
never turn "the log exists" or "the process came up" into a detection success.

On failure `launch_demo` returns `degraded` but does not restart the device
services automatically; restore them with `deploy_demo.py --action stop`, or
manually via `/etc/init.d/<service> start`.

Prerequisites: the demo's `demo.json` has a `live` section, and the
camera/detection code is actually implemented (`live.implemented=true`). With
only the transport skeleton, the UI shows a "demo skeleton" notice and
`LIVE_STATUS=placeholder`; in that state, do not claim the user can see
detection results. See [tools/device-pipeline.md](references/tools/device-pipeline.md)
for how to complete the camera pipeline. **The inference frame must be taken
via `FrameRelay` from `live_camera.hpp` — never call `retrieveFrame` directly
in the inference loop**: the latter's FIFO queue turns a 5 fps consumer into a
3-second delay.

**Output contract**: the first line of the final reply is the clickable
`LIVE_URL`, followed by at most two lines of honest status (whether streaming
is live, whether detection is genuinely wired in; when `LIVE_PERF` is present,
include the result age). `LIVE_PERF=degraded` must never be described as
passing performance. `LIVE_RESULT_AGE_MS` is the real device-side result age,
not a host-clock difference. Do not deliver a command list, the six-stage
table, or a `device-run` report as the deliverable; without running
`evaluate_demo.py`, give no accuracy or quality conclusions. Full steps in
[workflows/live-demo.md](workflows/live-demo.md).

## Performance and smoothness (keep in mind when generating)

When generating a live demo, give a thought to performance and video smoothness
along the way: keep the video channel fluid first; measure `infer_ms` before
setting the detection cadence — cadence is only a frame-taking rhythm, not a
performance knob; report fps from the real interval between two consecutive
inferences, not as a substitute `1000 / infer_ms`; when the model is slow, look
first at input size and code-side optimizations such as decode/NMS. There is no
hard gate — `LIVE_PERF` is a reference signal only.

## Scope

- **In scope**: reCamera 2002 / SG200X, C/C++, `.cvimodel` (TPU-MLIR/CVI),
  official wiki demos and custom-model demos.
- **Out of scope**: reCamera Pro / RV1126B / RKNN (use `procode` or
  `recamera-pysdk`). Demos that need dedicated hardware (gimbal, voice, etc.)
  can only reach a "hardware blocked" conclusion.
- **Supported range**: reCamera 2002, OS >= 0.2.4 (below 0.2.4 is outside the
  supported range). Other 2002 variants (2002w, HQ PoE, Gimbal) are
  unverified — reports must mark them `not_run`.
- Never modify firmware, Buildroot, signatures, or device identity; never stop
  `sshd`, networking, or dhcp services; never write passwords into scripts,
  argv, reports, or the knowledge base.

First judge feasibility via [capability-routing.md](references/capability-routing.md);
the conclusion must be exactly one of Supported / Supported with preconditions
/ Blocked / Not supported.

## Pipeline lanes

| Lane | Commands | Device |
|---|---|---|
| Development (offline) | `new_demo` → `validate_demo` → `build_demo` → `smoke_demo` → `package_demo` | No |
| Model (offline) | `fetch_models` → `convert_model` → `inspect` | No |
| Acceptance (online) | `probe_device` → `deploy_demo` → `capture_evidence` → `evaluate_demo` → `record_demo` | Yes |
| Host-side viewer (online, optional) | `serve_demo` → browser live view/logs/parameter tuning → `capture_evidence --source live` | Yes |

For document navigation see [doc-router.md](references/doc-router.md). Do not
read every document before starting.

## Quick start

```bash
export RECAMERA_PASSWORD='<device password>'     # environment variable only; SSH_ASKPASS by default

# 0. One command (for an already-implemented live demo; prints LIVE_URL at the end)
#    Omit --bind for local-only viewing; --bind 0.0.0.0 to share on the LAN
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/my_live \
  --host <device-ip> --bind 0.0.0.0 --allow-actions --allow-service-stop

# 1. Scaffold (the generated workspace passes offline validation immediately; --live udp adds the host-side transport)
python3 scripts/new_demo.py --name my_demo --task detection --out ~/reCamera_demo
python3 scripts/new_demo.py --name my_live_demo --task detection --live udp --out ~/reCamera_demo

# 2. Offline chain
python3 scripts/validate_demo.py --demo-dir ~/reCamera_demo/my_demo
python3 scripts/build_demo.py    --demo-dir ~/reCamera_demo/my_demo
python3 scripts/smoke_demo.py    --demo-dir ~/reCamera_demo/my_demo
python3 scripts/package_demo.py  --demo-dir ~/reCamera_demo/my_demo

# 3. Models (optional)
python3 scripts/fetch_models.py --name yolo11n --out ~/reCamera_demo/my_demo/models
python3 scripts/convert_model.py inspect model.onnx
python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
  --quant i8 --dataset calibration.txt --out build/model-i8 --sample held-out.jpg

# 4. Device
python3 scripts/probe_device.py  --host <device-ip> --report dist/probe.json
python3 scripts/deploy_demo.py   --demo-dir ~/reCamera_demo/my_demo --host <device-ip> \
  --action install --replace
python3 scripts/deploy_demo.py   --demo-dir ~/reCamera_demo/my_demo --host <device-ip> --action start
python3 scripts/deploy_demo.py   --demo-dir ~/reCamera_demo/my_demo --host <device-ip> --action verify

# 5. Host-side viewer (optional): browser live view + logs + parameter tuning
python3 scripts/serve_demo.py    --demo-dir ~/reCamera_demo/my_demo \
  --device-host <device-ip> --open
```

The host-side Python code depends only on the 3.10+ standard library; model
conversion needs an external TPU-MLIR + `onnx`, evidence capture needs
`ffmpeg` (plus Chrome/Chromium for WebUI screenshots), and cross-compiling
needs Xuantie GCC + the SG200X SDK. By default the password is passed through
the `SSH_ASKPASS` subprocess environment (`DEMO_SSH_AUTH=sshpass` switches
back to sshpass).

## Tools

| Script | Purpose | Device |
|---|---|---|
| `new_demo.py` | Generate a cpp / cmake / script / prebuilt workspace from `templates/` | No |
| `validate_demo.py` | JSON Schema + contract + hash + secret-leak checks; three tiers `demo/deliver/publish` | No |
| `build_demo.py` | SDK-free cross-compilation, verifies ELF64 RISC-V, records the toolchain lock and artifact hashes | No |
| `smoke_demo.py` | Runs the host smoke test per the `smoke` section of `demo.json`; records `not_run` when no hook exists | No |
| `package_demo.py` | Generate a deterministic `.tar.gz` + `files.sha256` + packaging report | No |
| `fetch_models.py` | Download official models and verify them against `configs/model-assets.lock.json` | No |
| `convert_model.py` | ONNX inspection, recipe validation, FP/INT8 conversion, numerical comparison | No |
| `probe_device.py` | Read-only device inventory, contract-drift check | Yes |
| `deploy_demo.py` | upload/install/start/stop/restart/status/verify/collect/clean | Yes |
| `launch_demo.py` | One-command deploy + start the fixed UI in the background + print `LIVE_URL` | Yes |
| `serve_demo.py` | Host fixed UI: UDP H.264/detections/logs, parameter control, session persistence (for observation/parameter tuning — not a replacement for the device management UI) | Yes |
| `capture_evidence.py` | RTSP/WebUI/UDP/live-session/file evidence, hash and zero-byte checks | Yes/local |
| `evaluate_demo.py` | detection/classification/OCR/segmentation/pose/custom scoring | No |
| `record_demo.py` | Write records back to the record file in the fixed format (default user data directory, initialized from the template on first run) | No |
| `diagnose_demo.py` | Read-only diagnosis of demos that fail to install or start | Yes |
| `check_knowledge.py` | Structure, link, contract, lock, script syntax, and secret-leak checks | Optionally online |

Command details and exit codes: [references/scripts.md](references/scripts.md).

## Workspace contract

Every demo directory must have a `demo.json`; all fields are documented in
[demo-contract.md](references/demo-contract.md), and the machine-checkable
schema is at `schemas/demo.schema.json`.

```json
{
  "schema_version": 1,
  "name": "hello_cpp",
  "version": "1.0.0",
  "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
  "build": {"kind": "cpp", "source_dir": "src", "sources": ["main.c"], "binary": "hello_cpp"},
  "runtime": {"executable": "hello_cpp", "args": [], "env": {}, "sudo": false,
              "timeout_s": 10, "expect_stdout": ["hello"]},
  "evaluation": {"task": "custom", "thresholds": {}},
  "model": null,
  "inputs": {"camera": false, "video": false, "images": false},
  "smoke": {"kind": "host-logic", "command": ["sh", "smoke.sh"], "timeout_s": 10,
            "expect_stdout": ["self-test ok"]}
}
```

Fixed contracts: `configs/device-contract.json` (device),
`evaluation-contract.json` (scoring), `toolchain.lock.json` (toolchain
minimums), `model-assets.lock.json` (official model hashes). Record on-device
changes and drift with `probe_device.py` — do not edit the contracts in place.

## Evidence levels (must be reported separately)

| Level | Meaning | Cannot prove |
|---|---|---|
| `static` | Schema + `validate_demo.py` passed | Compiles, runs |
| `offline` | Cross-compiled, smoke-tested, packaged artifact hashes locked | Device compatibility |
| `host-baseline` | The official Python/NVIDIA baseline ran to completion on fixed inputs | On-reCamera behavior |
| `device-upload` | Files uploaded and SHA-256 matches | Process start, task correctness |
| `device-run` | Process started; logs and stdout markers match | Precision/recall |
| `task-quality` | `evaluate_demo.py` quality gate passed | Generalization to other scenes |

For levels not executed, write "not executed / unverified"; never substitute a
PID, a socket, or the existence of logs. All reports follow the six-stage
structure in `schemas/report.schema.json`.

The host-side live view is **not evidence**: it exists only for observation and
parameter tuning. When evidence must be kept, convert device snapshots, logs,
and event streams into a standard `evidence.json` with
`capture_evidence.py --source live --session <serve_demo session directory>`.

## Quality gate and evidence

- Vision quality gate: the same batch of fixed inputs, with the baseline and
  device outputs frame-aligned; detection defaults to `F1 >= 0.60` plus
  per-class recall thresholds; classification/OCR/segmentation/pose default to
  `>= 0.60`.
- The dataset must provide `eval/input/manifest.json`
  (source/version/license/samples/selection/cache_path), see
  [evaluation.md](references/evaluation.md).
- Evidence directory layout and report formats: [evidence.md](references/evidence.md).
- When the score does not pass, do not record "success" evidence and do not
  write a success record; failures must still be recorded (`status: failed`).

## Knowledge base

- `knowhubs/reCamera_KnowHub/overview.md`: device access, services, the C++
  build/deploy baseline, receiver recording, wiki mapping
- `knowhubs/reCamera_KnowHub/demo-records.md`: capability catalog + record
  format (personal records go to the user data directory, never back into the
  skill)
- `knowhubs/reCamera_KnowHub/model-conversion.md`: CVI/TPU-MLIR conversion
  methodology (historical results are marked `not_run`; re-verify before use)
- Provenance and licensing: see the `NOTICE` file at the root (upstream wiki
  GPL-3.0, this skill's code Apache-2.0)
- Recording conventions: [knowledge-base.md](references/knowledge-base.md)

## Security and device boundaries

- The device password is passed only via the `RECAMERA_PASSWORD` environment
  variable; never write it into scripts, argv, reports, the knowledge base, or
  chat logs. Sharing a `LIVE_URL` means sharing that session's access token —
  send it only to trusted people and run `launch_demo.py --stop` when done.
- By default bind only to `127.0.0.1` and keep browser actions disabled. For
  LAN viewing, add `--bind 0.0.0.0` explicitly; for browser-triggered
  install/start/stop, add `--allow-actions` explicitly. Use both only on
  trusted LANs — never expose to the public internet (no TLS).
- Stopping Node-RED/Supervisor also takes down the device Web UI and Studio;
  this requires an explicit `--allow-service-stop`, and services must be
  restored after testing with `deploy_demo.py --action stop`.
- Never modify firmware, Buildroot, signatures, or device identity; never stop
  `sshd`, networking, or dhcp services.
- The host-side live view is not evidence; when evidence is needed use
  `capture_evidence.py --source live`.

## Maintenance

```bash
python3 -m pytest tests -q                       # offline tests
python3 scripts/check_knowledge.py               # structure/links/contracts/locks/secrets
python3 scripts/check_knowledge.py --online      # upstream wiki branch
python3 scripts/check_knowledge.py --update-lock # update the wiki snapshot lock
```

After changing scripts you must update both `tests/` and
[references/scripts.md](references/scripts.md) in the same pass; contract
changes must cite on-device measured evidence. The host-side static assets
live in `templates/live-ui/`; the bundled jmuxer is MIT-licensed — keep
`vendor/LICENSE.jmuxer` when upgrading.
