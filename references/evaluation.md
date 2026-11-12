# Evaluation and quality gates

Evaluation answers two questions: **is the input trustworthy**, and **does the device output approximate the official Python/NVIDIA pipeline**. Only when both hold can the demo be considered "running correctly".

## 1. Dataset provenance

For vision demos, identify the model's origin first, then decide the evaluation inputs:

- Model origin: paper, GitHub, Hugging Face, model card, training scripts, `data.yaml`.
- Original training/validation/test dataset names, versions, class lists, licenses.
- Input size, preprocessing, postprocessing, and metrics recommended by the official examples.

The shared cache lives at a fixed path:

```text
~/reCamera_demo/datasets/<dataset_name>/
```

Check the cache before downloading; download sources are limited to official sites, links given in the project README, Hugging Face datasets, official Kaggle mirrors, or trusted open-source mirrors.

## 2. Fixed evaluation inputs

```text
~/reCamera_demo/<demo_name>/eval/input/
├── images/            # optional, positive/negative/boundary samples
├── video/input.mp4    # same input batch; prefer 5-10 s of consecutive frames
└── manifest.json
```

`manifest.json` must contain at least (validated by `evaluate_demo.py`):

```json
{
  "source": "COCO val2017 / as specified in the project README",
  "version": "2017",
  "license": "CC BY 4.0",
  "samples": 60,
  "selection": "consecutive frames from one sequence; includes occlusion, small objects, low light",
  "cache_path": "~/reCamera_demo/datasets/coco",
  "class_map": {"0": "person"},
  "synthesized_video": false
}
```

Requirements:

- The baseline and the device use **exactly the same files**.
- Samples cover positive, negative, and boundary cases; detection tasks must not show only frames where the target is present.
- When the live camera is the selling point, also record a raw video clip as a supplement, but it cannot replace dataset evaluation.
- When the original dataset cannot be found, the report must be marked "non-original-dataset evaluation" and explain why.

Scoring still runs without `--dataset-manifest`, but the report is marked "input provenance unverified"; `--strict` fails it outright.

## 3. Host baseline

Run the same inputs on a host with an NVIDIA GPU using the **official original scripts**, producing:

```text
eval/baseline/
├── frames/           # input frames
├── visualized/       # visualized frames
├── baseline.jsonl
└── baseline.mp4
```

One record per line in `baseline.jsonl`; `frame` starts at 0, increases, and is unique.

## 4. Device output

Run the same inputs on the reCamera and export a structure-aligned `recamera.jsonl`. Detection boxes must be restored to the original image coordinate system, and the input size, threshold, NMS, and label map must be recorded.

## 5. JSONL structures

### detection

```json
{"frame": 12, "objects": [
  {"cls": "person", "conf": 0.91, "box": [120, 80, 260, 340]}
]}
```

`box` is `[x1,y1,x2,y2]` in original-image pixel coordinates. Greedy IoU matching is done per class; unmatched baseline objects count as FN, unmatched device objects count as FP.

### classification

```json
{"frame": 0, "top1": "cat"}
```

### OCR

```json
{"frame": 0, "text": "recognized text"}
```

Similarity uses normalized Levenshtein: `1 - distance / max(len(a), len(b))`.

### segmentation

```json
{"frame": 0, "iou": 0.87}
{"frame": 1, "class_iou": {"0": 0.9, "1": 0.8}}
```

Aggregated into mean IoU.

### pose

```json
{"frame": 0, "keypoints_norm": [[0.1, 0.2], [0.5, 0.5]]}
{"frame": 0, "keypoints": [[100, 120, 0.9]], "width": 640, "height": 480}
```

PCK: keypoint distance ≤ `pck_ratio × image diagonal` (use `√2` for normalized coordinates).

### custom / depth

```json
{"frame": 0, "metric": "abs_rel", "value": 0.12}
```

The report must be able to explain the metric definition; higher `value` is better. If a metric is lower-is-better, invert it in the export script first and say so in the `metric` name.

## 6. Default thresholds

From `configs/evaluation-contract.json`:

| Task | Primary metric | Default threshold |
|---|---|---|
| detection | F1 | 0.60; per-class recall can also be set |
| classification | top-1 agreement | 0.60 |
| OCR | text similarity | 0.60 |
| segmentation | mIoU | 0.60 |
| pose | PCK | 0.60 |
| custom/depth | custom | must pass `--threshold pass_score=` explicitly |

General defaults: `min_samples=10`, `iou_threshold=0.5`, `confidence_threshold=0.25`, `max_missing_frames=0`, `pck_ratio=0.2`.

Override with:

```bash
python3 scripts/evaluate_demo.py ... \
  --threshold pass_score=0.65 --threshold iou_threshold=0.45 \
  --critical-class person --critical-class car
```

`--allow-small-sample` may be added for debugging on small samples, but must not be used for final acceptance.

## 7. Quality gates and the failure loop

When a gate fails, investigate in this order:

1. Whether the input comes from the model's original dataset and whether the baseline itself is trustworthy.
2. resize/letterbox/coordinate restoration.
3. RGB/BGR, normalization, mean/std, CHW/HWC, quantized input ranges.
4. label map order, class filtering, thresholds, NMS, top-k.
5. Model-conversion accuracy, calibration data, postprocessing formulas.
6. Whether the C++ postprocessing matches the official Python.

After a fix, rerun the affected steps until it passes, or clearly record that the attempt is "not suitable to continue". Failures must also be written to the knowledge base (`status: failed`).

## 8. Report artifacts

`evaluate_demo.py` writes under `--out-dir`:

- `qa_report.json`: machine-readable, with metrics, thresholds, samples, failures, reasons.
- `qa_report.md`: human-readable, with sample counts, pass/fail, failed frames, evidence boundaries.
- `failures.json`: list of failed frames, for frame-by-frame comparison.

The report only proves "output consistency on fixed inputs"; it does not prove real-time performance, generalization to other scenes, or the Web UI/recording pipeline. Capture separate evidence for those.
