#!/usr/bin/env python3
"""Read-only inventory of a reCamera device.

Runs one POSIX shell probe over SSH, records OS/tools/paths/storage/services,
and compares the result with configs/device-contract.json. It never writes to
the device and never changes a service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SSH,
    SSHError,
    load_contracts,
    now_iso,
    redact,
    semver_tuple,
    write_json,
)

PROBE = r"""
set -u
echo "SECTION device"
echo "hostname=$(hostname 2>/dev/null)"
echo "kernel=$(uname -srm 2>/dev/null)"
echo "arch=$(uname -m 2>/dev/null)"
echo "os_name=$(sed -n 's/^NAME=//p' /etc/os-release 2>/dev/null | head -1)"
echo "os_version=$(sed -n 's/^VERSION=//p' /etc/os-release 2>/dev/null | head -1)"
echo "buildroot_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null | head -1)"
echo "api_raw=$(curl -s --max-time 3 http://127.0.0.1/api/version 2>/dev/null)"
echo "uptime_s=$(cut -d. -f1 /proc/uptime 2>/dev/null)"
echo "mac_eth0=$(cat /sys/class/net/eth0/address 2>/dev/null)"
echo "mac_usb0=$(cat /sys/class/net/usb0/address 2>/dev/null)"
echo "SECTION storage"
echo "root_used_pct=$(df -P / 2>/dev/null | awk 'NR==2{gsub(/%/,"",$5); print $5}')"
echo "root_avail_kb=$(df -P / 2>/dev/null | awk 'NR==2{print $4}')"
echo "userdata_avail_kb=$(df -P /userdata 2>/dev/null | awk 'NR==2{print $4}')"
echo "SECTION tools"
for t in sh curl wget python3 ffmpeg aplay setsid sha256sum tar scp gcc g++ cmake; do
  if command -v "$t" >/dev/null 2>&1; then echo "tool.$t=yes"; else echo "tool.$t=no"; fi
done
echo "SECTION paths"
for p in /userdata /userdata/Models /userdata/local /userdata/local/models /mnt/system/sdk-release /mnt/system/lib /mnt/system/usr/lib /mnt/system/usr/lib/3rd /etc/init.d; do
  if [ -e "$p" ]; then echo "path.$p=yes"; else echo "path.$p=no"; fi
