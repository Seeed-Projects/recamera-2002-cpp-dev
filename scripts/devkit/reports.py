"""Stage-separated reports.

Every command reports the same six evidence stages. A stage that was not
executed stays ``not_run``; it is never inferred from another stage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import now_iso, write_json

STAGES = (
    "static",
    "offline",
    "host-baseline",
    "device-upload",
    "device-run",
    "task-quality",
)
STATUSES = ("passed", "failed", "not_run", "unverified")


def new_report(kind: str, demo: str | None = None, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "generated_at": now_iso(),
        "demo": demo,
        "stages": {stage: {"status": "not_run"} for stage in STAGES},
    }
    if details:
        report["details"] = details
    return report


def set_stage(
    report: dict[str, Any],
    stage: str,
    status: str,
    detail: str = "",
    evidence: dict[str, Any] | None = None,
) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    entry: dict[str, Any] = {"status": status}
    if detail:
        entry["detail"] = detail
    if evidence:
        entry["evidence"] = evidence
    report.setdefault("stages", {})[stage] = entry


def report_ok(report: dict[str, Any]) -> bool:
    """True when no executed stage failed. not_run/unverified are allowed."""
    return all(entry.get("status") != "failed" for entry in report.get("stages", {}).values())


def write_report(path: Path, report: dict[str, Any]) -> None:
    report["generated_at"] = now_iso()
    write_json(path, report)