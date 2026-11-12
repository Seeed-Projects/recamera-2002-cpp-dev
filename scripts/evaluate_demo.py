#!/usr/bin/env python3
"""Score a reCamera run against an official Python/NVIDIA baseline.

Reads baseline.jsonl and device.jsonl, aligns frames, applies the evaluation
contract thresholds, and writes qa_report.json / qa_report.md / failures.json.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    STAGES,
    load_contracts,
    load_jsonl,
    now_iso,
    write_json,
)


def load_frames(path: Path) -> dict[int, dict]:
    frames: dict[int, dict] = {}
    for index, record in enumerate(load_jsonl(path)):
        frame = record.get("frame", index)
        if not isinstance(frame, int):
            raise ValueError(f"{path}: frame must be an integer, got {frame!r}")
        if frame in frames:
            raise ValueError(f"{path}: duplicate frame {frame}")
        frames[frame] = record
    return frames


def iou(a: list[float], b: list[float]) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def detection_match(baseline: dict, device: dict, iou_thr: float, conf_thr: float) -> tuple[int, int, int, list[float], dict[str, list[int]]]:
    base = [o for o in baseline.get("objects", []) if float(o.get("conf", 1.0)) >= conf_thr]
    dev = [o for o in device.get("objects", []) if float(o.get("conf", 1.0)) >= conf_thr]
    used = [False] * len(dev)
    tp = fp = fn = 0
    ious: list[float] = []
    per_class: dict[str, list[int]] = {}
    for obj in sorted(base, key=lambda o: -float(o.get("conf", 1.0))):
        cls = str(obj.get("cls"))
        best_idx, best_iou = -1, 0.0
        for idx, cand in enumerate(dev):
            if used[idx] or str(cand.get("cls")) != cls:
                continue
            value = iou(obj.get("box", []), cand.get("box", []))
            if value > best_iou:
                best_idx, best_iou = idx, value
        stats = per_class.setdefault(cls, [0, 0])  # [support, hits]
        stats[0] += 1
        if best_idx >= 0 and best_iou >= iou_thr:
            used[best_idx] = True
            tp += 1
            stats[1] += 1
            ious.append(best_iou)
        else:
            fn += 1
    fp = sum(1 for flag in used if not flag)
    return tp, fp, fn, ious, per_class


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return previous[-1]


def text_similarity(a: str, b: str) -> float:
    a, b = " ".join((a or "").split()), " ".join((b or "").split())
    return 1.0 - levenshtein(a, b) / max(len(a), len(b), 1)


def diagonal(record: dict) -> float:
    width = float(record.get("width", 0) or 0)
    height = float(record.get("height", 0) or 0)
    if width > 0 and height > 0:
        return math.hypot(width, height)
    return math.sqrt(2.0)


def point(entry) -> tuple[float, float]:
    if isinstance(entry, dict):
        return float(entry.get("x", 0)), float(entry.get("y", 0))
    return float(entry[0]), float(entry[1])


def pose_pck(baseline: dict, device: dict, ratio: float) -> float:
    base = baseline.get("keypoints_norm") or baseline.get("keypoints") or []
    dev = device.get("keypoints_norm") or device.get("keypoints") or []
    normalized = ("keypoints_norm" in baseline and bool(base)) or (
        bool(base) and all(0.0 <= coord <= 1.0 for entry in base for coord in point(entry)))
    scale = math.sqrt(2.0) if normalized else diagonal(device or baseline)
    total = hits = 0
    for index, expected in enumerate(base):
        visibility = float(expected.get("v", expected.get("conf", 1.0))) if isinstance(expected, dict) else 1.0
        if visibility <= 0:
            continue
        total += 1
        if index < len(dev):
            ex, ey = point(expected)
            dx, dy = point(dev[index])
            if math.hypot(ex - dx, ey - dy) <= ratio * scale:
                hits += 1
    return hits / total if total else 1.0


def evaluate(task: str, baseline: dict[int, dict], device: dict[int, dict], opts: dict) -> dict:
    all_frames = sorted(set(baseline) | set(device))
    missing = sorted(set(baseline) - set(device))
    extra = sorted(set(device) - set(baseline))
    matched = sorted(set(baseline) & set(device))
    failures: list[dict] = []
    notes: list[str] = []
    metrics: dict[str, float] = {}
    per_class: dict[str, dict] = {}

    if task == "detection":
        tp = fp = fn = 0
        ious: list[float] = []
        class_totals: dict[str, list[int]] = {}
        for frame in all_frames:
            base_record = baseline.get(frame, {"objects": []})
            dev_record = device.get(frame, {"objects": []})
            f_tp, f_fp, f_fn, f_ious, f_classes = detection_match(
                base_record, dev_record, opts["iou_threshold"], opts["confidence_threshold"])
            tp += f_tp
            fp += f_fp
            fn += f_fn
            ious.extend(f_ious)
            for cls, (support, hits) in f_classes.items():
                stats = class_totals.setdefault(cls, [0, 0])
                stats[0] += support
                stats[1] += hits
            if f_fn and frame not in missing:
                failures.append({"frame": frame, "reason": f"false_negatives={f_fn}"})
        for cls, (support, hits) in sorted(class_totals.items()):
            per_class[cls] = {"support": support, "hits": hits,
                              "recall": hits / support if support else 1.0}
        metrics["precision"] = tp / (tp + fp) if tp + fp else 1.0
        metrics["recall"] = tp / (tp + fn) if tp + fn else 1.0
        metrics["f1"] = (2 * metrics["precision"] * metrics["recall"] /
                         (metrics["precision"] + metrics["recall"])) if (metrics["precision"] + metrics["recall"]) else 0.0
        metrics["mean_matched_iou"] = sum(ious) / len(ious) if ious else 0.0
        metrics["true_positives"] = tp
        metrics["false_positives"] = fp
        metrics["false_negatives"] = fn
    elif task == "classification":
        hits = 0
        for frame in matched:
            expected = str(baseline[frame].get("top1"))
            actual = str(device[frame].get("top1"))
            if expected == actual:
                hits += 1
            else:
                failures.append({"frame": frame, "reason": f"top1 {actual!r} != {expected!r}"})
        metrics["top1_agreement"] = hits / len(matched) if matched else 0.0
    elif task == "ocr":
        similarities = []
        for frame in matched:
            expected = str(baseline[frame].get("text", ""))
            actual = str(device[frame].get("text", ""))
            value = text_similarity(expected, actual)
            similarities.append(value)
            if value < opts["pass_score"]:
                failures.append({"frame": frame, "reason": f"text similarity {value:.3f}"})
        metrics["text_similarity"] = sum(similarities) / len(similarities) if similarities else 0.0
        metrics["char_accuracy"] = metrics["text_similarity"]
    elif task == "segmentation":
        values = []
        for frame in matched:
            record = device[frame]
            if "iou" in record:
                value = float(record["iou"])
            elif "class_iou" in record and isinstance(record["class_iou"], dict) and record["class_iou"]:
                value = sum(float(v) for v in record["class_iou"].values()) / len(record["class_iou"])
            else:
                notes.append(f"frame {frame}: no iou/class_iou; skipped")
                continue
            values.append(value)
            if value < opts["pass_score"]:
                failures.append({"frame": frame, "reason": f"iou {value:.3f}"})
        metrics["miou"] = sum(values) / len(values) if values else 0.0
    elif task == "pose":
        values = []
        for frame in matched:
            value = pose_pck(baseline[frame], device[frame], opts["pck_ratio"])
            values.append(value)
            if value < opts["pass_score"]:
                failures.append({"frame": frame, "reason": f"pck {value:.3f}"})
        metrics["pck"] = sum(values) / len(values) if values else 0.0
    elif task in ("custom", "depth"):
        values = []
        for frame in matched:
            record = device[frame]
            if "value" not in record:
                notes.append(f"frame {frame}: no custom metric value; skipped")
                continue
            values.append(float(record["value"]))
        metrics["custom"] = sum(values) / len(values) if values else 0.0
        metric_name = device.get(matched[0], {}).get("metric") if matched else None
        if metric_name:
            notes.append(f"custom metric defined by inputs: {metric_name}")
    else:
        raise ValueError(f"unsupported task: {task}")

    for frame in missing:
        failures.append({"frame": frame, "reason": "missing in device output"})
    for frame in extra:
        failures.append({"frame": frame, "reason": "not present in baseline"})

    support = len(baseline)
    metrics["samples"] = support
    metrics["matched_frames"] = len(matched)
    metrics["missing_frames"] = len(missing)
    return {
        "metrics": metrics,
        "per_class": per_class,
        "failures": failures,
        "notes": notes,
        "samples": {"baseline_frames": len(baseline), "device_frames": len(device),
                    "matched": len(matched), "missing": len(missing), "extra": len(extra)},
    }


def resolve_thresholds(task: str, task_cfg: dict, manifest_thresholds: dict, cli_thresholds: dict) -> dict:
    thresholds = {
        "pass_score": task_cfg.get("threshold"),
        "iou_threshold": None,
        "confidence_threshold": None,
        "max_missing_frames": None,
        "min_samples": None,
        "pck_ratio": None,
        "per_class_recall": task_cfg.get("per_class_recall"),
    }
    for key in thresholds:
        if manifest_thresholds.get(key) is not None:
            thresholds[key] = float(manifest_thresholds[key])
    for key, value in cli_thresholds.items():
        if key in thresholds:
            thresholds[key] = float(value)
        else:
            raise ValueError(f"unknown threshold: {key}")
    return {k: v for k, v in thresholds.items() if v is not None}


def gate(task: str, metrics: dict, thresholds: dict, per_class: dict, critical: list[str],
         missing: int, samples: int, allow_small: bool, strict: bool,
         dataset_ok: bool, notes: list[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    primary = {"detection": "f1", "classification": "top1_agreement", "ocr": "text_similarity",
               "segmentation": "miou", "pose": "pck"}.get(task, "custom")
    limit = thresholds.get(primary)
    if limit is None and primary != "custom":
        limit = thresholds.get("pass_score")
    if limit is None:
        if strict:
            reasons.append(f"no threshold defined for {primary}")
    elif metrics.get(primary, 0.0) < limit:
        reasons.append(f"{primary} {metrics.get(primary, 0.0):.3f} < {limit:.3f}")
    if task == "detection" and critical:
        for cls in critical:
            recall = per_class.get(cls, {}).get("recall", 0.0)
            limit_cls = thresholds.get("per_class_recall", 0.6)
            if recall < limit_cls:
                reasons.append(f"class {cls} recall {recall:.3f} < {limit_cls:.3f}")
    if samples < int(thresholds.get("min_samples", 0) or 0) and not allow_small:
        reasons.append(f"samples {samples} < min_samples {thresholds.get('min_samples')}")
    if missing > int(thresholds.get("max_missing_frames", 0) or 0):
        reasons.append(f"missing frames {missing} > max_missing_frames {thresholds.get('max_missing_frames')}")
    if not dataset_ok and strict:
        reasons.append("dataset manifest is missing or incomplete")
    return not reasons, reasons


def render_markdown(report: dict) -> str:
    lines = [
        f"# QA Report: {report['task']}",
        "",
        f"- Result: {'PASS' if report['passed'] else 'FAIL'}",
        f"- Generated at: {report['generated_at']}",
        f"- baseline: `{report['inputs']['baseline']}`",
        f"- device: `{report['inputs']['device']}`",
        f"- Samples: baseline {report['samples']['baseline_frames']} frames, "
        f"device {report['samples']['device_frames']} frames, "
        f"matched {report['samples']['matched']} frames, missing {report['samples']['missing']} frames",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Threshold |",
        "|---|---|---|",
    ]
    for key, value in report["metrics"].items():
        limit = report["thresholds"].get(key, "")
        if not limit and key == report.get("primary_metric"):
            limit = report["thresholds"].get("pass_score", "")
        shown = f"{value:.4f}" if isinstance(value, float) else str(value)
        lines.append(f"| {key} | {shown} | {limit} |")
    if report.get("per_class"):
        lines += ["", "## Per class", "", "| Class | support | hits | recall |", "|---|---|---|---|"]
        for cls, stats in report["per_class"].items():
            lines.append(f"| {cls} | {stats['support']} | {stats['hits']} | {stats['recall']:.4f} |")
    if report["failures"]:
        lines += ["", "## Failed frames", ""]
        for failure in report["failures"][:100]:
            lines.append(f"- frame {failure['frame']}: {failure['reason']}")
        if len(report["failures"]) > 100:
            lines.append(f"- ... {len(report['failures']) - 100} more")
    if report["notes"]:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in report["notes"]]
    if report["reasons"]:
        lines += ["", "## Failure reasons", ""] + [f"- {reason}" for reason in report["reasons"]]
    lines += ["", "## Evidence boundary", "",
              "This report only compares the baseline against device output on fixed inputs; "
              "it does not prove hardware real-time behavior, generalization to other scenes, "
              "or the Web UI/recording pipeline.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--device", required=True, type=Path)
    parser.add_argument("--task", choices=("detection", "classification", "ocr", "segmentation", "pose", "depth", "custom"))
    parser.add_argument("--demo-dir", type=Path, help="read task/thresholds from demo.json")
    parser.add_argument("--threshold", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--critical-class", action="append", default=[])
    parser.add_argument("--dataset-manifest", type=Path)
    parser.add_argument("--allow-small-sample", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    contract_defaults, eval_contract = load_contracts()
    task = args.task
    manifest_thresholds: dict = {}
    if args.demo_dir:
        from demo_lib import load_manifest
        manifest, _ = load_manifest(args.demo_dir)
        task = task or manifest["evaluation"]["task"]
        manifest_thresholds = manifest["evaluation"].get("thresholds", {})
    if not task:
        print("ERROR: --task or --demo-dir is required", file=sys.stderr)
        return 2

    defaults = eval_contract.get("defaults", {})
    task_cfg = eval_contract["tasks"][task]
    cli_thresholds = {}
    for item in args.threshold:
        if "=" not in item:
            print(f"ERROR: --threshold expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        cli_thresholds[key] = float(value)
    thresholds = resolve_thresholds(task, task_cfg, manifest_thresholds, cli_thresholds)
    for key in ("iou_threshold", "confidence_threshold", "max_missing_frames", "min_samples", "pck_ratio"):
        if key not in thresholds:
            thresholds[key] = float(defaults[key])
    if not cli_thresholds or "pass_score" not in cli_thresholds:
        thresholds.setdefault("pass_score", task_cfg.get("threshold"))

    dataset_ok = True
    dataset_notes: list[str] = []
    if args.dataset_manifest:
        try:
            dataset = json.loads(args.dataset_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read dataset manifest: {exc}", file=sys.stderr)
            return 2
        requirement = eval_contract.get("dataset_manifest", {}).get("required_fields", [])
        missing = [field for field in requirement if not dataset.get(field)]
        dataset_ok = not missing
        if missing:
            dataset_notes.append(f"dataset manifest missing fields: {missing}")
    else:
        dataset_ok = False
        dataset_notes.append("no --dataset-manifest supplied: input provenance is unverified")

    try:
        baseline = load_frames(args.baseline)
        device = load_frames(args.device)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result = evaluate(task, baseline, device, {
        "iou_threshold": float(thresholds.get("iou_threshold", 0.5)),
        "confidence_threshold": float(thresholds.get("confidence_threshold", 0.25)),
        "pass_score": float(thresholds.get("pass_score") or 0.6),
        "pck_ratio": float(thresholds.get("pck_ratio", 0.2)),
    })
    passed, reasons = gate(
        task, result["metrics"], thresholds, result["per_class"], args.critical_class,
        result["samples"]["missing"], result["samples"]["baseline_frames"],
        args.allow_small_sample, args.strict, dataset_ok, result["notes"],
    )
    report = {
        "schema_version": 1,
        "kind": "demo-evaluation",
        "task": task,
        "primary_metric": {"detection": "f1", "classification": "top1_agreement",
                           "ocr": "text_similarity", "segmentation": "miou",
                           "pose": "pck"}.get(task, "custom"),
        "generated_at": now_iso(),
        "inputs": {
            "baseline": str(args.baseline),
            "device": str(args.device),
            "dataset_manifest": str(args.dataset_manifest) if args.dataset_manifest else None,
        },
        "samples": result["samples"],
        "metrics": result["metrics"],
        "per_class": result["per_class"],
        "thresholds": thresholds,
        "passed": passed,
        "reasons": reasons,
        "failures": result["failures"],
        "notes": result["notes"] + dataset_notes,
        "stages": {
            stage: {"status": (
                ("passed" if passed else "failed") if stage == "task-quality"
                else "not_run"
            )}
            for stage in STAGES
        },
    }
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "qa_report.json", report)
    (out_dir / "qa_report.md").write_text(render_markdown(report), encoding="utf-8")
    write_json(out_dir / "failures.json", report["failures"])
    if args.report:
        write_json(args.report, report)

    print(f"qa: {'PASS' if passed else 'FAIL'} ({task})")
    for key, value in result["metrics"].items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
    for reason in reasons:
        print(f"  REASON {reason}")
    print(f"report: {out_dir / 'qa_report.md'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())