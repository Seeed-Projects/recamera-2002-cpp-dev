import json

import evaluate_demo


def write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def run(tmp_path, baseline, device, task, extra=()):
    base = tmp_path / "baseline.jsonl"
    dev = tmp_path / "device.jsonl"
    write_jsonl(base, baseline)
    write_jsonl(dev, device)
    args = [
        "--baseline", str(base), "--device", str(dev), "--task", task,
        "--out-dir", str(tmp_path / "out"),
        "--threshold", "min_samples=1",
    ] + list(extra)
    return evaluate_demo.main(args), json.loads((tmp_path / "out" / "qa_report.json").read_text())


def box(cls, conf, box):
    return {"cls": cls, "conf": conf, "box": box}


def test_detection_perfect(tmp_path):
    frames = [
        {"frame": 0, "objects": [box("person", 0.9, [10, 10, 50, 50])]},
        {"frame": 1, "objects": []},
    ]
    code, report = run(tmp_path, frames, frames, "detection")
    assert code == 0
    assert report["metrics"]["f1"] == 1.0
    assert report["samples"]["matched"] == 2


def test_detection_miss_fails(tmp_path):
    baseline = [{"frame": 0, "objects": [box("person", 0.9, [10, 10, 50, 50])]}]
    device = [{"frame": 0, "objects": []}]
    code, report = run(tmp_path, baseline, device, "detection")
    assert code == 1
    assert report["metrics"]["f1"] == 0.0
    assert report["reasons"]


def test_detection_low_iou_counts_fp_and_fn(tmp_path):
    baseline = [{"frame": 0, "objects": [box("person", 0.9, [0, 0, 10, 10])]}]
    device = [{"frame": 0, "objects": [box("person", 0.9, [50, 50, 60, 60])]}]
    code, report = run(tmp_path, baseline, device, "detection")
    assert code == 1
    assert report["metrics"]["false_positives"] == 1
    assert report["metrics"]["false_negatives"] == 1


def test_missing_frame_fails(tmp_path):
    baseline = [
        {"frame": 0, "objects": []},
        {"frame": 1, "objects": []},
    ]
    device = [{"frame": 0, "objects": []}]
    code, report = run(tmp_path, baseline, device, "detection")
    assert code == 1
    assert report["samples"]["missing"] == 1
    assert any("missing frames" in reason for reason in report["reasons"])


def test_classification_pass(tmp_path):
    baseline = [{"frame": i, "top1": "cat"} for i in range(3)]
    device = [{"frame": 0, "top1": "cat"}, {"frame": 1, "top1": "cat"}, {"frame": 2, "top1": "dog"}]
    code, report = run(tmp_path, baseline, device, "classification")
    assert report["metrics"]["top1_agreement"] == 2 / 3
    assert code == 0


def test_ocr_similarity(tmp_path):
    baseline = [{"frame": 0, "text": "hello world"}]
    device = [{"frame": 0, "text": "hello world"}]
    code, report = run(tmp_path, baseline, device, "ocr")
    assert code == 0
    assert report["metrics"]["text_similarity"] == 1.0


def test_ocr_low_similarity_fails(tmp_path):
    baseline = [{"frame": 0, "text": "hello world"}]
    device = [{"frame": 0, "text": "xyz"}]
    code, report = run(tmp_path, baseline, device, "ocr")
    assert code == 1


def test_segmentation_iou(tmp_path):
    baseline = [{"frame": 0, "iou": 1.0}, {"frame": 1, "iou": 1.0}]
    device = [{"frame": 0, "iou": 0.9}, {"frame": 1, "iou": 0.8}]
    code, report = run(tmp_path, baseline, device, "segmentation")
    assert code == 0
    assert abs(report["metrics"]["miou"] - 0.85) < 1e-6


def test_pose_pck_normalized(tmp_path):
    kp = [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]]
    baseline = [{"frame": 0, "keypoints": kp}]
    device = [{"frame": 0, "keypoints": kp}]
    code, report = run(tmp_path, baseline, device, "pose")
    assert code == 0
    assert report["metrics"]["pck"] == 1.0


def test_custom_metric(tmp_path):
    baseline = [{"frame": 0, "value": 0.8}, {"frame": 1, "value": 1.0}]
    device = [{"frame": 0, "value": 0.8, "metric": "depth_abs_rel"}, {"frame": 1, "value": 1.0}]
    code, report = run(tmp_path, baseline, device, "custom", ["--threshold", "pass_score=0.6"])
    assert code == 0
    assert report["metrics"]["custom"] == 0.9


def test_malformed_jsonl_returns_2(tmp_path):
    base = tmp_path / "baseline.jsonl"
    dev = tmp_path / "device.jsonl"
    base.write_text("{not json}\n", encoding="utf-8")
    dev.write_text("{}\n", encoding="utf-8")
    code = evaluate_demo.main([
        "--baseline", str(base), "--device", str(dev), "--task", "detection",
        "--out-dir", str(tmp_path / "out"),
    ])
    assert code == 2


def test_duplicate_frame_rejected(tmp_path):
    base = tmp_path / "baseline.jsonl"
    dev = tmp_path / "device.jsonl"
    write_jsonl(base, [{"frame": 0, "objects": []}, {"frame": 0, "objects": []}])
    write_jsonl(dev, [{"frame": 0, "objects": []}])
    code = evaluate_demo.main([
        "--baseline", str(base), "--device", str(dev), "--task", "detection",
        "--out-dir", str(tmp_path / "out"),
    ])
    assert code == 2