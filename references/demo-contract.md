# Demo Contract (`demo.json`)

`demo.json` is the single declaration file of a demo workspace. `validate_demo.py`, `build_demo.py`, and `deploy_demo.py` all treat it as the source of truth; missing or inconsistent fields fail in the offline stage instead of waiting for the device to report an error.

## Minimal structure

```json
{
  "schema_version": 1,
  "name": "hello_cpp",
  "version": "1.0.0",
  "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
  "build": {"kind": "cpp", "source_dir": "src", "sources": ["main.c"], "binary": "hello_cpp"},
  "runtime": {
    "executable": "hello_cpp",
    "args": [],
    "env": {},
    "sudo": false,
    "timeout_s": 10,
    "expect_stdout": ["hello"]
  },
  "evaluation": {"task": "custom", "thresholds": {}},
  "model": null,
  "inputs": {"camera": false, "video": false, "images": false}
}
```

## Fields

| Field | Required | Description |
|---|---|---|
| `schema_version` | Yes | Fixed `1` |
| `name` | Yes | Matches `^[a-z0-9][a-z0-9_-]{1,63}$`; also used as the remote directory name |
| `version` | Yes | Demo version; semantic versioning is not required |
| `device.model` | Yes | Must exist in `devices` in `configs/device-contract.json` |
| `device.os_min` | No | Warns only when below the `tested` version in `configs/device-contract.json` |
| `build.kind` | Yes | `cpp` / `cmake` / `prebuilt` / `script` |
| `build.source_dir` | No | Relative to the demo root, default `.` |
| `build.sources` | Required for cpp | List of source files relative to `source_dir` |
| `build.binary` | Yes | Artifact name; ends up at `dist/<binary>` |
| `build.artifact` | Required for prebuilt | Prebuilt file; `build.sha256` is optional but recommended in deliver mode |
| `build.script` | Required for script | Build script; must produce `dist/<binary>` |
| `build.include_dirs` / `lib_dirs` / `libs` / `cflags` / `ldflags` / `defines` | No | String lists |
| `build.cmake_args` | No | Extra arguments passed to cmake |
| `runtime.executable` | Yes | Relative file name; path separators are not allowed |
| `runtime.args` | No | String list; shell metacharacters (semicolons, pipes, &, $, backticks, redirects) and newlines are not allowed |
| `runtime.env` | No | Keys must match `^[A-Z_][A-Z0-9_]*$`; values must be single-line |
| `runtime.sudo` | No | Default `false`; when `true`, uses `sudo -S` with the password passed via stdin from an environment variable |
| `runtime.timeout_s` | No | 1..3600, foreground-run timeout |
| `runtime.long_running` | No | Default `false`; when `true`, starts in the background with `setsid` and writes `run.pid` |
| `runtime.requires_camera` | No | Declares exclusive camera access; deployment warns about service conflicts |
| `runtime.services_stop` / `services_start` | No | Only `allowed_service_controls` entries from `configs/device-contract.json` are allowed |
| `runtime.expect_stdout` | No | Device acceptance markers; empty warns in deliver mode |
| `runtime.files` | No | Files to upload besides the binary/model |
| `model` | No | `{file, sha256, size, task, conversion}`; deliver mode requires `sha256` |
| `model.conversion` | No | `{onnx_sha256, dataset, input_size, layout}`; missing only warns |
| `evaluation.task` | Yes | Must be a task in `configs/evaluation-contract.json` |
| `evaluation.thresholds` | No | Numbers in 0..1; override contract defaults |
| `inputs` | No | Three booleans: `camera` / `video` / `images` |
| `smoke` | No | Host smoke test: `{kind, command, fixtures, timeout_s, expect_stdout}` |
| `evidence` | No | `{required_stages: [...]}`, declares the evidence stages the delivery must cover |
| `toolchain` | No | `{lock, strict}`, declares the toolchain lock file and whether matching is strict |
| `live` | No | Host-side live-view declaration; `serve_demo.py` reads it to generate controls, see below |

The machine-checkable schema is at `schemas/demo.schema.json`; `validate_demo.py` runs the schema first,
then the semantic checks below.

## Build kinds

### `cpp`

Compiles directly with `riscv64-unknown-linux-musl-gcc/g++`:

```bash
python3 scripts/build_demo.py --demo-dir ~/reCamera_demo/<demo>
```

