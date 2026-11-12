# Scaffolding: `new_demo.py`

Generates a workspace from `templates/` that passes offline validation out of the box.

```bash
python3 scripts/new_demo.py --name fall_detection --task pose --out ~/reCamera_demo
python3 scripts/new_demo.py --name hello --kind cmake --out ~/reCamera_demo
python3 scripts/new_demo.py --name vendor --kind prebuilt --artifact ./demo.riscv
```

## Parameters

| Parameter | Description |
|---|---|
| `--name` | Required, `^[a-z0-9][a-z0-9_-]{1,63}$`; also the remote directory name |
| `--kind` | `cpp` (default) / `cmake` / `script` / `prebuilt` |
| `--task` | Taken from `configs/evaluation-contract.json`, default `custom` |
| `--version` | Default `1.0.0` |
| `--out` | Default `~/reCamera_demo`; the actual directory is `<out>/<name>` |
| `--artifact` | Required for `prebuilt`; copied into `assets/` with its sha256 recorded |
| `--live` | `none` (default) / `udp`; only for `cpp`/`cmake`; adds the H.264 UDP transport, the parameter listener, and the host-side viewer manifest |
| `--force` | Allows overwriting existing template files |

## Generated contents

```text
<name>/
├── demo.json            # name/binary/version/task already rendered
├── src/main.c           # hello + --self-test skeleton
├── smoke.sh             # compiles on the host and runs --self-test
├── README.md
├── .gitignore
└── eval/input/manifest.json   # dataset provenance placeholder; must be filled before acceptance
```

After generation it automatically runs `validate_demo.py --mode demo`. On failure the files are kept; fix per the errors.

`--live udp` additionally generates:

```text
src/live_udp.h / live_udp.cpp   # UDP fragmentation, H.264/detection/log/snapshot, control plane
src/main.c                      # --live-self-test skeleton (replace with the camera pipeline)
smoke.sh                        # host compile + self-test + loopback transport test
LIVE.md                         # SSCMA H.264 channel wiring
demo.json                       # live section, services_stop, long_running
```

Viewing from the host:

```bash
python3 scripts/launch_demo.py --demo-dir <dir> --host 192.168.42.1 --allow-actions
# or start only the host UI
python3 scripts/serve_demo.py --demo-dir <dir> --device-host 192.168.42.1 --open
```

Generation sets `live.implemented=false`. Replace the placeholder loop in `src/main.cpp` and wire up the real
camera/detection, then change it to `true`; otherwise the UI shows a "demo skeleton" and `launch_demo` reports
`placeholder`.

## Next steps

```bash
python3 scripts/build_demo.py   --demo-dir <dir>
python3 scripts/smoke_demo.py   --demo-dir <dir>
python3 scripts/package_demo.py --demo-dir <dir>
```

When replacing the hello logic in `src/main.c` with real functionality, **keep the `--self-test` branch**,
otherwise `smoke_demo.py` has no executable verification path in a device-free environment.
