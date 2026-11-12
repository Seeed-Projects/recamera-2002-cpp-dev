# Deploying to the device: `probe_device.py` / `deploy_demo.py` / `diagnose_demo.py`

## Connection

- Direct IP: `192.168.42.1` (USB/RNDIS).
- SSH user: `recamera`.
- Keep the password in an environment variable only:

```bash
export RECAMERA_PASSWORD='<device password>'
```

The scripts use OpenSSH `SSH_ASKPASS` by default (the password exists only in the subprocess environment).
`DEMO_SSH_AUTH=sshpass` switches back to `sshpass -e`. With an SSH key already configured, use `--no-password`.

The host key default is `StrictHostKeyChecking=accept-new`; use `--insecure-host-key` only in test environments.

## Read-only inventory

```bash
python3 scripts/probe_device.py --host 192.168.42.1 --report dist/probe.json
```

Collects hostname/kernel/arch, OS, uptime, MAC, storage, tools, paths, init services,
processes, and the `.cvimodel` inventory, and compares them against `configs/device-contract.json`. Any error exits 1;
SSH failure exits 2.

## Deployment actions

```bash
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action upload
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action install --replace
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action start
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action verify
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action status
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action stop
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action collect
python3 scripts/deploy_demo.py --demo-dir <dir> --host 192.168.42.1 --action clean --yes
```

| Action | Behavior |
|---|---|
| `upload` | Uploads to `/userdata/.demo-stage/<name>-<ts>`, verifying each file's SHA-256 |
| `install` | Copies to `/userdata/demos/<name>`; if it already exists, `--replace` is required, and a `.bak-<ts>` backup is made first |
| `start` | Runs in the foreground, or in the background via `setsid` + `run.pid` |
| `verify` | status + `runtime.expect_stdout` markers, outputs `evidence_level: device-run` |
| `collect` | Pulls back `logs/` and the remote `demo.json` |
| `clean` | Deletes the remote demo directory and staging; requires `--yes` |

## Camera arbitration

C++ camera demos need `S03node-red`, `S91sscma-node`, and `S93sscma-supervisor` stopped.
This must be declared in `demo.json` and authorized on the command line:

```json
"runtime": {
  "requires_camera": true,
  "services_stop": ["S03node-red", "S91sscma-node", "S93sscma-supervisor"],
  "services_start": ["S03node-red", "S91sscma-node", "S93sscma-supervisor"]
}
```

```bash
python3 scripts/deploy_demo.py ... --action start --allow-service-stop
```

Stopping the supervisor takes the Web UI offline; `stop` restores services by default, and only `--no-restore` skips that.

## Host-side viewer integration

When the manifest contains a `live` section, `start`/`restart` read the `LIVE_PEER` environment variable and
automatically append `--peer <host-ip>` (skipped if `runtime.args` already has `--peer`). `LIVE_ARGS` can append
`--port` / `--control-port` (flags already present in `runtime.args` are not duplicated):

```bash
export LIVE_PEER=192.168.42.2
export LIVE_ARGS="--port 5000 --control-port 5001"
python3 scripts/deploy_demo.py ... --action start --allow-service-stop
```

`launch_demo.py` sets both variables automatically from the manifest and host port availability.

## Startup failure

```bash
python3 scripts/diagnose_demo.py --demo-dir <dir> --host 192.168.42.1 --report dist/diagnose.json
```

Read-only collection of the remote directory, `run.pid`, logs, processes, `/dev/video*`, `/dev/snd/*`, memory/disk,
and the tail of the kernel log; outputs findings and hints.

## Exit codes

- `0`: the requested action completed.
- `1`: validation, runtime markers, or quality gates failed.
- `2`: arguments, SSH, input files, or other prerequisites failed.

## Evidence limits

Passing `verify` only proves the process started and the stdout markers matched; **it does not prove the task is correct**.
Accuracy must go through the `task-quality` stage of `evaluate_demo.py`.
When the manifest contains `live` with `live.implemented=false`, verify writes an additional
`live.implemented` warning: even if the process markers PASS, you cannot claim a camera/detection feed exists.
