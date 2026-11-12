# reCamera 2002 C++ Dev Toolkit

> **From a one-sentence requirement to a clickable live view.**
>
> A reproducible C++ development toolchain for the reCamera 2002 (SG200X):
> declarative contracts, staged commands, offline-first validation, and
> verifiable evidence.
>
> **Maintained by** Seeed Studio · **License** Apache-2.0 · **Version** 0.1.1
> **Supported** reCamera 2002 · OS >= 0.2.4

---

## Why choose it

For most embedded-vision projects the cost is not in writing code but in
"inconsistent environments, steps that cannot be reproduced, and results that
cannot be re-checked". This toolchain turns those three into engineering
constraints:

- **Contract first**: every demo is declared by `demo.json`, and build,
  deploy, and scoring all follow the contract; missing fields fail at the
  offline stage instead of waiting for the device to complain.
- **Offline first**: the five steps — scaffold, validate, cross-compile, smoke
  test, package — go fully green without touching a device; when the device
  stage is missing it is recorded explicitly as `not_run`, never faking
  success with "the process came up".
- **One-command delivery**: for an already-implemented live demo, a single
  command completes deployment, streaming, and UI startup, ending with a
  clickable `LIVE_URL`.
- **Evidence levels**: six evidence tiers, `static → offline → host-baseline →
  device-upload → device-run → task-quality`, with each tier's what-it-proves
  and what-it-does-not-prove fixed in the spec.
- **Multi-agent native**: the same directory serves Claude Code, Codex, and
  Gemini CLI; the scripts depend only on the Python standard library and
  system commands.
- **Secure defaults**: the UI binds `127.0.0.1` only by default, passwords
  travel only through environment variables, and stopping services requires
  explicit authorization.

## Capability matrix

| Stage | Command | Output |
|---|---|---|
| Scaffold | `new_demo.py` | A workspace that passes offline validation immediately after generation |
| Model | `fetch_models.py` / `convert_model.py` | Official models (SHA-256 verified) or ONNX → `.cvimodel` |
| Build | `build_demo.py` | RISC-V ELF + toolchain lock + artifact hashes |
| Smoke test | `smoke_demo.py` | A host-logic verification report without a device |
| Package | `package_demo.py` | Deterministic `.tar.gz` + `files.sha256` |
| Device | `probe_device.py` / `deploy_demo.py` / `diagnose_demo.py` | Device inventory, deploy/start-stop, failure diagnosis |
| Live preview | `launch_demo.py` / `serve_demo.py` | Browser live view + detection overlay + parameter adjustment + logs |
| Acceptance | `capture_evidence.py` / `evaluate_demo.py` / `record_demo.py` | Standard evidence, quality-gate report, reusable record |

## Supported range

| Item | Support |
|---|---|
| Device | reCamera 2002 (SG200X); OS >= 0.2.4 |
| Languages | C / C++ (SDK-free host smoke tests; SDK linking is built inside the `sscma-example-sg200x` tree) |
| Model format | `.cvimodel` (TPU-MLIR / CVI) |
| Host side | Linux / macOS (SDK builds and TPU-MLIR require Linux or Docker/VM); Windows via WSL2 |
| Not applicable | reCamera Pro / RV1126B / RKNN (please use `procode` or `recamera-pysdk`) |

> 2002w, HQ PoE, and Gimbal are unverified; versions below OS 0.2.4 are
> outside the supported range. When using these combinations, reports must be
> marked `not_run` — do not write unverified as failed or as success.

## Installation

Copy or symlink the directory into the matching agent's skills directory,
keeping the directory name `recamera-2002-cpp-dev`. The same directory can
serve multiple agents at once.

```bash
git clone <repo-url> ~/src/ai-skills

# Claude Code
cp -r ~/src/ai-skills/skills/recamera-2002-cpp-dev ~/.claude/skills/

# Codex
cp -r ~/src/ai-skills/skills/recamera-2002-cpp-dev ~/.codex/skills/

# Gemini CLI (if used)
cp -r ~/src/ai-skills/skills/recamera-2002-cpp-dev ~/.gemini/skills/
```

You can also `git clone <repo-url> ~/.claude/skills/recamera-2002-cpp-dev`
directly, then symlink to the other agent directories as needed.

Cross-agent conventions:

- The entry point is always `SKILL.md`; the frontmatter uses only `name` +
  `description`.
- Codex UI metadata lives in `agents/openai.yaml`; Claude Code ignores that
  file.
- The scripts depend on no agent-specific tooling — only system commands and
  the Python standard library.
- Codex requires explicit invocation of `$recamera-2002-cpp-dev` by default;
  to allow implicit triggering, set `allow_implicit_invocation` to `true`.

### Prerequisites

| Dependency | Purpose | Required |
|---|---|---|
| Python 3.10+ | All host-side scripts (standard library only) | Required |
| SSH access to the reCamera 2002 | Deploy, probe, evidence | Required for device stages |
| Xuantie GCC 10.2.0 + SG200X SDK | Cross-compilation | Required for the build stage |
| TPU-MLIR + `onnx` | Custom model conversion | Optional |
| `ffmpeg` / Chrome | Video and WebUI evidence | Optional |

The device is connected directly over USB/RNDIS by default at address
`192.168.42.1`, SSH user `recamera`. The password is read only from an
environment variable:

```bash
export RECAMERA_PASSWORD='<device password>'
```

Scripts use OpenSSH `SSH_ASKPASS` by default, so the password never enters
argv, the process list, or reports; with an SSH key already configured you can
use `--no-password`.

## Quick start

### 1. Offline chain (no device needed)

```bash
# Generate a workspace
python3 scripts/new_demo.py --name my_demo --task detection --out ~/reCamera_demo

# Generate a Live workspace (adds the UDP transport skeleton)
python3 scripts/new_demo.py --name my_live --task detection --live udp --out ~/reCamera_demo

# The five-step offline chain
python3 scripts/validate_demo.py --demo-dir ~/reCamera_demo/my_demo
python3 scripts/build_demo.py    --demo-dir ~/reCamera_demo/my_demo
python3 scripts/smoke_demo.py    --demo-dir ~/reCamera_demo/my_demo
python3 scripts/package_demo.py  --demo-dir ~/reCamera_demo/my_demo
```

The generated workspace validates immediately and ships a `smoke.sh` that runs
on the host. `--live udp` replaces `src/main.c` with the C++ `src/main.cpp`
and layers on the UDP/H.264 transport.

### 2. Get a live view with one command

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/my_live \
  --host <device-ip>
# LIVE_URL=http://127.0.0.1:8090/?token=...
```

By default it is reachable only from the local machine. To expose it for LAN
viewing, do so explicitly:

```bash
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/my_live \
  --host <device-ip> --bind 0.0.0.0 --allow-actions --allow-service-stop
# LIVE_URL=http://<host-ip>:8090/?token=...
```

The three switches are off by default and mean exactly what they say:

| Switch | Effect | Risk |
|---|---|---|
| `--bind 0.0.0.0` | Exposes the UI to the LAN | No TLS — trusted networks only |
| `--allow-actions` | Lets the page trigger install/start/stop | The page can operate the device |
| `--allow-service-stop` | Allows stopping Node-RED/Supervisor | The device Web UI and Studio go down with it |

> `launch_demo.py` only produces a real picture for demos whose
> camera/inference pipeline is implemented. With only the transport skeleton
> the UI shows "demo skeleton" and the status is `placeholder` — that is not
> a detection success.

### 3. Start only the host-side viewer

```bash
python3 scripts/serve_demo.py --demo-dir ~/reCamera_demo/my_live \
  --device-host <device-ip> --open