- `.c` files use gcc; if any `.cc/.cpp/.cxx` are present, g++ is used.
- The output must be ELF64 little-endian with `e_machine=243` (RISC-V), otherwise the build fails.
- Toolchain resolution order: `--toolchain-bin` → `TOOLCHAIN_BIN` → `$SG200X_SDK_PATH/host-tools/gcc/riscv64-linux-musl-x86_64/bin` → `~/host-tools/...` → `PATH`.

### `cmake`

```json
"build": {"kind": "cmake", "source_dir": "src", "binary": "demo", "cmake_args": ["-DWITH_OPENCV=ON"]}
```

The script sets `CC`/`CXX`/`PATH`/`SG200X_SDK_PATH`, runs `cmake -S src -B build` and `cmake --build`, then finds `binary` under `build/` and copies it to `dist/`. If upstream CMake hardcodes its own toolchain, the artifact architecture check still has the final say.

### `prebuilt`

```json
"build": {"kind": "prebuilt", "artifact": "assets/demo.riscv", "binary": "demo", "sha256": "<sha256>"}
```

`build_demo.py` copies the artifact to `dist/` and validates the architecture; a `sha256` mismatch fails immediately.

### `script`

```json
"build": {"kind": "script", "script": "build.sh", "binary": "demo"}
```

The script runs in the demo root with `SG200X_SDK_PATH`, `PATH`, and `DEMO_DIR` in the environment; it must produce `dist/<binary>` itself.

## Runtime and safety rules

- Runtime library paths default to `runtime_env` in `device-contract.json` and can be overridden by `runtime.env`.
- `services_stop` only allows `S03node-red`, `S91sscma-node`, and `S93sscma-supervisor`, and requires an explicit `--allow-service-stop` at execution time; stopping the supervisor takes the Web UI offline, so restore it after testing.
- `deploy_demo.py` only kills the process recorded in its own `run.pid`; it never `pkill`s by process name.
- The remote directory is pinned to `/userdata/demos/<name>`; `--remote-dir` may only be used under that prefix, and `clean` requires `--yes`.
- An existing installation is backed up and replaced only with `--replace`.

## Validation modes

```bash
python3 scripts/validate_demo.py --demo-dir <dir>                 # demo: correct structure is enough
python3 scripts/validate_demo.py --demo-dir <dir> --mode deliver  # deliver: stricter - model hashes, acceptance markers, artifacts
python3 scripts/validate_demo.py --demo-dir <dir> --mode publish  # publish: README, model provenance, version
```

Exit codes: `0` pass (warnings possible), `1` errors present. Items such as `runtime_preconditions_unverified` are listed in the report and mean real-device measurement is still required.

## Host smoke test (`smoke`)

```json
"smoke": {
  "kind": "host-logic",
  "command": ["sh", "smoke.sh"],
  "fixtures": ["eval/input/images"],
  "timeout_s": 15,
  "expect_stdout": ["self-test ok"]
}
```

- `kind`: `host-logic` / `mock` / `none`.
- `command` is an argv list that does not go through a shell; metacharacters cause rejection.
- `fixtures` are paths relative to the demo root, exposed at run time via `DEMO_FIXTURES`.
- Without a `smoke` section, `smoke_demo.py` records `not_run` and does not fail.

## Live (host-side viewer)

`live` is an optional section, written by `new_demo.py --live udp`. The device pushes H.264 and detection
JSON over UDP, and the host `serve_demo.py` serves a fixed browser UI - no device Web UI is needed.

```json
"live": {
  "transport": "udp",
  "implemented": true,
  "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30, "bitrate_kbps": 8000},
  "udp": {"video_port": 5000, "control_port": 5001, "max_payload": 1200, "snapshot_fps": 1},
  "perf": {"max_result_age_ms": 500, "min_detect_fps": 1, "min_video_fps": 15},
  "model": {"input_width": 640, "input_height": 640,
            "preprocess": "stretch", "scale": 1.0, "pad_x": 0, "pad_y": 0},
  "params": {
    "confidence": {"type": "number", "min": 0.05, "max": 0.95, "step": 0.05,
                   "default": 0.60, "label": "confidence"},
    "max_det": {"type": "integer", "min": 1, "max": 100, "step": 1, "default": 20}
  }
}
```

