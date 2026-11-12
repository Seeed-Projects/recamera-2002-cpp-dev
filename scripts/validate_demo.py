#!/usr/bin/env python3
"""Offline validation for a reCamera demo workspace.

Never touches a device. Validates demo.json, source layout, model hashes,
evaluation contract, service-control safety and accidental secret leaks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    ValidationResult,
    load_contracts,
    load_manifest,
    now_iso,
    print_result,
    validate_manifest,
    write_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("demo", "deliver", "publish"), default="demo",
                        help="deliver adds publication-grade checks; publish adds distribution checks")
    parser.add_argument("--report", type=Path, help="write a JSON report")
    args = parser.parse_args(argv)

    demo_dir = args.demo_dir.resolve()
    if not demo_dir.is_dir():
        print(f"ERROR: demo directory not found: {demo_dir}", file=sys.stderr)
        return 1

    try:
        manifest, manifest_path = load_manifest(demo_dir)
    except (FileNotFoundError, ValueError) as exc:
        result = ValidationResult(errors=[str(exc)])
        print_result(result, "validate_demo")
        return 1

    result = validate_manifest(manifest, demo_dir, load_contracts(), mode=args.mode)
    print_result(result, "validate_demo")
    print(f"  manifest: {manifest_path}")
    print(f"  mode: {args.mode}")

    if args.report:
        report = {
            "schema_version": 1,
            "kind": "demo-validation",
            "generated_at": now_iso(),
            "demo_dir": str(demo_dir),
            "manifest": str(manifest_path),
            "mode": args.mode,
            "result": result.as_dict(),
        }
        write_json(args.report, report)
        print(f"  report: {args.report}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())