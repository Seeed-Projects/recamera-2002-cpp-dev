# Model conversion: `convert_model.py`

ONNX → TPU-MLIR → `.cvimodel`. All work happens on the host; the device is never touched.

```bash
python3 scripts/convert_model.py check-env
python3 scripts/convert_model.py inspect model.onnx
python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
  --quant i8 --dataset calibration.txt --out build/model-i8
```

## Prerequisites

- TPU-MLIR's `python/tools/` (`model_transform.py`, `run_calibration.py`, `model_deploy.py`).
- Discovery order: `--tpu-mlir-root` → `TPU_MLIR_PATH` → `~/tpu-mlir`.
- The environment where the tools live must be able to `import tpu_mlir`; `check-env` reports "tool present"
  and "module importable" separately, and returns 0 only when both pass.

## recipe.json

The schema is in `schemas/recipe.schema.json`.

```json
{
  "model_name": "yolo11n",
  "input_name": "images",
  "input_layout": "NCHW",
  "input_shape": [1, 3, 640, 640],
  "color": "RGB",
  "mean": [0, 0, 0],
  "std": [255, 255, 255],
  "resize": {"mode": "letterbox", "pad_value": 114},
  "output_names": ["output0"],
  "input_num": 100
}
```

- `mean`/`std` must be in the **model's channel order**. `std` is converted into TPU-MLIR's `--scale`.
- When `resize.mode=letterbox`, `--keep_aspect_ratio` is added.
- `quantize_table` may be a path relative to the recipe; it is used for hybrid quantization of pose/seg models.

## Pipeline

| Step | Command | Output |
|---|---|---|
| 1 | `model_transform.py` | `<name>.mlir` |
| 2 | `run_calibration.py` (i8 only) | `<name>_cali_table` |
| 3 | `model_deploy.py` | `<name>.cvimodel` |

- The output directory must be new or empty; the script never overwrites an existing build.
- The stdout/stderr of each step is written to `<out>/logs/NN.log`.
- `--sample held-out.jpg` passes the image to `model_transform` and `model_deploy`, and
  TPU-MLIR then runs an ONNX/simulator numerical comparison; without it the report says `numerical_validation: not_run`.
- On success it writes `conversion-report.json` and `manifest.fragment.json`; the latter can be merged directly into `demo.json`.

## Exit codes

- `0`: conversion completed (check the report to see whether the numerical comparison ran).
- `1`: a step failed; see the corresponding `logs/NN.log`.
- `2`: environment, arguments, or prerequisites not satisfied.

## Limits

- A successful conversion only proves the model compiled; **it does not prove accuracy**.
- Dynamic shapes, multiple inputs, and non-image inputs are not covered by the generic recipe and need model-specific scripts.
- Hybrid quantization is not configured automatically; you must reuse the tensor names from the official step1/step2.
