#!/usr/bin/env python3
"""One-shot launcher: deploy a live demo and start the host viewer.

This is the command that turns a prepared live demo workspace into the
user-facing experience:

    $ python3 scripts/launch_demo.py --demo-dir ~/reCamera_demo/<demo> \
        --host 192.168.42.1 --allow-actions
    LIVE_URL=http://<host-ip>:8090/?token=...

It validates/builds/smoke-tests the workspace, installs and starts the demo on
the reCamera, starts ``serve_demo.py`` as a detached host process, waits for
health (and optionally the first video frame), then prints the clickable URL
and exits. The host service keeps running after this script returns.

Use ``--stop`` / ``--status`` to manage the service later. Live viewing is not
evidence; use ``capture_evidence.py --source live`` for that.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, now_iso, read_json, redact, write_json  # noqa: E402
from devkit.live import DEFAULT_CONTROL_PORT, DEFAULT_VIDEO_PORT  # noqa: E402
from serve_demo import _local_ip_for  # noqa: E402

SCRIPTS = SKILL_ROOT / "scripts"
DEFAULT_HTTP_PORT = 8090
SERVER_STATE = "server.json"
LAUNCH_STATE = "launch.json"
SERVER_LOG = "server.log"
_SHOW_PROGRESS = True
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _live_dir(demo_dir: Path) -> Path:
    return demo_dir / "dist" / "live"


def _state_path(demo_dir: Path) -> Path:
    return _live_dir(demo_dir) / SERVER_STATE


def _launch_path(demo_dir: Path) -> Path:
    return _live_dir(demo_dir) / LAUNCH_STATE


def _log_path(demo_dir: Path) -> Path:
    return _live_dir(demo_dir) / SERVER_LOG


def _write_private_json(path: Path, payload: Any) -> None:
    write_json(path, payload)
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if user:
            try:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json_or_none(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _url(host: str, port: int, token: str) -> str:
    base = f"http://{host}:{port}/"
    return f"{base}?token={quote(token, safe='')}" if token else base


def _public_url(host: str, port: int, token: str) -> str:
    """User-facing URL: DEMO_PUBLIC_BASE overrides the LAN address."""
    base = (os.environ.get("DEMO_PUBLIC_BASE") or "").strip().rstrip("/")
    if base:
        return f"{base}/?token={quote(token, safe='')}" if token else f"{base}/"
    return _url(host, port, token)


def _is_loopback(bind: str) -> bool:
    return bind in ("127.0.0.1", "localhost", "::1")


def _check_host(bind: str) -> str:
    return "127.0.0.1" if bind in ("0.0.0.0", "::") else bind


def _free_tcp_port(bind: str, preferred: int) -> int:
    def try_bind(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((bind if not _is_loopback(bind) else "127.0.0.1", port))
            return True
        except OSError:
            return False

    if preferred and try_bind(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind if not _is_loopback(bind) else "127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _free_udp_port(bind: str, preferred: int) -> int:
    host = "127.0.0.1" if _is_loopback(bind) else bind

    def try_bind(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind((host, port))
            return True
        except OSError:
            return False

    if preferred and try_bind(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _resolve_live_ports(manifest: dict[str, Any], args: argparse.Namespace) -> tuple[int, int]:
    live = manifest.get("live") if isinstance(manifest.get("live"), dict) else {}
    udp = live.get("udp") if isinstance(live.get("udp"), dict) else {}
    udp_port = args.udp_port if args.udp_port is not None else int(
        udp.get("video_port") or DEFAULT_VIDEO_PORT)
    control_port = args.control_port if args.control_port is not None else int(
        udp.get("control_port") or DEFAULT_CONTROL_PORT)
    return udp_port, control_port


def _detach_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        flags = 0
        for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
            flags |= int(getattr(subprocess, name, 0))
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in proc.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _http_json(url: str, *, token: str = "", timeout: float = 2.0,
               method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if token:
        url += ("&" if "?" in url else "?") + "token=" + quote(token, safe="")
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with _LOCAL_OPENER.open(request, timeout=timeout) as response:
        body = response.read()
    value = json.loads(body.decode("utf-8")) if body else {}
    return value if isinstance(value, dict) else {}


def _health(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        _http_json(f"http://{host}:{port}/api/health", timeout=timeout)
        return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _server_state(port: int, token: str, host: str = "127.0.0.1") -> dict[str, Any] | None:
    try:
        return _http_json(f"http://{host}:{port}/api/state", token=token, timeout=2.0)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _wait_health(port: int, timeout: float, host: str = "127.0.0.1") -> bool:
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        if _health(port, host):
            return True
        time.sleep(0.2)
    return False


def _wait_video(port: int, token: str, timeout: float,
                host: str = "127.0.0.1") -> dict[str, Any] | None:
    deadline = time.time() + max(0.0, timeout)
    last: dict[str, Any] | None = None
    while time.time() <= deadline:
        state = _server_state(port, token, host)
        if state is not None:
            last = state
            video = state.get("video")
            if isinstance(video, dict) and int(video.get("frames") or 0) > 0:
                return state
        time.sleep(0.5)
    return last


def _wait_pipeline(port: int, token: str, timeout: float,
                   host: str = "127.0.0.1") -> dict[str, Any] | None:
    """Collect enough meta samples for the demo.json ``live.perf`` gate.

    Returns as soon as the pipeline is measured (ok or not), or immediately
    when the demo has no perf thresholds.  A timeout still returns the last
    state so launch can report "unmeasured" instead of blocking.
    """
    deadline = time.time() + max(0.0, timeout)
    last: dict[str, Any] | None = None
    while time.time() <= deadline:
        state = _server_state(port, token, host)
        if state is not None:
            last = state
            pipeline = state.get("pipeline") or {}
            if not pipeline.get("enforced", False):
                return state
            if pipeline.get("ok") is False or pipeline.get("measured", False):
                return state
        time.sleep(0.5)
    return last


def _show_cmd(cmd: list[Any]) -> str:
    """Render a command for the terminal without leaking --token values."""
    parts = [str(part) for part in cmd]
    shown: list[str] = []
    hide_next = False
    for part in parts:
        if hide_next:
            shown.append("***")
            hide_next = False
            continue
        if part.startswith("--token="):
            shown.append("--token=***")
            continue
        shown.append(redact(part))
        if part == "--token":
            hide_next = True
    return " ".join(shown)


def _run(cmd: list[str], *, env: dict[str, str] | None = None,
         timeout: int = 900, cwd: Path | None = None) -> tuple[int, str, str]:
    if _SHOW_PROGRESS:
        print(f"+ {_show_cmd(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def _manifest(demo_dir: Path) -> dict[str, Any]:
    path = demo_dir / "demo.json"
    if not path.is_file():
        raise ValueError(f"demo.json not found: {path}")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError("demo.json must be an object")
    return manifest


def _offline(demo_dir: Path, args: argparse.Namespace) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if not args.no_validate:
        code, out, err = _run([sys.executable, str(SCRIPTS / "validate_demo.py"),
                               "--demo-dir", str(demo_dir)], timeout=120)
        if code != 0:
            return False, [f"validate failed: {_tail(out, err)}"]
    if not args.no_build:
        manifest = _manifest(demo_dir)
        binary = demo_dir / "dist" / str(manifest.get("build", {}).get("binary", ""))
        if args.rebuild or not binary.is_file():
            code, out, err = _run([sys.executable, str(SCRIPTS / "build_demo.py"),
                                   "--demo-dir", str(demo_dir)], timeout=900)
            if code != 0:
                return False, [f"build failed: {_tail(out, err)}"]
    if not args.no_smoke:
        code, out, err = _run([sys.executable, str(SCRIPTS / "smoke_demo.py"),
                               "--demo-dir", str(demo_dir)], timeout=180)
        if code == 2:
            warnings.append(f"smoke not run: {_tail(out, err)}")
        elif code != 0:
            warnings.append(f"smoke failed: {_tail(out, err)}")
    if args.package:
        code, out, err = _run([sys.executable, str(SCRIPTS / "package_demo.py"),
                               "--demo-dir", str(demo_dir)], timeout=300)
        if code != 0:
            warnings.append(f"package failed: {_tail(out, err)}")
    return True, warnings


def _tail(stdout: str, stderr: str, limit: int = 600) -> str:
    text = (stderr or stdout or "").strip()
    return text[-limit:] if text else "no output"


def _deploy(demo_dir: Path, args: argparse.Namespace, peer: str) -> tuple[bool, list[str]]:
    if args.no_deploy:
        return True, ["deploy skipped (--no-deploy)"]
    warnings: list[str] = []
    host = args.host
    env = dict(os.environ)
    env["LIVE_PEER"] = peer
    env["LIVE_ARGS"] = f"--port {args.udp_port} --control-port {args.control_port}"
    probe = _live_dir(demo_dir) / "probe.json"
    code, out, err = _run([sys.executable, str(SCRIPTS / "probe_device.py"),
                           "--host", host, "--report", str(probe)], env=env, timeout=120)
    if code != 0:
        warnings.append(f"probe failed: {_tail(out, err)}")
    code, out, err = _run([sys.executable, str(SCRIPTS / "deploy_demo.py"),
                           "--demo-dir", str(demo_dir), "--host", host,
                           "--action", "install", "--replace"], env=env, timeout=900)
    if code != 0:
        return False, warnings + [f"install failed: {_tail(out, err)}"]
    code, out, err = _run([sys.executable, str(SCRIPTS / "deploy_demo.py"),
                           "--demo-dir", str(demo_dir), "--host", host,
                           "--action", "start",
                           *(["--allow-service-stop"]
                             if getattr(args, "allow_service_stop", False) else [])],
                          env=env, timeout=300)
    if code != 0:
        return False, warnings + [f"start failed: {_tail(out, err)}"]
    if args.verify:
        code, out, err = _run([sys.executable, str(SCRIPTS / "deploy_demo.py"),
                               "--demo-dir", str(demo_dir), "--host", host,
                               "--action", "verify"], env=env, timeout=120)
        if code != 0:
            warnings.append(f"verify failed: {_tail(out, err)}")
    return True, warnings


def _server_command(demo_dir: Path, args: argparse.Namespace, *, port: int,
                    token: str, session_dir: Path, allow_actions: bool) -> list[str]:
    cmd = [
        sys.executable, str(SCRIPTS / "serve_demo.py"),
        "--demo-dir", str(demo_dir),
        "--device-host", args.host,
        "--bind", args.bind,
        "--http-port", str(port),
        "--udp-port", str(args.udp_port),
        "--control-port", str(args.control_port),
        f"--token={token}",
        "--session-dir", str(session_dir),
        "--quiet",
    ]
    if allow_actions:
        cmd.append("--allow-actions")
    if args.record_video:
        cmd.append("--record-video")
    if args.open:
        cmd.append("--open")
    return cmd


def _start_server(demo_dir: Path, args: argparse.Namespace, *, port: int,
                  token: str, session_dir: Path) -> tuple[int | None, list[str]]:
    log_path = _log_path(demo_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    allow_actions = args.actions and shutil.which("ssh") is not None
    warnings: list[str] = []
    if args.actions and not allow_actions:
        warnings.append("ssh not found on PATH; UI actions disabled")
    cmd = _server_command(demo_dir, args, port=port, token=token,
                          session_dir=session_dir, allow_actions=allow_actions)
    if _SHOW_PROGRESS:
        print(f"+ {_show_cmd(cmd)}")
    log_file = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(demo_dir), stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, **_detach_kwargs(),
        )
    except OSError as exc:
        log_file.close()
        return None, warnings + [f"cannot start serve_demo.py: {exc}"]
    finally:
        log_file.close()
    if not _wait_health(port, args.wait_server, _check_host(args.bind)):
        _terminate_pid(proc.pid)
        return None, warnings + [f"serve_demo.py did not become healthy; see {log_path}"]
    return proc.pid, warnings


def _terminate_pid(pid: int) -> None:
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        for _ in range(20):
            if not _pid_alive(pid):
                return
            time.sleep(0.1)


def _pid_is_serve(pid: int, demo_dir: Path) -> bool:
    """Best-effort identity check before killing a PID from a stale state file."""
    if not pid:
        return False
    text = ""
    if os.name == "nt":
        commands = [
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
        ]
        for command in commands:
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.stdout:
                text = proc.stdout
                break
    else:
        proc_path = Path(f"/proc/{pid}/cmdline")
        if proc_path.is_file():
            try:
                text = proc_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                text = ""
        if not text:
            try:
                proc = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                      capture_output=True, text=True, timeout=5)
                text = proc.stdout
            except (OSError, subprocess.SubprocessError):
                text = ""
    return "serve_demo.py" in text and str(demo_dir) in text


def _stop_server(demo_dir: Path, args: argparse.Namespace, *, quiet: bool = False) -> int:
    state = _read_json_or_none(_state_path(demo_dir))
    if not state:
        if not quiet:
            print("LIVE_STATUS=stopped")
        return 0
    port = int(state.get("http_port") or 0)
    token = str(state.get("token") or "")
    host = str(state.get("bind") or "127.0.0.1")
    check_host = _check_host(host)
    if port and _health(port, check_host):
        try:
            _http_json(f"http://{check_host}:{port}/api/shutdown", token=token,
                       method="POST", payload={}, timeout=3.0)
            for _ in range(25):
                if not _health(port, check_host):
                    break
                time.sleep(0.2)
        except (OSError, ValueError, urllib.error.URLError):
            pass
    pid = int(state.get("pid") or 0)
    if pid and _pid_alive(pid):
        if _pid_is_serve(pid, demo_dir):
            _terminate_pid(pid)
        else:
            print(f"LIVE_WARNING=pid {pid} is not the expected serve_demo process; not killing",
                  file=sys.stderr)
    try:
        _state_path(demo_dir).unlink(missing_ok=True)
    except OSError:
        pass
    if not quiet:
        print("LIVE_STATUS=stopped")
    return 0


def _status(demo_dir: Path, args: argparse.Namespace) -> int:
    state = _read_json_or_none(_state_path(demo_dir))
    if not state:
        print("LIVE_STATUS=stopped")
        return 1
    port = int(state.get("http_port") or 0)
    token = str(state.get("token") or "")
    bind = str(state.get("bind") or "127.0.0.1")
    check_host = _check_host(bind)
    if not port or not _health(port, check_host):
        print("LIVE_STATUS=stale")
        print(f"LIVE_PID={state.get('pid', '')}")
        return 1
    live_state = _server_state(port, token, check_host) or {}
    url = str(state.get("url") or "")
    print(f"LIVE_STATUS=running")
    print(f"LIVE_URL={url}")
    print(f"LIVE_PID={state.get('pid', '')}")
    print(f"LIVE_VIDEO_FRAMES={((live_state.get('video') or {}).get('frames', 0))}")
    print(f"LIVE_IMPLEMENTED={str(bool(live_state.get('implemented', False))).lower()}")
    return 0


def _server_state_payload(
    demo_dir: Path,
    args: argparse.Namespace,
    *,
    pid: int,
    port: int,
    token: str,
    url: str,
    local_url: str,
    session_dir: Path,
    peer: str,
    requested_udp: int,
    requested_control: int,
) -> dict[str, Any]:
    return {
        "pid": pid,
        "http_port": port,
        "udp_port": args.udp_port,
        "control_port": args.control_port,
        "requested_udp_port": requested_udp,
        "requested_control_port": requested_control,
        "bind": args.bind,
        "token": token,
        "url": url,
        "local_url": local_url,
        "session_dir": str(session_dir),
        "demo_dir": str(demo_dir),
        "device_host": args.host,
        "peer": peer,
        "started_at": now_iso(),
        "log": str(_log_path(demo_dir)),
    }


def _launch_state(demo_dir: Path, args: argparse.Namespace, *, url: str,
                  local_url: str, token: str, port: int, pid: int,
                  session_dir: Path, peer: str, video_state: dict[str, Any] | None,
                  deploy_ok: bool, warnings: list[str]) -> dict[str, Any]:
    video = (video_state or {}).get("video") or {}
    pipeline = (video_state or {}).get("pipeline") or {}
    implemented = bool((video_state or {}).get("implemented", False))
    frames = int(video.get("frames") or 0)
    ingest_error = str((video_state or {}).get("ingest_error") or "")
    observed = bool((video_state or {}).get("implemented_observed", frames > 0))
    if ingest_error:
        warnings.append(ingest_error)
    if ingest_error or not deploy_ok:
        status = "degraded"
    elif frames > 0:
        status = "ready"
        if not implemented:
            warnings.append(
                "live.implemented=false but video frames were observed; set it to true")
    elif implemented:
        status = "degraded"
    else:
        status = "placeholder"
    if status == "ready" and pipeline.get("enforced") and pipeline.get("ok") is False:
        status = "degraded"
        for check in pipeline.get("checks") or []:
            if not check.get("ok"):
                warnings.append(
                    f"perf {check.get('name')}={check.get('value')} "
                    f"(limit {check.get('limit')})")
    return {
        "schema_version": 1,
        "kind": "demo-launch",
        "generated_at": now_iso(),
        "status": status,
        "url": url,
        "local_url": local_url,
        "host": args.host,
        "peer": peer,
        "http_port": port,
        "udp_port": args.udp_port,
        "control_port": args.control_port,
        "token": token,
        "pid": pid,
        "demo_dir": str(demo_dir),
        "session_dir": str(session_dir),
        "video_frames": frames,
        "implemented": implemented,
        "implemented_observed": observed,
        "deploy_ok": deploy_ok,
        "ingest_error": ingest_error,
        "pipeline": pipeline,
        "warnings": warnings,
    }


def _print_result(result: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"LIVE_URL={result['url']}")
    print(f"LIVE_LOCAL_URL={result['local_url']}")
    print(f"LIVE_STATUS={result['status']}")
    print(f"LIVE_SESSION={result['session_dir']}")
    print(f"LIVE_VIDEO_FRAMES={result['video_frames']}")
    print(f"LIVE_IMPLEMENTED={str(result['implemented']).lower()}")
    print(f"LIVE_IMPLEMENTED_OBSERVED={str(result.get('implemented_observed', False)).lower()}")
    pipeline = result.get("pipeline") or {}
    if pipeline:
        if pipeline.get("result_age_ms") is not None:
            print(f"LIVE_RESULT_AGE_MS={pipeline.get('result_age_ms')}")
        if pipeline.get("detect_fps") is not None:
            print(f"LIVE_DETECT_FPS={pipeline.get('detect_fps')}")
        if pipeline.get("enforced"):
            verdict = ("ok" if pipeline.get("ok") is True else
                       "degraded" if pipeline.get("ok") is False else "unmeasured")
            print(f"LIVE_PERF={verdict}")
    for warning in result.get("warnings") or []:
        print(f"LIVE_WARNING={warning}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo-dir", type=Path, required=True,
                        help="prepared live demo workspace (must contain demo.json with live)")
    parser.add_argument("--host", default=os.environ.get("RECAMERA_HOST", "192.168.42.1"), help="reCamera IP (default: $RECAMERA_HOST)")
    parser.add_argument("--peer", help="host IP the device should send UDP to (default: auto)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="host HTTP bind (default 127.0.0.1; use 0.0.0.0 for LAN)")
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("DEMO_HTTP_PORT", "8090")), help="host UI port (default: 8090 / $DEMO_HTTP_PORT)")
    parser.add_argument("--udp-port", type=int, default=None)
    parser.add_argument("--control-port", type=int, default=None)
    parser.add_argument("--token", help="UI token (default: reuse or generate)")
    parser.add_argument("--session-dir", type=Path, help="override session output directory")
    parser.add_argument("--actions", dest="actions", action="store_true", default=False,
                        help="enable UI deploy/start/stop actions (default: disabled)")
    parser.add_argument("--no-actions", dest="actions", action="store_false",
                        help="disable UI actions (default)")
    parser.add_argument("--allow-actions", dest="actions", action="store_true",
                        help="alias for --actions")
    parser.add_argument("--allow-service-stop", action="store_true",
                        help="allow deploy to stop Node-RED/Supervisor (also stops the device Web UI)")
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument("--package", action="store_true", help="also build the reproducible archive")
    parser.add_argument("--no-deploy", action="store_true", help="only start the host UI")
    parser.add_argument("--verify", action="store_true", help="run deploy_demo verify after start")
    parser.add_argument("--record-video", action="store_true")
    parser.add_argument("--open", action="store_true", help="open the UI on this host")
    parser.add_argument("--wait", type=float, default=15.0, help="seconds to wait for first video")
    parser.add_argument("--perf-wait", type=float, default=3.0,
                        help="seconds to collect live.perf samples (default 3)")
    parser.add_argument("--no-perf-gate", action="store_true",
                        help="skip the live.perf result-age/update-rate gate")
    parser.add_argument("--wait-server", type=float, default=10.0)
    parser.add_argument("--restart", action="store_true", help="stop an existing service first")
    parser.add_argument("--stop", action="store_true", help="stop the host service and exit")
    parser.add_argument("--status", action="store_true", help="print host service status and exit")
    parser.add_argument("--json", action="store_true", help="machine-readable final result")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _SHOW_PROGRESS
    args = parse_args(argv)
    _SHOW_PROGRESS = not args.json
    demo_dir = args.demo_dir.expanduser().resolve()
    try:
        if args.status:
            return _status(demo_dir, args)
        if args.stop:
            return _stop_server(demo_dir, args)
        if not demo_dir.is_dir():
            print(f"ERROR: demo directory not found: {demo_dir}", file=sys.stderr)
            return 2
        manifest = _manifest(demo_dir)
        live = manifest.get("live")
        if not isinstance(live, dict):
            print("ERROR: demo.json has no live section; scaffold with "
                  "`new_demo.py --live udp` or add the live transport.", file=sys.stderr)
            return 2

        state = _read_json_or_none(_state_path(demo_dir))
        carry_warnings: list[str] = []
        state_host = "127.0.0.1"
        if state:
            state_host = _check_host(str(state.get("bind") or "127.0.0.1"))
        if state and not args.restart and _health(int(state.get("http_port") or 0), state_host):
            want_udp, want_control = _resolve_live_ports(manifest, args)
            state_udp = int(state.get("requested_udp_port", state.get("udp_port") or 0))
            state_control = int(state.get("requested_control_port", state.get("control_port") or 0))
            token_mismatch = bool(args.token and args.token != state.get("token"))
            host_mismatch = str(state.get("device_host") or "") != args.host
            bind_mismatch = str(state.get("bind") or "") != args.bind
            ports_mismatch = state_udp != want_udp or state_control != want_control
            if not (token_mismatch or host_mismatch or bind_mismatch or ports_mismatch):
                url = str(state.get("url") or "")
                if args.json:
                    print(json.dumps({
                        "schema_version": 1, "kind": "demo-launch",
                        "status": "reused", "url": url,
                        "local_url": state.get("local_url", ""),
                        "http_port": state.get("http_port"),
                        "udp_port": state.get("udp_port"),
                        "control_port": state.get("control_port"),
                        "token": state.get("token", ""),
                        "demo_dir": str(demo_dir),
                        "session_dir": state.get("session_dir", ""),
                        "device_host": state.get("device_host", ""),
                        "warnings": [],
                    }, ensure_ascii=False))
                else:
                    print(f"LIVE_URL={url}")
                    print("LIVE_STATUS=reused")
                return 0
            reasons = []
            if ports_mismatch:
                reasons.append(f"requested ports {state_udp}/{state_control} -> "
                               f"{want_udp}/{want_control}")
            if host_mismatch:
                reasons.append(f"device {state.get('device_host')} -> {args.host}")
            if bind_mismatch:
                reasons.append(f"bind {state.get('bind')} -> {args.bind}")
            if token_mismatch:
                reasons.append("token changed")
            carry_warnings.append("live config changed (" + "; ".join(reasons) +
                                  "); restarting host service")
            if args.no_deploy:
                carry_warnings.append(
                    "device demo was not redeployed; restart it with the new peer/ports")
            _stop_server(demo_dir, args, quiet=True)
        elif state and args.restart:
            _stop_server(demo_dir, args, quiet=True)

        ok, warnings = _offline(demo_dir, args)
        warnings = carry_warnings + warnings
        if not ok:
            for warning in warnings:
                print(f"ERROR: {warning}", file=sys.stderr)
            return 2

        udp_port, control_port = _resolve_live_ports(manifest, args)
        chosen_udp = _free_udp_port("0.0.0.0", udp_port)
        if chosen_udp != udp_port:
            warnings.append(f"UDP port {udp_port} is busy; using {chosen_udp}")
        args.udp_port = chosen_udp
        args.control_port = control_port

        token = args.token or (str(state.get("token")) if state and state.get("token") else "")
        if not token:
            token = secrets.token_urlsafe(12)
        port = _free_tcp_port(args.bind, args.http_port or _preferred_port(state))
        peer = args.peer or _local_ip_for(args.host)
        session_dir = (args.session_dir or
                       demo_dir / "eval" / "live" / now_iso().replace(":", "").replace("-", ""))
        session_dir = session_dir.expanduser().resolve()

        url_host = "127.0.0.1" if _is_loopback(args.bind) else peer
        url = _public_url(url_host, port, token)
        local_url = _url("127.0.0.1", port, token)

        pid, server_warnings = _start_server(
            demo_dir, args, port=port, token=token, session_dir=session_dir,
        )
        warnings.extend(server_warnings)
        if not pid:
            for warning in warnings:
                print(f"ERROR: {warning}", file=sys.stderr)
            return 2
        # Persist state before deploy/wait so --stop still works after Ctrl+C.
        state_payload = _server_state_payload(
            demo_dir, args, pid=pid, port=port, token=token, url=url,
            local_url=local_url, session_dir=session_dir, peer=peer,
            requested_udp=udp_port, requested_control=control_port,
        )
        _write_private_json(_state_path(demo_dir), state_payload)

        deploy_ok = True
        deploy_warnings: list[str] = []
        if not args.no_deploy and not os.environ.get("RECAMERA_PASSWORD"):
            warnings.append("RECAMERA_PASSWORD is unset; deploy relies on an SSH key")
        if not args.no_deploy:
            deploy_ok, deploy_warnings = _deploy(demo_dir, args, peer)
            warnings.extend(deploy_warnings)

        video_state = _wait_video(port, token, args.wait, _check_host(args.bind))
        if video_state is not None and not args.no_perf_gate:
            perf_state = _wait_pipeline(port, token, args.perf_wait, _check_host(args.bind))
            if perf_state is not None:
                video_state = perf_state
        result = _launch_state(
            demo_dir, args, url=url, local_url=local_url, token=token, port=port,
            pid=pid, session_dir=session_dir, peer=peer, video_state=video_state,
            deploy_ok=deploy_ok, warnings=warnings,
        )
        state_payload["last_status"] = result["status"]
        state_payload["last_checked_at"] = now_iso()
        _write_private_json(_state_path(demo_dir), state_payload)
        _write_private_json(_launch_path(demo_dir), result)
        _print_result(result, args)
        if args.debug:
            print(f"launch state: {_launch_path(demo_dir)}")
            print(f"server log:   {_log_path(demo_dir)}")
        return 0 if result["status"] in ("ready", "placeholder") else 1
    except KeyboardInterrupt:
        print("interrupted; host service (if started) keeps running", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        if args.debug:
            raise
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _preferred_port(state: dict[str, Any] | None) -> int:
    if state and state.get("http_port"):
        return int(state["http_port"])
    return DEFAULT_HTTP_PORT


if __name__ == "__main__":
    raise SystemExit(main())