#!/usr/bin/env python3
"""Append a validated demo result to a per-user records file.

By default the records file lives in the user data directory
(``$XDG_DATA_HOME/recamera-2002-cpp-dev/demo-records.md``), never in the
installed skill directory, so upgrading the skill cannot overwrite personal
history. The shipped ``knowhubs/reCamera_KnowHub/demo-records.md`` is only the
seed/template. New entries use the English record format below; legacy records
files keep their original Chinese run-records heading (matched through an
escaped pattern) so the marker is still found. Duplicate name+date entries are
rejected, and the records file is backed up first.

Record format::

    ### <demo_name> (<YYYY-MM-DD>)
    - Status: ✅ success / ❌ failed
    - Capability: <one line>
    - Model files: <files or "none">
    - Dependencies: <deps or "none">
    - Build issues: <issue and fix, or "none">
    - Verification: <actual observed result>
    - Lessons: <reusable conclusion or "none">
    - Wiki issues: <optional>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, now_iso, write_json  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REQUIRED = ("name", "capability", "verification")
SEED_RECORDS = SKILL_ROOT / "knowhubs" / "reCamera_KnowHub" / "demo-records.md"
DATA_DIR_NAME = "recamera-2002-cpp-dev"
DATA_DIR_ENV = "RECAMERA_DATA_DIR"

# English heading for the run-records section, plus the legacy Chinese heading
# kept as an escaped pattern so seeded/legacy records files still parse.
RUN_SECTION_RE = re.compile(r"^## (?:Demos run so far|\u5df2\u8dd1\u8fc7\u7684 demo)\s*$", re.M)
# Legacy Chinese tokens accepted on input (written records always use English).
LEGACY_NONE = "\u65e0"
LEGACY_SUCCESS = "\u6210\u529f"
LEGACY_FAILED = "\u5931\u8d25"


def default_records_path() -> Path:
    """Per-user records path; ``RECAMERA_DATA_DIR`` overrides the data dir."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        base = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share") / DATA_DIR_NAME
    return base / "demo-records.md"