```

The page shows four kinds of information: live view, detection/geometry
overlay, model parameters, and device logs. Parameters are delivered over the
UDP control channel and take effect at the frame boundary — no process
restart.

## Workspace contract

Each demo directory is declared solely by `demo.json`; `validate_demo.py`,
`build_demo.py`, and `deploy_demo.py` all follow it.

```json
{
  "schema_version": 1,
  "name": "hello_cpp",
  "version": "1.0.0",
  "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
  "build": {"kind": "cpp", "source_dir": "src", "sources": ["main.c"], "binary": "hello_cpp"},
  "runtime": {
    "executable": "hello_cpp", "args": [], "env": {}, "sudo": false,
    "timeout_s": 10, "expect_stdout": ["hello"]
  },
  "evaluation": {"task": "custom", "thresholds": {}},
  "model": null,
  "inputs": {"camera": false, "video": false, "images": false},
  "smoke": {"kind": "host-logic", "command": ["sh", "smoke.sh"],
            "timeout_s": 10, "expect_stdout": ["self-test ok"]}
}
```

The fixed contracts and lock files live in `configs/` and `schemas/`:

| File | Contents |
|---|---|
| `configs/device-contract.json` | Device capabilities, paths, service whitelist, camera arbitration |
| `configs/evaluation-contract.json` | Default quality thresholds and dataset requirements per task |
| `configs/toolchain.lock.json` | Verified GCC minimums and search order |
| `configs/model-assets.lock.json` | Official model URL / SHA-256 / size / license |
| `configs/wiki-snapshot.lock.json` | Upstream wiki snapshot date and page list |
| `schemas/demo.schema.json` | Machine-checkable schema for `demo.json` |
| `schemas/recipe.schema.json` | Model conversion recipe |
| `schemas/report.schema.json` | Six-stage report structure |

Record differences against a real device as drift with `probe_device.py` — do
not edit the contracts in place.

## Path conventions

| Purpose | Default | Override |
|---|---|---|
| Demo workspace | `~/reCamera_demo/<demo>/` | `new_demo.py --out`, `--demo-dir` on each script |
| Measured records | `~/.local/share/recamera-2002-cpp-dev/demo-records.md` | `XDG_DATA_HOME` / `RECAMERA_DATA_DIR` / `--records` |
| SG200X SDK | `$SG200X_SDK_PATH` | `--sdk-root`, `SG200X_SDK_PATH` |
| RISC-V toolchain | `$TOOLCHAIN_BIN` → path inside the SDK → `~/host-tools/...` → `PATH` | `--toolchain-bin`, `TOOLCHAIN_BIN` |
| TPU-MLIR | `$TPU_MLIR_PATH` → `~/tpu-mlir` | `--tpu-mlir-root`, `TPU_MLIR_PATH` |

Device-side paths come in two kinds — do not mix them:

- **This toolchain's deploy root**: `/userdata/demos/<demo>/` (staging is
  `/userdata/.demo-stage/`).
- **Wiki baseline example directory**: `/home/recamera/<demo>/`, used only for
  comparison against the upstream docs — not this toolchain's install
  location.

The runtime libraries are fixed at
`/mnt/system/lib:/mnt/system/usr/lib:/mnt/system/usr/lib/3rd`, and service
scripts live in `/etc/init.d/S*`. Inside Docker, `/workspace/tpu-mlir` is a
container mount point.

## Directory structure

```text
recamera-2002-cpp-dev/
├── SKILL.md                     # Agent entry point: boundaries, pipeline lanes, command table
├── workflows/                   # End-to-end flows: full acceptance / one-sentence LIVE_URL delivery
├── references/                  # Read on demand: contracts, evaluation, evidence, tool manuals
│   ├── doc-router.md
│   └── tools/                   # scaffold / models / build / deploy / serve ...
├── configs/                     # Device/evaluation/toolchain/model/wiki locks
├── schemas/                     # demo.json / recipe / report schema
├── templates/                   # cpp / cmake / script / live / live-ui
├── scripts/                     # 16 CLI + the devkit library
│   └── devkit/                  # contracts / transport / artifacts / reports / schema
├── tests/                       # Offline pytest regression suite
├── examples/                    # Minimal manifest examples + community records
└── knowhubs/reCamera_KnowHub/   # Device knowledge base (provenance and licensing in NOTICE)
```

## Model pipeline

### Official models

```bash
python3 scripts/fetch_models.py --list
python3 scripts/fetch_models.py --name yolo11n --out ~/reCamera_demo/my_demo/models
```

Downloads come only from the Seeed-hosted URLs recorded in
`configs/model-assets.lock.json`, with size and SHA-256 verified before
writing. Model licenses and attribution are in that file's `license` field;
the repository does not distribute model files.

### Custom ONNX

```bash
python3 scripts/convert_model.py check-env
python3 scripts/convert_model.py inspect model.onnx
python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
  --quant i8 --dataset calibration.txt --out build/model-i8 --sample held-out.jpg
