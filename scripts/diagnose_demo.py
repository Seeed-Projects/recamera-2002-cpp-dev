#!/usr/bin/env python3
"""Read-only diagnosis for an installed reCamera demo that will not run.

Collects install hashes, PID, logs, camera/audio device state, storage, memory,
services and kernel tail, then turns them into concrete hints. It never changes
the device.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SSH,
    SSHError,
    load_manifest,
    now_iso,
    redact,
    sha256_file,
    write_json,
)

PROBE = r"""
set -u
DIR="$1"
echo "SECTION dir"
ls -la "$DIR" 2>&1
echo "SECTION manifest"
cat "$DIR/demo.json" 2>&1 | head -20
echo "SECTION pid"
if [ -s "$DIR/run.pid" ]; then echo "pid=$(cat "$DIR/run.pid")"; else echo "pid="; fi
echo "SECTION logs"
for f in $(ls -t "$DIR"/logs/*.log 2>/dev/null | head -2); do echo "== $f"; tail -n 80 "$f" 2>&1; done
echo "SECTION hashes"
cd "$DIR" 2>/dev/null && find . -maxdepth 1 -type f -exec sha256sum {} \; 2>/dev/null
echo "SECTION processes"
ps w 2>/dev/null | grep -v grep | grep -E 'node-red|sscma|supervisor|detector|ttyd' | head -20
echo "SECTION devices"
ls -l /dev/video* /dev/snd/* 2>&1 | head -20
echo "SECTION resources"
free -m 2>&1 | head -4
df -h / /userdata 2>&1 | head -5
echo "SECTION kernel"
dmesg 2>/dev/null | tail -n 30
echo "SECTION end"
"""


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"__raw__": []}
    current = "__raw__"
    for line in text.splitlines():
        if line.startswith("SECTION "):
            current = line[len("SECTION "):].strip()
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def classify(manifest: dict, data: dict[str, str], expected: dict[str, str]) -> tuple[list[str], list[str], bool]:
    """Return (findings, hints, ok) from collected section text."""
    findings: list[str] = []
    hints: list[str] = []
    runtime = manifest.get("runtime", {})
    name = manifest.get("name", "")

    logs = data.get("logs", "")
    processes = data.get("processes", "")
    resources = data.get("resources", "")
    kernel = data.get("kernel", "")
    devices = data.get("devices", "")

    if "no such file" in data.get("dir", "").lower() or data.get("dir", "").strip() == "":
        findings.append("remote demo directory missing or empty")
        hints.append("run deploy_demo.py --action install")

    installed = {}
    for line in data.get("dir", "").splitlines():
        parts = line.split()
        if len(parts) >= 9 and not line.startswith("total"):
            installed[parts[-1]] = line
    for rel, digest in expected.items():
        if rel not in installed:
            findings.append(f"installed file missing: {rel}")
            hints.append("re-run install with --replace")
    hashes = {}
    for line in data.get("hashes", "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            hashes[parts[1].lstrip("./")] = parts[0]
    for rel, digest in expected.items():
        if digest and rel in hashes and hashes[rel] != digest:
            findings.append(f"installed hash mismatch: {rel}")
            hints.append("re-run install with --replace; local dist may be stale")
    if logs and "permission denied" in logs.lower():
        findings.append("log is not readable by the SSH user")
        hints.append("re-run start (it chmods logs) or read the log through sudo")
    if any(token in logs.lower() for token in ("cannot open", "no such file", "not found")):
        findings.append("runtime reported a missing file/library")
        hints.append("check LD_LIBRARY_PATH and that models/files were uploaded")
    if "segmentation fault" in logs.lower() or "illegal instruction" in logs.lower():
        findings.append("binary crashed (SIGSEGV/SIGILL)")
        hints.append("confirm the artifact is RISC-V musl and matches the device ABI")
    if "out of memory" in logs.lower() or "ion ioctl" in logs.lower():
        findings.append("ION/user memory exhaustion reported")
        hints.append("reduce model input/ION usage, stop other camera services, or reboot")
    if "address already in use" in logs.lower() or "device busy" in logs.lower():
        findings.append("port or camera device is busy")
        hints.append("stop the occupying service only with explicit --allow-service-stop")

    pid_line = data.get("pid", "")
    pid = pid_line.split("=", 1)[1].strip() if "=" in pid_line else ""
    if runtime.get("long_running"):
        if not pid:
            findings.append("long-running demo has no run.pid")
            hints.append("start it with deploy_demo.py --action start")
        elif "error" in data.get("pid", "").lower():
            findings.append("cannot inspect the PID")
            hints.append("check sudo permissions for kill -0")

    if runtime.get("requires_camera") and any(s in processes for s in ("sscma", "node-red", "supervisor")):
        findings.append("camera services are running while the demo requires the camera")
        hints.append("declare services_stop in demo.json and pass --allow-service-stop, then restore services")
    if not any(name in line for line in processes.splitlines()) and runtime.get("long_running"):
        findings.append("demo process is not present in ps")
        hints.append("inspect the latest log and exit code before restarting")

    if devices.strip():
        if "video" not in devices:
            findings.append("no /dev/video* node is visible")
            hints.append("confirm the camera driver and that the demo does not need a stopped service")
        if "/dev/snd" not in devices and manifest.get("runtime", {}).get("audio"):
            findings.append("no /dev/snd nodes are visible")
            hints.append("audio demos need ALSA devices and permissions")

    if resources:
        for line in resources.splitlines():
            if line.startswith("/dev/root") or line.startswith("overlay"):
                parts = line.split()
                if len(parts) >= 5 and parts[-2].rstrip("%").isdigit() and int(parts[-2].rstrip("%")) >= 98:
                    findings.append("root filesystem is nearly full")
                    hints.append("write demo files under /userdata, not /")
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) < 40:
                    findings.append("low free memory")
                    hints.append("stop competing services or reduce model size")

    for marker, hint in (("out of memory", "kernel reported OOM; reduce memory pressure"),
                         ("oom-kill", "kernel OOM killer fired; reduce memory pressure"),
                         ("segfault", "kernel saw a segfault; check ABI and libraries")):
        if marker in kernel.lower():
            findings.append(f"kernel tail mentions {marker}")
            hints.append(hint)

    ok = not findings
    return findings, hints, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="recamera")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--password-env", default="RECAMERA_PASSWORD")
    parser.add_argument("--no-password", action="store_true")
    parser.add_argument("--insecure-host-key", action="store_true")
    parser.add_argument("--remote-dir")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    demo_dir = args.demo_dir.resolve()
    manifest, _ = load_manifest(demo_dir)
    remote_dir = args.remote_dir or f"/userdata/demos/{manifest['name']}"
    ssh = SSH(args.host, args.user, args.port, None if args.no_password else args.password_env,
              strict_host_key="insecure" if args.insecure_host_key else "accept-new")
    try:
        result = ssh.run(f"sh -c {shlex.quote(PROBE)} sh {shlex.quote(remote_dir)}", timeout=60)
    except SSHError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not result.ok:
        print(f"ERROR: diagnosis probe failed: {result.stderr}", file=sys.stderr)
        return 2

    data = parse_sections(result.stdout)
    expected = {}
    for rel in [manifest["build"]["binary"], "demo.json", *manifest.get("runtime", {}).get("files", [])]:
        local = demo_dir / ("dist/" + rel if rel == manifest["build"]["binary"] else rel)
        expected[rel] = sha256_file(local) if local.is_file() else ""
    findings, hints, ok = classify(manifest, data, expected)

    report = {
        "schema_version": 1,
        "kind": "demo-diagnosis",
        "generated_at": now_iso(),
        "demo": manifest.get("name"),
        "target": {"host": args.host, "user": args.user, "port": args.port},
        "remote_dir": remote_dir,
        "findings": findings,
        "hints": hints,
        "ok": ok,
        "raw": redact(result.stdout, ssh.password),
    }
    if args.report:
        write_json(args.report, report)
    for finding in findings:
        print(f"FINDING {finding}")
    for hint in hints:
        print(f"HINT    {hint}")
    print(f"diagnosis: {'OK' if ok else 'ISSUES FOUND'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())