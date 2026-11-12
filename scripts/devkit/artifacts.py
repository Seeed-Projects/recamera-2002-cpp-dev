"""Artifact helpers: toolchain discovery and deterministic packaging."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import tarfile
from pathlib import Path
from typing import Iterable

TOOLCHAIN_GLOBS = (
    "host-tools/gcc/riscv64-linux-musl-x86_64/bin",
    "host-tools/gcc/riscv64-linux-musl-x86_64/bin64",
    "osdrv/tools/board/gcc/riscv64-linux-musl-x86_64/bin",
)
COMPILERS = {
    "c": ("riscv64-unknown-linux-musl-gcc", "riscv64-linux-musl-gcc"),
    "cxx": ("riscv64-unknown-linux-musl-g++", "riscv64-linux-musl-g++"),
}


def find_toolchain(explicit: str | None, sdk_root: Path | None) -> tuple[Path | None, Path | None]:
    """Resolve the RISC-V musl toolchain bin directory.

    Order: explicit -> TOOLCHAIN_BIN -> SDK paths -> ~/host-tools -> PATH.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_bin = os.environ.get("TOOLCHAIN_BIN")
    if env_bin:
        candidates.append(Path(env_bin).expanduser())
    if sdk_root:
        for rel in TOOLCHAIN_GLOBS:
            candidates.append(sdk_root / rel)
    candidates.append(Path.home() / "host-tools/gcc/riscv64-linux-musl-x86_64/bin")
    for cand in candidates:
        if cand.is_dir() and any((cand / name).is_file() for names in COMPILERS.values() for name in names):
            return cand, None
    for names in COMPILERS.values():
        for name in names:
            found = shutil.which(name)
            if found:
                return Path(found).parent, None
    return None, Path("RISC-V musl toolchain not found; use --toolchain-bin or TOOLCHAIN_BIN")


def compiler_path(toolchain_bin: Path, kind: str) -> Path:
    for name in COMPILERS[kind]:
        candidate = toolchain_bin / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{COMPILERS[kind][0]} not found in {toolchain_bin}")


def _tarinfo(name: str, mode: int, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def deterministic_tar(
    files: Iterable[tuple[Path, str]],
    out_path: Path,
    root_name: str,
    executable_suffixes: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Create a reproducible .tar.gz.

    ``files`` yields ``(source, archive-relative-path)``. Entries are sorted,
    timestamps/owners are fixed, and gzip mtime is zero, so two runs over the
    same content produce the same bytes.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(files, key=lambda item: item[1])
    manifest: list[dict[str, object]] = []

    with open(out_path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                root = _tarinfo(root_name.rstrip("/") + "/", 0o755, 0)
                root.type = tarfile.DIRTYPE
                tar.addfile(root)
                for source, rel in entries:
                    source = Path(source)
                    data = source.read_bytes()
                    mode = 0o755 if rel.endswith(executable_suffixes) else 0o644
                    info = _tarinfo(f"{root_name}/{rel}", mode, len(data))
                    tar.addfile(info, io.BytesIO(data))
                    manifest.append({"path": rel, "size": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()})
    return manifest


def package_files(demo_dir: Path, manifest: dict, binary_rel: str | None) -> list[tuple[Path, str]]:
    """Collect the payload for an installable demo archive.

    Sources are included so the archive doubles as a developer handoff. Build
    outputs, evaluation results, caches and VCS data are excluded.
    """
    demo_dir = Path(demo_dir)
    skip_parts = {".git", "dist", "build", "__pycache__", ".pytest_cache"}
    files: list[tuple[Path, str]] = []
    for path in sorted(demo_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(demo_dir)
        if any(part in skip_parts for part in rel.parts):
            continue
        # Keep provenance and reference code; drop baseline/device outputs.
        if rel.parts[0] == "eval" and not (len(rel.parts) > 1
                                           and rel.parts[1] in ("input", "labels", "reference")):
            continue
        files.append((path, rel.as_posix()))
    if binary_rel:
        binary = demo_dir / binary_rel
        if binary.is_file() and not any(rel == Path(binary_rel).as_posix() for _, rel in files):
            files.append((binary, Path(binary_rel).as_posix()))
    return files