```

Conversion requires an external TPU-MLIR environment (`inspect` additionally
needs `onnx`). A successful conversion only proves it compiled — it does not
prove accuracy; accuracy must go through the fixed-input quality gate of
`evaluate_demo.py`.

## Evidence and quality gates

| Level | Produced by | What you may claim | What you may not claim |
|---|---|---|---|
| `static` | `validate_demo.py` | Contract/structure/hash correctness | Compiles, runs |
| `offline` | `build_demo.py` | The artifact is a RISC-V ELF with locked hashes | The device can load it |
| `host-baseline` | Official Python/NVIDIA baseline | The baseline's result on fixed inputs | On-device behavior |
| `device-upload` | `deploy_demo.py upload/install` | Files arrived with matching hashes | Process start |
| `device-run` | `deploy_demo.py start/verify` | Process, logs, and markers match | Precision/recall |
| `task-quality` | `evaluate_demo.py` | Quality-gate conclusion on fixed inputs | Generalization, real-time performance |

- For levels not executed, write "not executed / unverified"; never substitute
  a PID, a socket, or the existence of logs.
- Vision quality gates default to `F1 >= 0.60` (classification/OCR/
  segmentation/pose default to `>= 0.60`), and the dataset must provide
  `eval/input/manifest.json` recording source, version, license, and sample
  selection.
- The host-side live view is not evidence; when evidence is needed use
  `capture_evidence.py --source live --session <serve_demo session directory>`.

## Security model

- **Credentials**: the device password is passed only via `RECAMERA_PASSWORD`;
  scripts use `SSH_ASKPASS` or `sshpass -e` — never in argv, never in reports.
- **Network**: the UI defaults to `127.0.0.1:8090`; LAN access requires an
  explicit `--bind 0.0.0.0` and automatically demands a token. There is no
  TLS — never expose it to the public internet.
- **Device operations**: browser actions are disabled by default; stopping
  Node-RED/Supervisor requires an explicit `--allow-service-stop`, and
  services must be restored after testing.
- **Boundaries**: no changes to firmware, Buildroot, signatures, or device
  identity; no stopping of `sshd`, networking, or dhcp services.

Sharing a `LIVE_URL` means sharing that session's access token; send it only
to trusted people and run `launch_demo.py --stop` when done.

## Verification boundaries

- The repository's automated tests cover host-side logic and **do not replace
  real-device verification**. On-device conclusions must come from
  `probe_device.py` / `capture_evidence.py`; items not executed are marked
  `not_run`.
- `build_demo.py`'s `cpp`/`cmake` modes cover only SDK-free host smoke tests;
  real camera/NPU demos that link `sscma-micro` should be built inside the
  `sscma-example-sg200x` SDK tree.
- `new_demo.py --live udp` only generates the transport skeleton
  (`live.implemented=false`); camera and inference code are wired in per
  [references/tools/device-pipeline.md](references/tools/device-pipeline.md),
  and until then the UI clearly shows "demo skeleton".

## Maintenance and self-check

```bash
python3 -m pytest tests -q                     # offline regression
python3 scripts/check_knowledge.py             # structure/links/contracts/locks/secrets
python3 scripts/check_knowledge.py --online    # upstream wiki branch drift
python3 scripts/check_knowledge.py --update-lock
```

Maintenance rules:

- Any script change must be accompanied by updates to `tests/` and
  `references/scripts.md`.
- Any device fact change must be measured first, then reflected in
  `configs/device-contract.json`'s `os.minimum` / `tested`.
- Any schema change must be synchronized across `schemas/`, `templates/`, and
  the corresponding tests.
- No password or private key may ever appear in the repository, scripts,
  docs, or reports.
- Adding a new device means creating a new knowhub and registering it in
  `SKILL.md`.

## License and third parties

The code, scripts, templates, and original documentation in this repository
are licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).

- The knowledge base distills paths, commands, and original summaries from the
  Seeed reCamera wiki (GPL-3.0); upstream prose is not distributed with the
  repository. See [NOTICE](NOTICE) for details.
- `templates/live-ui/vendor/jmuxer.min.js` is MIT; its license is at
  `templates/live-ui/vendor/LICENSE.jmuxer`.
- TPU-MLIR, firmware, and `.cvimodel` model files are not distributed with
  this repository.

---

**Seeed Studio** · reCamera 2002 C++ Dev Toolkit · v0.1.1
