#!/usr/bin/env python3
"""Build a deterministic, verified demo archive.

    python3 scripts/package_demo.py --demo-dir <dir>
    python3 scripts/package_demo.py --demo-dir <dir> --mode publish

The archive contains demo.json, the built binary, models, sources and
eval/input provenance, plus files.sha256. Timestamps and owners are fixed, so
packaging the same content twice produces identical bytes.
"""

from __future__ import annotations

import argparse
import hashlib
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
    package_files,
    print_result,
    set_stage,
    sha256_file,
    validate_manifest,
    write_report,
)
from devkit.artifacts import deterministic_tar  # noqa: E402


def build_file_list(demo_dir: Path, manifest: dict) -> list[tuple[Path, str]]:
    binary_rel = None
    if manifest.get("build", {}).get("kind") in ("cpp", "cmake", "prebuilt"):
        binary_rel = f"dist/{manifest['build']['binary']}"
    files = package_files(demo_dir, manifest, binary_rel)
    report = demo_dir / "dist" / "build-report.json"
    if report.is_file():
        files.append((report, "build-report.json"))
    return files


def write_checksums(files: list[tuple[Path, str]]) -> Path:
    lines = []
    for source, rel in sorted(files, key=lambda item: item[1]):
        lines.append(f"{sha256_file(source)}  {rel}")
    tmp = Path(tempfile.mkstemp(prefix="files-", suffix=".sha256")[1])
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def verify_archive(archive: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        missing_manifest = [n for n in names if n.endswith("/demo.json")]
        if not missing_manifest:
            errors.append("archive has no demo.json")
        sha_member = next((n for n in names if n.endswith("/files.sha256")), None)
        if not sha_member:
            errors.append("archive has no files.sha256")
            return errors
        payload = tar.extractfile(sha_member)
        if payload is None:
            errors.append("cannot read files.sha256")
            return errors
        entries = []
        for line in payload.read().decode("utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            entries.append((digest, rel))
        root = sha_member.rsplit("/", 1)[0]
        for digest, rel in entries:
            member = f"{root}/{rel}"
            handle = tar.extractfile(member)
            if handle is None:
                errors.append(f"missing member: {member}")
                continue
            actual = hashlib.sha256(handle.read()).hexdigest()
            if actual != digest:
                errors.append(f"hash mismatch: {rel}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="output directory (default: <demo>/dist)")
    parser.add_argument("--mode", choices=("package", "publish"), default="package")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    demo_dir = args.demo_dir.resolve()
    try:
        manifest, _ = load_manifest(demo_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    validate_mode = "publish" if args.mode == "publish" else "deliver"
    result = validate_manifest(manifest, demo_dir, load_contracts(SKILL_ROOT),
                               mode=validate_mode, skill_root=SKILL_ROOT)
    print_result(result, "package validation")
    if not result.ok:
        return 1

    kind = manifest.get("build", {}).get("kind")
    binary = demo_dir / "dist" / str(manifest.get("build", {}).get("binary", ""))
    if kind in ("cpp", "cmake", "prebuilt") and not binary.is_file():
        print(f"ERROR: built artifact missing: {binary} (run build_demo.py first)", file=sys.stderr)
        return 2

    files = build_file_list(demo_dir, manifest)
    if not any(rel == "demo.json" for _, rel in files):
        files.append((demo_dir / "demo.json", "demo.json"))
    checksums = write_checksums(files)
    files.append((checksums, "files.sha256"))

    out_dir = (args.out or (demo_dir / "dist")).resolve()
    root_name = f"{manifest['name']}-{manifest['version']}"
    archive = out_dir / f"{root_name}.tar.gz"
    try:
        entries = deterministic_tar(files, archive, root_name,
                                    executable_suffixes=(".sh", ".py", str(manifest["build"]["binary"])))
    finally:
        checksums.unlink(missing_ok=True)

    errors = verify_archive(archive, manifest)
    if errors:
        print("ERROR: archive verification failed", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    report = new_report("demo-package", manifest.get("name"),
                        archive=str(archive), mode=args.mode, files=entries)
    set_stage(report, "static", "passed", "schema and contract validation")
    set_stage(report, "offline", "passed", "deterministic archive verified",
              {"sha256": sha256_file(archive), "size": archive.stat().st_size})
    set_stage(report, "device-upload", "not_run")
    set_stage(report, "device-run", "not_run")
    set_stage(report, "task-quality", "not_run")

    report_path = args.report or (out_dir / "package-report.json")
    write_report(report_path, report)
    print(f"archive: {archive}")
    print(f"sha256:  {sha256_file(archive)}")
    print(f"size:    {archive.stat().st_size} bytes")
    print(f"report:  {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())