| Field | Description |
|---|---|
| `live.transport` | Only `udp` is supported for now |
| `live.implemented` | `true` means the camera/detection pipeline is implemented; when `false` the UI shows a "demo skeleton" notice and `launch_demo` reports `placeholder` |
| `live.video.codec` | Only `h264` for now (`serve_demo.py` plays it via MSE) |
| `live.video.width` / `height` / `fps` | Actual video-channel output, not the model input |
| `live.video.bitrate_kbps` | Expected bitrate, used only for UI/cross-checks |
| `live.udp.video_port` | Device → host video/detection/log port |
| `live.udp.control_port` | Host → device parameter-control port |
| `live.udp.max_payload` | UDP fragment size, default 1200 |
| `live.udp.snapshot_fps` | Device JPEG snapshot rate, used for evidence; 0 means not sending (evidence capture would then fail) |
| `live.perf` | Performance gate: `max_result_age_ms` (device-side result age), `min_detect_fps` (detection update rate), `min_video_fps` (video frame rate). `serve_demo.py` judges from `meta.age_ms` and frame rates over the last 10 s; `launch_demo.py` reports `degraded` when thresholds are exceeded |
| `live.model.input_width` / `input_height` | Inference input size, e.g. 640×640 |
| `live.model.preprocess` | `stretch` / `letterbox` / `crop`, determines the inverse coordinate transform |
| `live.model.scale` / `pad_x` / `pad_y` | letterbox/crop parameters |
| `live.params` | UI control definitions: `type` (number/integer/boolean/string/enum), `min`, `max`, `step`, `default`, `label`, `enum`, `apply` |
| Custom keys in `live.params` | Known keys (confidence/nms/max_det/force_idr/lane_*, etc.) are handled as strong types by the device; other simple identifiers go into device extras (≤16), read by the demo via `get_number`/`get_bool` |

Constraints:

- When `live` is present, `runtime.requires_camera` should be `true`; otherwise `validate_demo.py` warns.
- With no `live.perf`, the performance gate is disabled; `new_demo.py --live udp` writes
  `{max_result_age_ms: 500, min_detect_fps: 1, min_video_fps: 15}` by default. The device must send
  `meta.age_ms` (the true capture→send frame age, see `FrameRelay` in `live_camera.hpp`);
  without `age_ms` the performance gate reports `unmeasured` and must not be treated as a pass.
- `runtime.services_stop` may declare stopping `S03node-red` / `S91sscma-node` / `S93sscma-supervisor`;
  execution requires an explicit `--allow-service-stop`; `stop` restores these services by default.
- Device-side coordinates stay as **normalized center points in model input space**; the UI applies
  the inverse transform to video frames per `preprocess`.
- `meta.shapes` (`annotations` is a compatibility alias) supports `polyline` / `polygon` /
  `point` / `text`; `pts` use `space:"frame"` (frame-space normalized, recommended) or
  `space:"model"` (model input space). Lane detection should send polylines instead of hard-coding
  lines as detection boxes. Budget: meta ≤ 8 KB, ≤ 16 shapes, ≤ 80 points each.
- The live UI is not evidence; capture evidence via `capture_evidence.py --source live`.

## Evidence stages (`evidence`)

```json
"evidence": {"required_stages": ["static", "offline", "host-baseline", "device-upload", "device-run"]}
```

Allowed values: `static`, `offline`, `host-baseline`, `device-upload`, `device-run`, `task-quality`.
`package_demo.py --mode publish` will not fabricate a stage just because you declared it; anything not executed stays `not_run`.

## Toolchain (`toolchain`)

```json
"toolchain": {"lock": "configs/toolchain.lock.json", "strict": true}
```

The default lock file is `configs/toolchain.lock.json`. `build_demo.py` always records the actual version;
only `--strict-toolchain` (or an explicit CI switch) makes a version mismatch fail.

## Common rejection reasons

| Error | Fix |
|---|---|
| `device.model ... not in device-contract` | Use `reCamera 2002`, or register the new device in the contract first with measured evidence |
| `build source missing` | `sources` paths are wrong relative to `source_dir` |
| `model sha256 mismatch` | Recompute `sha256sum`; don't reuse an old model hash |
| `service ... is not an allowed service control` | Stopping sshd/network services is not allowed; use an allowed camera service instead |
| `runtime.args contains a shell metacharacter` | Pass args as a list; don't build shell strings |
| `literal sshpass password` | Keep credentials in environment variables only; remove them from docs/source |
| `not an ELF file` / `e_machine=62` | Rebuild with the RISC-V musl toolchain; don't ship an x86 artifact |
