#!/usr/bin/env python3
"""Fetch an official reCamera model and verify it against the asset lock.

Downloads only from configs/model-assets.lock.json. Nothing is installed or
executed; the file is written into the demo's models/ directory and checked by
size and SHA-256.

    python3 scripts/fetch_models.py --name yolo11n --out ~/reCamera_demo/foo/models
    python3 scripts/fetch_models.py --list
    python3 scripts/fetch_models.py --name person --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, load_lock, now_iso, read_json, write_json  # noqa: E402

LOCK_NAME = "model-assets.lock.json"
USER_AGENT = "recamera-demo-toolkit/1.0"


def load_assets() -> dict:
    lock = load_lock(LOCK_NAME, SKILL_ROOT)
    if not lock:
        raise FileNotFoundError(f"configs/{LOCK_NAME} is missing")
    return lock


def download(url: str, dest: Path, timeout: int = 60) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as fh:
        shutil.copyfileobj(response, fh)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_one(name: str, asset: dict, base_url: str, out_dir: Path, force: bool,
              allow_unpinned: bool, dry_run: bool) -> dict:
    filename = asset.get("file")
    if not filename:
        return {"name": name, "ok": False, "error": "lock entry has no file"}
    target = out_dir / filename
    expected_sha = asset.get("sha256")
    expected_size = asset.get("size")
    if not expected_sha and not allow_unpinned:
        return {"name": name, "ok": False, "error": "no sha256 in lock; pass --allow-unpinned"}
    if target.is_file() and not force:
        actual = sha256_file(target)
        if expected_sha and actual == expected_sha:
            return {"name": name, "ok": True, "path": str(target), "cached": True, "sha256": actual}
        return {"name": name, "ok": False, "error": f"{target} exists but hash differs; use --force"}

    url = base_url.rstrip("/") + "/" + filename
    if dry_run:
        return {"name": name, "ok": True, "dry_run": True, "url": url, "target": str(target)}

    with tempfile.TemporaryDirectory(prefix="recamera-model-") as tmp:
        tmp_path = Path(tmp) / filename
        try:
            download(url, tmp_path)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"name": name, "ok": False, "error": f"download failed: {exc}", "url": url}
        size = tmp_path.stat().st_size
        if expected_size and size != expected_size:
            return {"name": name, "ok": False, "error": f"size mismatch: expected {expected_size}, got {size}"}
        actual = sha256_file(tmp_path)
        if expected_sha and actual != expected_sha:
            return {"name": name, "ok": False, "error": f"sha256 mismatch: expected {expected_sha}, got {actual}"}
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_path), target)
    return {"name": name, "ok": True, "path": str(target), "size": size, "sha256": actual, "url": url}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", action="append", default=[], help="asset name from the lock (repeatable)")
    parser.add_argument("--out", type=Path, default=Path("models"), help="destination directory")
    parser.add_argument("--file", type=Path, help="write a JSON report here")
    parser.add_argument("--list", action="store_true", help="list available asset names")
    parser.add_argument("--all", action="store_true", help="fetch every locked asset")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    parser.add_argument("--allow-unpinned", action="store_true", help="allow an entry without sha256")
    parser.add_argument("--dry-run", action="store_true", help="print the URL plan without downloading")
    args = parser.parse_args(argv)

    try:
        lock = load_assets()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    assets = lock.get("assets", {})
    base_url = lock.get("base_url", "")

    if args.list:
        for name, asset in sorted(assets.items()):
            print(f"{name:20s} {asset.get('file')}  {asset.get('size', '?')} bytes  {asset.get('task', '')}")
        return 0

    names = sorted(assets) if args.all else args.name
    if not names:
        print("ERROR: pass --name, --all, or --list", file=sys.stderr)
        return 2
    unknown = [n for n in names if n not in assets]
    if unknown:
        print(f"ERROR: unknown asset(s): {unknown}; known: {sorted(assets)}", file=sys.stderr)
        return 2

    results = []
    failures = 0
    for name in names:
        result = fetch_one(name, assets[name], base_url, args.out, args.force,
                           args.allow_unpinned, args.dry_run)
        results.append(result)
        status = "OK  " if result.get("ok") else "FAIL"
        print(f"{status} {name}: {result.get('path') or result.get('url') or result.get('error')}")
        if not result.get("ok"):
            failures += 1

    report = {
        "schema_version": 1,
        "kind": "model-fetch",
        "generated_at": now_iso(),
        "out": str(args.out),
        "dry_run": args.dry_run,
        "results": results,
    }
    if args.file:
        write_json(args.file, report)
        print(f"report: {args.file}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())