def read_records(path: Path, dry_run: bool, seed: bool = False) -> str:
    """Read the records file; seed the default one from the shipped template."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if not seed:
        raise OSError(f"records file not found: {path}")
    if not SEED_RECORDS.is_file():
        raise OSError(f"records file missing and no template at {SEED_RECORDS}")
    text = SEED_RECORDS.read_text(encoding="utf-8")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"initialized records: {path}")
    return text


def one_line(value: str) -> str:
    return " ".join(str(value).split())


def none_or(value: object) -> str:
    """Collapse missing values and the legacy Chinese 'none' token to 'none'."""
    text = one_line(value if value is not None else "")
    if not text or text == LEGACY_NONE:
        return "none"
    return text


def normalize(result: dict) -> dict:
    errors: list[str] = []
    for key in REQUIRED:
        if not str(result.get(key, "")).strip():
            errors.append(f"missing required field: {key}")
    name = str(result.get("name", ""))
    if name and not NAME_RE.match(name):
        errors.append("name must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    day = str(result.get("date") or date.today().isoformat())
    if not DATE_RE.match(day):
        errors.append("date must be YYYY-MM-DD")
    status = result.get("status", "")
    if status in ("success", "✅", LEGACY_SUCCESS, "pass", "passed"):
        status_text = "✅ success"
    elif status in ("failed", "❌", LEGACY_FAILED, "fail"):
        status_text = "❌ failed"
    else:
        errors.append("status must be success|failed (or ✅/❌)")
        status_text = ""
    return {
        "errors": errors,
        "name": name,
        "date": day,
        "status": status_text,
        "capability": one_line(result.get("capability", "")),
        "model_files": none_or(result.get("model_files")),
        "dependencies": none_or(result.get("dependencies")),
        "build_issues": none_or(result.get("build_issues")),
        "verification": one_line(result.get("verification", "")),
        "lessons": none_or(result.get("lessons")),
        "wiki_issues": one_line(result.get("wiki_issues", "")),
        "evidence": result.get("evidence", {}),
    }


def render_entry(entry: dict) -> str:
    lines = [
        f"### {entry['name']} ({entry['date']})",
        f"- Status: {entry['status']}",
        f"- Capability: {entry['capability']}",
        f"- Model files: {entry['model_files']}",
        f"- Dependencies: {entry['dependencies']}",
        f"- Build issues: {entry['build_issues']}",
        f"- Verification: {entry['verification']}",
        f"- Lessons: {entry['lessons']}",
    ]
    if entry["wiki_issues"]:
        lines.append(f"- Wiki issues: {entry['wiki_issues']}")
    return "\n".join(lines) + "\n"


def insert_entry(text: str, entry: dict) -> str:
    match = RUN_SECTION_RE.search(text)
    if not match:
        raise ValueError("demo-records.md has no run-records section "
                         "(expected '## Demos run so far')")
    search_from = match.end()
    next_section = text.find("\n## ", search_from)
    if next_section < 0:
        next_section = len(text)
    block = render_entry(entry)
    before = text[:next_section].rstrip() + "\n\n"
    after = text[next_section:].lstrip("\n")
    return before + block + ("\n" + after if after else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path, help="JSON result file")
    parser.add_argument("--records", type=Path, default=None,
                        help="records markdown (default: user data dir, "
                             "seeded from the shipped demo-records.md)")
    parser.add_argument("--report-dir", type=Path, help="also write <dir>/<name>/DEPLOY_REPORT.md")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read result: {exc}", file=sys.stderr)
        return 1
    entry = normalize(raw)
    if entry["errors"]:
        for error in entry["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    records = args.records if args.records is not None else default_records_path()
    try:
        text = read_records(records, args.dry_run, seed=args.records is None)
    except OSError as exc:
        print(f"ERROR: cannot read records: {exc}", file=sys.stderr)
        return 1
    if re.search(rf"^### {re.escape(entry['name'])} \({re.escape(entry['date'])}\)$", text, re.M):
        print(f"ERROR: {entry['name']} ({entry['date']}) already exists in {records}", file=sys.stderr)
        return 1

    new_text = insert_entry(text, entry)
    block = render_entry(entry)
    if args.dry_run:
        print(block)
        print(f"dry-run: would append to {records}")
        return 0

    backup = records.with_suffix(records.suffix + f".bak-{now_iso().replace(':', '').replace('-', '')}")
    backup.write_text(text, encoding="utf-8")
    records.write_text(new_text, encoding="utf-8")

    report = {
        "schema_version": 1,
        "kind": "demo-record",
        "generated_at": now_iso(),
        "records": str(records),
        "backup": str(backup),
        "entry": entry,
    }
    if args.report:
        write_json(args.report, report)

    if args.report_dir:
        demo_report = args.report_dir / entry["name"] / "DEPLOY_REPORT.md"
        demo_report.parent.mkdir(parents=True, exist_ok=True)
        demo_report.write_text(render_deploy_report(entry, raw), encoding="utf-8")
        print(f"deploy report: {demo_report}")

    print(f"recorded: {entry['name']} ({entry['date']}) -> {records}")
    print(f"backup:   {backup}")
    return 0


def render_deploy_report(entry: dict, raw: dict) -> str:
    lines = [
        f"# {entry['name']} Deploy Report",
        "",
        f"- Date: {entry['date']}",
        f"- Status: {entry['status']}",
        f"- Capability: {entry['capability']}",
        f"- Model files: {entry['model_files']}",
        f"- Dependencies: {entry['dependencies']}",
        f"- Build issues: {entry['build_issues']}",
        f"- Verification: {entry['verification']}",
        f"- Lessons: {entry['lessons']}",
    ]
    if entry["wiki_issues"]:
        lines.append(f"- Wiki issues: {entry['wiki_issues']}")
    if raw.get("evidence"):
        lines += ["", "## Evidence", "", "```json", json.dumps(raw["evidence"], ensure_ascii=False, indent=2), "```"]
    lines += ["", "## Evidence boundaries", "",
              "Statuses in this report only cover items that were actually executed and recorded; "
              "device, hardware or quality-gate items that were not executed must be explicitly "
              "marked unverified.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