done
echo "SECTION services"
ls /etc/init.d/ 2>/dev/null | sed -n 's/^/init./p'
echo "SECTION processes"
ps w 2>/dev/null | grep -v grep | grep -E 'node-red|sscma|supervisor|ttyd|sshd|detector|recamera' | sed -n 's/^/proc./p'
echo "SECTION models"
echo "model_count=$(find /userdata/Models /userdata/local/models -maxdepth 1 -name '*.cvimodel' 2>/dev/null | wc -l)"
find /userdata/Models /userdata/local/models -maxdepth 1 -name '*.cvimodel' 2>/dev/null | sed -n 's/^/model./p'
echo "SECTION end"
"""


def parse_probe(text: str) -> dict:
    data: dict[str, dict[str, str]] = {}
    section = "misc"
    data[section] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("SECTION "):
            section = line[len("SECTION "):].strip()
            data.setdefault(section, {})
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            data[section][key] = value
        elif line:
            data[section][line] = "yes"
    return data


def api_version(raw: str) -> str | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    value = obj.get("data")
    return str(value) if value is not None else None


def check_contract(inventory: dict, contract: dict, expect_mac: str | None) -> tuple[list[dict], list[str]]:
    checks: list[dict] = []
    drift: list[str] = []

    def add(name: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})
        if not ok and severity == "error":
            drift.append(detail)

    profiles = contract.get("devices", {})
    profile = profiles.get("reCamera 2002", {})
    device = inventory.get("device", {})
    storage = inventory.get("storage", {})
    tools = inventory.get("tools", {})
    paths = inventory.get("paths", {})

    arch = device.get("arch", "")
    add("arch", arch == profile.get("arch"), f"arch={arch!r}, expected {profile.get('arch')!r}")

    version = api_version(device.get("api_raw", "")) or ""
    os_spec = profile.get("os", {})
    tested = os_spec.get("tested", [])
    minimum = os_spec.get("minimum") or (tested[0] if tested else "")
    if version and minimum:
        ok = semver_tuple(version) >= semver_tuple(minimum)
        add("os_version", ok, f"api version {version}, supported >= {minimum}")
        if not ok:
            drift.append(f"OS {version} is older than supported minimum {minimum}")
    else:
        add("os_version", False, "cannot read /api/version over 127.0.0.1", severity="error")

    for tool in profile.get("required_tools", []):
        add(f"tool.{tool}", tools.get(f"tool.{tool}") == "yes", f"required tool {tool} missing")

    for key, path in profile.get("paths", {}).items():
        if key in ("demo_root", "staging_root"):
            continue
        add(f"path.{path}", paths.get(f"path.{path}") == "yes", f"expected path missing: {path}", severity="error")

    avail = int(storage.get("userdata_avail_kb", "0") or 0)
    add("userdata_free", avail >= 1024 * 1024, f"/userdata free {avail // 1024} MiB (<1 GiB)", severity="warn")
    root_pct = int(storage.get("root_used_pct", "0") or 0)
    add("root_writable_headroom", root_pct < 100, f"/ used {root_pct}%", severity="warn")

    macs = {device.get("mac_eth0", "").lower(), device.get("mac_usb0", "").lower()} - {""}
    if expect_mac:
        add("mac_identity", expect_mac.lower() in macs,
            f"expected MAC {expect_mac} not in {sorted(macs)}")

    add("no_local_compiler", tools.get("tool.gcc") == "no", "device has a local compiler; cross-compile assumptions may differ",
        severity="info")

    return checks, drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="recamera")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--password-env", default="RECAMERA_PASSWORD",
                        help="environment variable holding the SSH password; use --no-password for key auth")
    parser.add_argument("--no-password", action="store_true")
    parser.add_argument("--insecure-host-key", action="store_true")
    parser.add_argument("--expect-mac")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="print the remote probe and exit")
    args = parser.parse_args(argv)

    if args.dry_run:
        print(PROBE)
        return 0

    password_env = None if args.no_password else args.password_env
    ssh = SSH(args.host, args.user, args.port, password_env,
              strict_host_key="insecure" if args.insecure_host_key else "accept-new")
    try:
        remote = ssh.run(PROBE, timeout=args.timeout)
    except SSHError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not remote.ok:
        print(f"ERROR: probe failed ({remote.returncode}): {remote.stderr}", file=sys.stderr)
        return 2

    inventory = parse_probe(remote.stdout)
    contract, _ = load_contracts()
    checks, drift = check_contract(inventory, contract, args.expect_mac)

    report = {
        "schema_version": 1,
        "kind": "device-probe",
        "generated_at": now_iso(),
        "target": {"host": args.host, "user": args.user, "port": args.port},
        "inventory": inventory,
        "checks": checks,
        "contract_drift": drift,
        "ok": not any(not c["ok"] and c["severity"] == "error" for c in checks),
        "raw_stdout": redact(remote.stdout, ssh.password),
    }
    if args.report:
        write_json(args.report, report)

    device = inventory.get("device", {})
    print(f"device:  {device.get('hostname')} ({device.get('kernel')})")
    print(f"api:     {api_version(device.get('api_raw', ''))}")
    print(f"storage: userdata {int(inventory.get('storage', {}).get('userdata_avail_kb', 0) or 0) // 1024} MiB free")
    for check in checks:
        if not check["ok"]:
            print(f"  {check['severity'].upper():5} {check['detail']}")
    print(f"probe: {'PASS' if report['ok'] else 'FAIL'}")
    if args.report:
        print(f"report: {args.report}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())