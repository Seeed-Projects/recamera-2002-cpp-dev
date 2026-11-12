#!/usr/bin/env python3
"""Cross-compile or stage a reCamera demo artifact.

Supports demo.json build.kind: cpp | cmake | prebuilt | script. The output is
always dist/<binary> plus dist/build-report.json; deployment is a separate step.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SKILL_ROOT,
    STAGES,
    ValidationResult,
    compiler_path,
    find_toolchain,
    load_contracts,
    load_lock,
    load_manifest,
    now_iso,
    print_result,
    semver_tuple,
    sha256_file,
    validate_elf_riscv,
    validate_manifest,
    write_json,
)

TOOLCHAIN_LOCK = "toolchain.lock.json"


def check_toolchain_lock(toolchain_bin: Path | None) -> tuple[list[str], list[str], dict]:
    """Compare the resolved toolchain with configs/toolchain.lock.json.

    Returns (warnings, errors, report). A missing lock or toolchain is a
    warning: the build still records the real compiler version and hashes.
    """
    lock = load_lock(TOOLCHAIN_LOCK, SKILL_ROOT)
    report: dict = {"lock": TOOLCHAIN_LOCK, "present": bool(lock)}
    warnings: list[str] = []
    errors: list[str] = []
    if not lock:
        warnings.append(f"{TOOLCHAIN_LOCK} not found; toolchain version is not pinned")
        return warnings, errors, report
    if toolchain_bin is None:
        warnings.append("toolchain not resolved; skip lock comparison")
        return warnings, errors, report

    version = ""
    for name in ("riscv64-unknown-linux-musl-gcc", "riscv64-linux-musl-gcc"):
        candidate = toolchain_bin / name
        if candidate.is_file():
            proc = subprocess.run([str(candidate), "--version"], capture_output=True, text=True)
            version = proc.stdout.splitlines()[0] if proc.stdout else ""
            break
    report.update({"resolved_bin": str(toolchain_bin), "compiler_version": version})

    minimum = lock.get("minimum_gcc")
    if minimum and version:
        got = semver_tuple(version)
        want = semver_tuple(minimum)
        if got and want and got[:len(want)] < want:
            errors.append(f"toolchain gcc {version!r} is older than lock minimum {minimum!r}")
    expected = lock.get("verified_gcc_pattern")
    if expected and version and not re.search(expected, version):
        warnings.append(f"toolchain gcc {version!r} does not match verified pattern {expected!r}")
    return warnings, errors, report


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}")
    return proc


def build_cpp(demo_dir: Path, manifest: dict, toolchain_bin: Path, sdk_root: Path | None) -> Path:
    build = manifest["build"]
    source_dir = (demo_dir / str(build.get("source_dir", "."))).resolve()
    sources = [str(source_dir / rel) for rel in build["sources"]]
    kind = "cxx" if any(Path(s).suffix.lower() in (".cc", ".cpp", ".cxx", ".hpp") for s in sources) else "c"
    compiler = compiler_path(toolchain_bin, kind)
    out = demo_dir / "dist" / build["binary"]
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(compiler), *sources]
    for inc in build.get("include_dirs", []):
        cmd += ["-I", str(source_dir / inc)]
    for define in build.get("defines", []):
        cmd += ["-D", define]
    cmd += [str(f) for f in build.get("cflags", [])]
    for lib_dir in build.get("lib_dirs", []):
        cmd += ["-L", str(source_dir / lib_dir)]
    for lib in build.get("libs", []):
        cmd += ["-l", lib]
    cmd += [str(f) for f in build.get("ldflags", [])]
    cmd += ["-o", str(out)]

    env = os.environ.copy()
    if sdk_root:
        env["SG200X_SDK_PATH"] = str(sdk_root)
    env["PATH"] = f"{toolchain_bin}:{env.get('PATH', '')}"
    run(cmd, cwd=source_dir, env=env)
    return out


def build_cmake(demo_dir: Path, manifest: dict, toolchain_bin: Path, sdk_root: Path | None) -> Path:
    build = manifest["build"]
    source_dir = (demo_dir / str(build.get("source_dir", "."))).resolve()
    build_dir = demo_dir / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = f"{toolchain_bin}:{env.get('PATH', '')}"
    env["CC"] = str(compiler_path(toolchain_bin, "c"))
    env["CXX"] = str(compiler_path(toolchain_bin, "cxx"))
    if sdk_root:
        env["SG200X_SDK_PATH"] = str(sdk_root)
    cmake_args = build.get("cmake_args", [])
    run(["cmake", "-S", str(source_dir), "-B", str(build_dir),
         "-DCMAKE_BUILD_TYPE=Release", *cmake_args], cwd=demo_dir, env=env)
    run(["cmake", "--build", str(build_dir), "-j", str(os.cpu_count() or 1)], cwd=demo_dir, env=env)
    wanted = build["binary"]
    matches = [p for p in build_dir.rglob(wanted) if p.is_file()]
    if not matches:
        raise FileNotFoundError(f"cmake build produced no {wanted} under {build_dir}")
    out = demo_dir / "dist" / wanted
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(matches[0], out)
    return out


def build_prebuilt(demo_dir: Path, manifest: dict) -> Path:
    build = manifest["build"]
    src = demo_dir / build["artifact"]
    out = demo_dir / "dist" / build["binary"]
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != out.resolve():
        shutil.copy2(src, out)
    return out


def build_script(demo_dir: Path, manifest: dict, toolchain_bin: Path | None, sdk_root: Path | None) -> Path:
    build = manifest["build"]
    env = os.environ.copy()
    if toolchain_bin:
        env["PATH"] = f"{toolchain_bin}:{env.get('PATH', '')}"
    if sdk_root:
        env["SG200X_SDK_PATH"] = str(sdk_root)
    env["DEMO_DIR"] = str(demo_dir)
    run(["sh", build["script"]], cwd=demo_dir, env=env)
    out = demo_dir / "dist" / build["binary"]
    if not out.is_file():
        raise FileNotFoundError(f"build script did not produce {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", required=True, type=Path)
    parser.add_argument("--sdk-root", type=Path, default=Path(os.environ["SG200X_SDK_PATH"]).expanduser()
                        if os.environ.get("SG200X_SDK_PATH") else None)
    parser.add_argument("--toolchain-bin", type=Path)
    parser.add_argument("--check-only", action="store_true", help="validate inputs without compiling")
    parser.add_argument("--strict-toolchain", action="store_true",
                        help="fail when the toolchain differs from configs/toolchain.lock.json")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    demo_dir = args.demo_dir.resolve()
    try:
        manifest, _ = load_manifest(demo_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = ValidationResult()
    if not args.skip_validate:
        result = validate_manifest(manifest, demo_dir, load_contracts(), mode="deliver")
        print_result(result, "pre-build validation")
        if not result.ok:
            return 1

    build = manifest["build"]
    kind = build["kind"]
    toolchain_bin: Path | None = None
    toolchain_lock: dict = {"present": False}
    if kind in ("cpp", "cmake", "script"):
        toolchain_bin, error = find_toolchain(
            str(args.toolchain_bin) if args.toolchain_bin else None,
            args.sdk_root,
        )
        if error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        print(f"toolchain: {toolchain_bin}")
        lock_warnings, lock_errors, toolchain_lock = check_toolchain_lock(toolchain_bin)
        for warning in lock_warnings:
            print(f"  WARN  {warning}")
        for error_text in lock_errors:
            print(f"  ERROR {error_text}", file=sys.stderr)
        if lock_errors and args.strict_toolchain:
            print("ERROR: --strict-toolchain rejected the resolved toolchain", file=sys.stderr)
            return 2

    if args.check_only:
        print("check-only: inputs look usable; nothing was built")
        return 0

    try:
        if kind == "cpp":
            artifact = build_cpp(demo_dir, manifest, toolchain_bin, args.sdk_root)
        elif kind == "cmake":
            artifact = build_cmake(demo_dir, manifest, toolchain_bin, args.sdk_root)
        elif kind == "prebuilt":
            artifact = build_prebuilt(demo_dir, manifest)
        elif kind == "script":
            artifact = build_script(demo_dir, manifest, toolchain_bin, args.sdk_root)
        else:
            raise ValueError(f"unsupported build.kind: {kind}")
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    elf_result = ValidationResult()
    validate_elf_riscv(artifact, elf_result, "built artifact")
    print_result(elf_result, "artifact check")
    if not elf_result.ok:
        return 1

    compiler_version = ""
    if toolchain_bin and kind in ("cpp", "cmake"):
        for name in ("riscv64-unknown-linux-musl-gcc", "riscv64-linux-musl-gcc"):
            candidate = toolchain_bin / name
            if candidate.is_file():
                proc = subprocess.run([str(candidate), "--version"], capture_output=True, text=True)
                compiler_version = proc.stdout.splitlines()[0] if proc.stdout else ""
                break

    sources = []
    for rel in build.get("sources", []):
        path = demo_dir / str(build.get("source_dir", ".")) / rel
        if path.is_file():
            sources.append({"path": str(path.relative_to(demo_dir)), "sha256": sha256_file(path)})

    report = {
        "schema_version": 1,
        "kind": "demo-build",
        "generated_at": now_iso(),
        "demo": manifest.get("name"),
        "build_kind": kind,
        "sdk_root": str(args.sdk_root) if args.sdk_root else None,
        "toolchain_bin": str(toolchain_bin) if toolchain_bin else None,
        "compiler_version": compiler_version,
        "toolchain_lock": toolchain_lock,
        "stages": {
            stage: {"status": (
                "not_run" if stage == "static" and args.skip_validate
                else "passed" if stage in ("static", "offline")
                else "not_run"
            )}
            for stage in STAGES
        },
        "sources": sources,
        "artifact": {
            "path": str(artifact.relative_to(demo_dir)),
            "size": artifact.stat().st_size,
            "sha256": sha256_file(artifact),
            "elf": "ELF64 RISC-V little-endian",
        },
    }
    report_path = args.report or (demo_dir / "dist" / "build-report.json")
    write_json(report_path, report)
    print(f"artifact: {artifact}")
    print(f"sha256:   {report['artifact']['sha256']}")
    print(f"report:   {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())