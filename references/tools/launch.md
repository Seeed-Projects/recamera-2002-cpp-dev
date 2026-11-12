# One-command launch: `launch_demo.py`

Turns a prepared live demo workspace into the experience the user asked for:

```text
python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
  --host 192.168.42.1 --bind 0.0.0.0 --allow-actions --allow-service-stop
```

Final output:

```text
LIVE_URL=http://<host-ip>:8090/?token=...
LIVE_LOCAL_URL=http://127.0.0.1:8090/?token=...
LIVE_STATUS=ready
LIVE_SESSION=<demo-dir>/eval/live/<timestamp>
LIVE_VIDEO_FRAMES=42
LIVE_IMPLEMENTED=true
LIVE_IMPLEMENTED_OBSERVED=true
LIVE_RESULT_AGE_MS=187.5
LIVE_DETECT_FPS=5.1
LIVE_PERF=ok
```

`--status` outputs `LIVE_STATUS=running|stale|stopped`; `--stop` outputs
`LIVE_STATUS=stopped`. When reusing an already-healthy service it outputs only:

```text
LIVE_URL=http://...
LIVE_STATUS=reused
```

The calling agent (Claude Code, Codex, etc.) should base its final reply on `LIVE_URL` (see the output contract in `SKILL.md`).
If the host has multiple NICs and `LIVE_URL` is not on the subnet your browser is in, replace the host part with an
address on that subnet (the port and token stay the same; `--bind 0.0.0.0` is required to allow LAN access).

## What it does

1. Validates that `demo.json` has a `live` section.
2. Offline: `validate_demo` → `build_demo` if needed → `smoke_demo` (skippable).
3. Starts `serve_demo.py` in the background on the host (detached; it keeps running after the terminal exits).
4. Computes the host address the device should send back to (the NIC matching `--host`) and injects it via `LIVE_PEER`.
5. `probe_device` → `deploy_demo install --replace` → `deploy_demo start` (including that authorization only when
   `--allow-service-stop` is passed explicitly).
6. Waits for `/api/health`, then waits for the first video frame (`--wait` seconds).
7. Writes `dist/live/server.json` and `dist/live/launch.json`, prints the URL, and exits.

The host service and the device demo keep running; the user opens the URL to see the video/logs/parameter tuning.

> `launch_demo` does not generate camera/detection code. The demo must already have a `camera_pipeline` and set
> `live.implemented=true`; otherwise the URL only shows logs/parameters and `LIVE_STATUS=placeholder`.
> For camera pipeline wiring see [device-pipeline.md](device-pipeline.md).

## Parameters

| Parameter | Description |
|---|---|
| `--demo-dir` | Required; an implemented live demo workspace |
| `--host` | Device IP, default `192.168.42.1` |
| `--peer` | Host IP the device sends UDP back to; by default picks the local NIC address along the route to the device |
| `--bind` | HTTP bind, default `127.0.0.1` (local only); use `0.0.0.0` explicitly for LAN viewing |
| `--http-port` | Default 8090; switches to a free port automatically if taken |
| `--udp-port` / `--control-port` | Defaults from `demo.json`'s `live.udp.{video_port,control_port}`, otherwise 5000 / 5001; if the host UDP ports are taken it switches ports automatically and notifies the device via `LIVE_ARGS` |
| `--token` | By default reuses or generates one; the URL carries the token |
| `--session-dir` | Default `<demo-dir>/eval/live/<timestamp>` |
| `--actions` / `--no-actions` | Whether the UI allows install/start/stop actions (off by default, requires `ssh`); `--allow-actions` is a compatibility alias for `--actions` |
| `--allow-service-stop` | Allows deploy to stop Node-RED/Supervisor; off by default — stopping them also takes down the device Web UI and Studio |
| `--no-validate` / `--no-build` / `--no-smoke` / `--package` | Skip or add offline stages |
| `--no-deploy` | Start only the host UI, do not touch the device |
| `--verify` | Append `deploy_demo verify` after start |
| `--wait` | Seconds to wait for the first frame, default 15 |
| `--perf-wait` | Seconds to collect `live.perf` samples after the first frame, default 3 |
| `--no-perf-gate` | Skip result-age/update-rate checks |
| `--restart` | Stop an existing host service before starting |
| `--open` | Open a browser on the host |
| `--json` | Print one final JSON line (for agent parsing) |
| `--status` / `--stop` | Inspect/stop the host service |

## Status meanings

| `LIVE_STATUS` | Meaning |
|---|---|
| `ready` | Service healthy, device started, video frames received (the manifest's `implemented` may still be false, in which case a warning is attached; the UI uses `implemented_observed` to hide the skeleton notice) |
| `placeholder` | Service healthy, no video frames, and `live.implemented=false` (transport skeleton/logs only) |
| `degraded` | Service healthy, but deploy failed, the wait timed out with no video, or `live.perf` missed its thresholds (result age/update rate/video fps) |
| `reused` | Reused a running service for the same directory |
| `stopped` / `stale` | Not running / process dead |

`degraded` and `placeholder` still print the URL: the user can read logs in the UI and click "Start" to retry.
When `degraded`, `LIVE_WARNING` carries the specific reason (UDP ingest error, probe/install/start failure,
first-frame wait timeout, `perf result_age_ms=...`, etc.). **Never** report `degraded` as success,
and never treat the live view as evidence.

`LIVE_PERF=unmeasured` means too few samples or `live.perf` not declared; it must not be treated as passing.
The performance gate only looks at the device-reported `meta.age_ms` (frame capture → send), not host clock differences.

## Host service lifecycle

- POSIX: `Popen(start_new_session=True)`, detached from the terminal.
- Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`.
- State lives in `dist/live/server.json` (token file permissions 600); logs in `dist/live/server.log`.
  On Windows restrict with `icacls /inheritance:r /grant:r <user>:F` to the current user;
  if `icacls` is unavailable the token is still protected by the user profile ACL, but do not share that directory.
- On Windows `serve_demo.py` runs, but `deploy_demo.py`'s password authentication relies on
  OpenSSH's `SSH_ASKPASS`; the existing helper is a POSIX shell script that native Windows
  OpenSSH cannot execute. **On Windows use SSH key authentication**, or install `sshpass` and set
  `DEMO_SSH_AUTH=sshpass`; otherwise the UI's deployment actions will report
  "Windows OpenSSH cannot execute …" explicitly rather than a mysterious install failed.
- `--stop` calls `/api/shutdown` first and falls back to killing by PID.
- `--stop` does not stop the demo on the device; when needed use
  `deploy_demo.py --action stop` (which restores the services in `services_start`).

## Relationship to evidence

`launch_demo` is only for "watching". To keep evidence:

```bash
python3 scripts/capture_evidence.py --source live \
  --session <LIVE_SESSION> --out <demo>/eval/recamera/evidence
```

The live UI is not `device-run` / `task-quality` evidence; report writing still follows
[evidence.md](../evidence.md).

## Lane markings / custom detection

`launch_demo` will not conjure detection algorithms out of thin air. The demo must produce detection boxes or
`annotations` (polylines/polygons, etc., see `templates/live/README.md`) itself.
When `live.implemented` is `false`, the UI shows a "demo skeleton" notice and `LIVE_STATUS=placeholder`.

If an existing demo only does **directional-arrow classification** (offline batch), that is not lane marking detection;
do not present it directly as a finished "parking lot lane marking detection". Real lane marking detection needs
a model (ONNX→cvimodel; the current TPU-MLIR environment is unverified) or a classic CV implementation, and
measured results on the device. Without those, the honest output is `placeholder` or `degraded`, not a faked success.
