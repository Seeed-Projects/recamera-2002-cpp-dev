#!/usr/bin/env python3
"""Host-side smoke test driven by demo.json.

Runs the declared ``smoke.command`` in the demo directory (or inside a packaged
archive) with a bounded timeout. This is for pure logic and mock drivers: it
never loads a device model and never talks to the reCamera. A demo without a
smoke section reports ``not_run`` (exit 2) instead of failing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SKILL_ROOT,
    load_contracts,
    load_manifest,
    new_report,
    print_result,
    set_stage,
    validate_manifest,
    write_report,
)


def extract_archive(archive: Path) -> tuple[Path, Path]:
    """Safely extract a demo archive; return (temp root, demo root)."""
    temp_root = Path(tempfile.mkdtemp(prefix="demo-smoke-"))
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name.startswith(("/", "..")) or "/../" in name or "\\" in name:
                raise ValueError(f"unsafe archive member: {name}")
            if member.issym() or member.islnk():
                raise ValueError(f"archive links are not allowed: {name}")
        if sys.version_info >= (3, 12):
            tar.extractall(temp_root, filter="data")
        else:
            tar.extractall(temp_root)
    candidates = [p.parent for p in temp_root.rglob("demo.json")]
    if not candidates:
        raise ValueError("archive contains no demo.json")
    return temp_root, candidates[0]


def run(args, demo_dir: Path) -> int:
    try:
        manifest, _ = load_manifest(demo_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result = validate_manifest(manifest, demo_dir, load_contracts(SKILL_ROOT),
                               mode="demo", skill_root=SKILL_ROOT)
    print_result(result, "smoke preconditions")
    if not result.ok:
        return 1

    smoke = manifest.get("smoke")
    if not smoke or smoke.get("kind") in (None, "none"):
        print("smoke: not_run (demo.json has no host smoke hook)")
        return 2

    command = smoke.get("command")
    if not isinstance(command, list) or not command:
        print("ERROR: smoke.command is missing", file=sys.stderr)
        return 1
    timeout = args.timeout or int(smoke.get("timeout_s", 15))
    fixtures = [str((demo_dir / rel).resolve()) for rel in smoke.get("fixtures", [])]
    fixtures += [str(path.resolve()) for path in args.fixture]
    markers = smoke.get("expect_stdout", [])

    env = os.environ.copy()
    env["DEMO_DIR"] = str(demo_dir)
    env["DEMO_FIXTURES"] = os.pathsep.join(fixtures)

    print("+ " + " ".join(command))
    try:
        proc = subprocess.run(command, cwd=str(demo_dir), capture_output=True, text=True,
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        print(f"ERROR: command not found: {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"ERROR: smoke command timed out after {timeout}s", file=sys.stderr)
        return 1

    output = (proc.stdout or "") + (proc.stderr or "")
    print(output.rstrip())
    missing = [marker for marker in markers if marker not in output]
    passed = proc.returncode == 0 and not missing
    if proc.returncode != 0:
        print(f"ERROR: smoke command exited {proc.returncode}", file=sys.stderr)
    for marker in missing:
        print(f"ERROR: expected output marker missing: {marker!r}", file=sys.stderr)

    report = new_report("demo-smoke", manifest.get("name"), command=command,
                        timeout_s=timeout, archive=str(args.archive) if args.archive else None)
    set_stage(report, "static", "passed", "contract validation")
    set_stage(report, "offline", "passed" if passed else "failed",
              "host smoke command", {"exit_code": proc.returncode, "missing_markers": missing})
    report_path = args.report or (Path.cwd() / "smoke-report.json" if args.archive
                                  else demo_dir / "dist" / "smoke-report.json")
    write_report(report_path, report)
    print(f"smoke: {'PASS' if passed else 'FAIL'}")
    print(f"report: {report_path}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo-dir", type=Path)
    source.add_argument("--archive", type=Path, help="run smoke against a packaged .tar.gz")
    parser.add_argument("--timeout", type=int, help="override smoke.timeout_s")
    parser.add_argument("--fixture", action="append", default=[], type=Path,
                        help="extra fixture path exposed as DEMO_FIXTURES")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    if not args.archive:
        return run(args, args.demo_dir.resolve())

    if not args.archive.is_file():
        print(f"ERROR: archive not found: {args.archive}", file=sys.stderr)
        return 2
    temp_root, demo_root = extract_archive(args.archive)
    try:
        return run(args, demo_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())