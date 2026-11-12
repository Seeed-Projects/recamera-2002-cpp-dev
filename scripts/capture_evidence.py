#!/usr/bin/env python3
"""Capture reproducible evidence for a demo run.

Sources: rtsp (ffmpeg), webui (headless Chrome screenshot + DOM), udp (official
receiver command), live (a serve_demo.py session directory), file (copy
existing artifacts). Every artifact is hashed and recorded in evidence.json; a
zero-byte artifact fails the capture.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import now_iso, sha256_file, write_json  # noqa: E402


def run(cmd: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run with a hard timeout and kill the whole process group on expiry."""
    print("+ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(cmd, 124, stdout or "", (stderr or "") + "\n[timeout]")


def tool_version(name: str) -> str:
    try:
        proc = subprocess.run([name, "-version"], capture_output=True, text=True, timeout=10)
        return (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def capture_rtsp(url: str, out: Path, duration: int, checks: list[dict]) -> list[Path]:
    video = out / "capture.mp4"
    cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url,
           "-t", str(duration), "-c", "copy", str(video)]
    proc = run(cmd, timeout=duration + 60)
    if proc.returncode != 0 or not video.is_file() or video.stat().st_size == 0:
        checks.append({"name": "rtsp.copy", "ok": False, "detail": "stream copy failed; retrying with libx264"})
        video.unlink(missing_ok=True)
        cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url,
               "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-an", str(video)]
        proc = run(cmd, timeout=duration + 120)
    checks.append({"name": "rtsp.video", "ok": proc.returncode == 0 and video.is_file() and video.stat().st_size > 0,
                   "detail": f"ffmpeg rc={proc.returncode}"})
    frame = out / "frame.jpg"
    proc = run(["ffmpeg", "-y", "-rtsp_transport", "tcp", "-ss", "1", "-i", url,
                "-frames:v", "1", str(frame)], timeout=60)
    checks.append({"name": "rtsp.frame", "ok": proc.returncode == 0 and frame.is_file() and frame.stat().st_size > 0,
                   "detail": f"ffmpeg rc={proc.returncode}"})
    return [p for p in (video, frame) if p.is_file()]


def capture_webui(url: str, out: Path, checks: list[dict], wait_ms: int) -> list[Path]:
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        checks.append({"name": "webui.chrome", "ok": False, "detail": "no Chrome/Chromium on PATH"})
        return []
    screenshot = out / "webui.png"
    dom = out / "webui.html"
    common = [chrome, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
              "--disable-dev-shm-usage", "--window-size=1280,720",
              f"--virtual-time-budget={wait_ms}", f"--timeout={wait_ms}"]
    proc = run(common + [f"--screenshot={screenshot}", url], timeout=wait_ms // 1000 + 60)
    checks.append({"name": "webui.screenshot", "ok": proc.returncode == 0 and screenshot.is_file() and screenshot.stat().st_size > 0,
                   "detail": f"chrome rc={proc.returncode} {proc.stderr.strip()[:160]}"})
    proc = run(common + ["--dump-dom", url], timeout=wait_ms // 1000 + 60)
    if proc.returncode == 0 and proc.stdout:
        dom.write_text(proc.stdout, encoding="utf-8")
    checks.append({"name": "webui.dom", "ok": dom.is_file() and dom.stat().st_size > 0,
                   "detail": f"chrome rc={proc.returncode}"})
    return [p for p in (screenshot, dom) if p.is_file()]


def capture_udp(receiver: list[str], out: Path, duration: int, checks: list[dict]) -> list[Path]:
    log = out / "receiver.log"
    started = time.time()
    with open(log, "w", encoding="utf-8") as fh:
        try:
            proc = subprocess.run(receiver, cwd=str(out), stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=duration)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = 124
        except OSError as exc:
            checks.append({"name": "udp.receiver", "ok": False, "detail": str(exc)})
            return [log]
    produced = [p for p in out.rglob("*") if p.is_file() and p != log and p.stat().st_size > 0]
    checks.append({"name": "udp.receiver", "ok": rc in (0, 124) and bool(produced),
                   "detail": f"rc={rc}, files={len(produced)}, elapsed={time.time() - started:.1f}s"})
    return [log] + produced


def capture_files(files: list[Path], out: Path, checks: list[dict]) -> list[Path]:
    copied: list[Path] = []
    for src in files:
        if not src.is_file():
            checks.append({"name": f"file.{src.name}", "ok": False, "detail": f"missing: {src}"})
            continue
        dst = out / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
    checks.append({"name": "file.copy", "ok": bool(copied), "detail": f"{len(copied)} file(s)"})
    return copied


def capture_live(session_dir: Path, out: Path, checks: list[dict]) -> list[Path]:
    """Turn a serve_demo.py session directory into standard evidence.

    Live viewing is not evidence by itself: we require at least one device
    snapshot (a JPEG frame sent by the demo), the device log, the event stream
    and session.json.
    """
    session_file = session_dir / "session.json"
    if not session_file.is_file():
        checks.append({"name": "live.session", "ok": False,
                       "detail": f"missing session.json in {session_dir}"})
        return []
    try:
        session = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append({"name": "live.session", "ok": False, "detail": f"invalid session.json: {exc}"})
        return []
    checks.append({"name": "live.session", "ok": True,
                   "detail": f"session {session.get('started_at')} -> {session.get('ended_at')}"})

    copied: list[Path] = []
    frames = sorted((session_dir / "frames").glob("*.jpg"))
    if not frames:
        checks.append({"name": "live.frames", "ok": False,
                       "detail": "no device JPEG snapshots; live UI alone is not evidence"})
    for src in frames:
        if src.stat().st_size == 0:
            checks.append({"name": f"live.frame.{src.name}", "ok": False, "detail": "zero-byte frame"})
            continue
        dst = out / f"live-{src.name}"
        shutil.copy2(src, dst)
        copied.append(dst)
    if frames:
        checks.append({"name": "live.frames", "ok": True, "detail": f"{len(frames)} snapshot(s)"})

    for rel, name in (("logs/device.log", "live-device.log"),
                      ("events.jsonl", "live-events.jsonl"),
                      ("session.json", "live-session.json")):
        src = session_dir / rel
        if src.is_file() and src.stat().st_size > 0:
            dst = out / name
            shutil.copy2(src, dst)
            copied.append(dst)
        elif rel == "logs/device.log":
            checks.append({"name": "live.log", "ok": False, "detail": f"missing {rel}"})
    if (out / "live-device.log").is_file():
        checks.append({"name": "live.log", "ok": True, "detail": "device log copied"})
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("rtsp", "webui", "udp", "file", "live"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--url")
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--wait-ms", type=int, default=8000)
    parser.add_argument("--receiver", help="receiver command, e.g. 'python3 udp_receiver.py --port 5001'")
    parser.add_argument("--file", action="append", default=[], type=Path)
    parser.add_argument("--session", type=Path, help="serve_demo.py session directory for --source live")
    parser.add_argument("--probe-report", type=Path, help="probe_device.py JSON to embed")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    artifacts: list[Path] = []
    target = None

    if args.source == "rtsp":
        if not args.url:
            print("ERROR: --url is required for rtsp", file=sys.stderr)
            return 2
        target = args.url
        artifacts = capture_rtsp(args.url, out, args.duration, checks)
    elif args.source == "webui":
        if not args.url:
            print("ERROR: --url is required for webui", file=sys.stderr)
            return 2
        target = args.url
        artifacts = capture_webui(args.url, out, checks, args.wait_ms)
    elif args.source == "udp":
        if not args.receiver:
            print("ERROR: --receiver is required for udp", file=sys.stderr)
            return 2
        import shlex
        target = args.receiver
        artifacts = capture_udp(shlex.split(args.receiver), out, args.duration, checks)
    elif args.source == "live":
        if not args.session:
            print("ERROR: --session is required for live", file=sys.stderr)
            return 2
        target = str(args.session.resolve())
        artifacts = capture_live(args.session.resolve(), out, checks)
    else:
        if not args.file:
            print("ERROR: --file is required for file", file=sys.stderr)
            return 2
        target = [str(p) for p in args.file]
        artifacts = capture_files(args.file, out, checks)

    integrity: list[dict] = []
    for path in artifacts:
        size = path.stat().st_size
        integrity.append({"path": str(path.relative_to(out)), "size": size, "sha256": sha256_file(path)})
        if size == 0:
            checks.append({"name": f"integrity.{path.name}", "ok": False, "detail": "zero-byte artifact"})

    probe = None
    if args.probe_report and args.probe_report.is_file():
        try:
            probe = json.loads(args.probe_report.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            checks.append({"name": "probe.report", "ok": False, "detail": "invalid probe JSON"})

    ok = bool(artifacts) and all(check["ok"] for check in checks)
    evidence = {
        "schema_version": 1,
        "kind": "demo-evidence",
        "generated_at": now_iso(),
        "source": args.source,
        "target": target,
        "duration_s": args.duration,
        "tool_versions": {name: tool_version(name) for name in ("ffmpeg",) if shutil.which(name)},
        "artifacts": integrity,
        "checks": checks,
        "device": probe.get("inventory", {}).get("device") if probe else None,
        "device_contract_drift": probe.get("contract_drift") if probe else None,
        "ok": ok,
    }
    report_path = args.report or (out / "evidence.json")
    write_json(report_path, evidence)
    for check in checks:
        if not check["ok"]:
            print(f"  FAIL {check['name']}: {check['detail']}")
    print(f"evidence: {report_path} ({len(integrity)} artifact(s))")
    print(f"capture: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())