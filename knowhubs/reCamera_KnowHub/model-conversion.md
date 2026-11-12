# Custom Model Conversion And NPU Demo Methodology

Use this methodology when the user asks to deploy a new PyTorch/ONNX model to the reCamera NPU or to create a new non-wiki demo. The expected output is a working `cvimodel`, a C++ demo on the reCamera side, real-device evidence and a short deployment report. Do not call a conversion "deployed" until the model loads and runs on the reCamera.

> **Verification status (important)**: this file is a methodology plus a compilation of historical project records, not automated
> verification results of this skill. The CVI compile commands, model sizes, ION usage and consistency numbers come from the
> author's early project records and were **not re-run** in the current skill environment (`not_run`). After changing the model,
> the OS or the TPU-MLIR version, you must re-confirm via the "Verification ladder" below, and must not quote the numbers
> below as this session's results.
>
> If the official Seeed `onnx-to-cvimodel` skill is installed, prefer it to run conversions; this file is only a reference for
> troubleshooting, trade-offs and output naming, and does not maintain a second conversion implementation.

## Supported Models

| Model | Task | Notes |
|------|------|------|
| YOLO11n | detect / pose / seg / cls | pose and seg need a qtable |
| YOLO26n | detect / cls | pose/seg unsupported (Mod op) |
| BiSeNetv2 | semantic segmentation (Cityscapes) | INT8+quant_output recommended |
| PP-LiteSeg | semantic segmentation (Cityscapes) | requires ONNX graph surgery |
| PaddleOCR | detection/recognition | requires paddle2onnx first |
| Depth Anything | depth estimation | ViT architecture, input must be a multiple of 14 |

Not supported: YOLO26 pose/seg (the Mod operation is unsupported), ONNX TopK/Argmax (CPU post-processing).

## Host Conversion Baseline

Preferred conversion stack:

- Docker image: `sophgo/tpuc_dev:v3.1` (Docker Hub).
- TPU-MLIR compiler: `https://github.com/sophgo/tpu-mlir`; mount a real local TPU-MLIR installation at `/workspace/tpu-mlir` inside the container; the image's built-in path is empty, so mounting is required.
- Use `model_transform.py`, `run_calibration.py` and `model_deploy.py`.
- Keep the work under the task directory, e.g. `$DEMO_DIR/recamera_<model>`.

Command template:

```bash
docker run --rm \
  -v /path/to/tpu-mlir:/workspace/tpu-mlir \
  -v "$PWD":/work \
  sophgo/tpuc_dev:v3.1 bash -lc '
    source /workspace/tpu-mlir/envsetup.sh
    cd /work
    model_transform.py --model_name <name> --model_def <model>.onnx \
      --input_shapes <shape> --mean <m0,m1,m2> --scale <s0,s1,s2> \
      --keep_aspect_ratio --pixel_format rgb --output_names <names> \
      --test_input <image> --mlir <name>.mlir
    run_calibration.py <name>.mlir --dataset calib --input_num 100 \
      -o <name>_cali_table
    model_deploy.py --mlir <name>.mlir --quantize INT8 \
      --calibration_table <name>_cali_table --chip cv181x \
      --fuse_preprocess --customization_format RGB_PACKED \
      --model <name>_cv181x_int8.cvimodel
  '
```

## Model-Specific Output Names

### YOLO11 Detection
```
--output_names /model.23/cv2.0/cv2.0.2/Conv_output_0,/model.23/cv3.0/cv3.0.2/Conv_output_0,/model.23/cv2.1/cv2.1.2/Conv_output_0,/model.23/cv3.1/cv3.1.2/Conv_output_0,/model.23/cv2.2/cv2.2.2/Conv_output_0,/model.23/cv3.2/cv3.2.2/Conv_output_0
```
The order must alternate Box, Class, Box, Class, Box, Class.

### YOLO26 Detection
```
--output_names /model.23/one2one_cv2.0/one2one_cv2.0.2/Conv_output_0,/model.23/one2one_cv3.0/one2one_cv3.0.2/Conv_output_0,/model.23/one2one_cv2.1/one2one_cv2.1.2/Conv_output_0,/model.23/one2one_cv3.1/one2one_cv3.1.2/Conv_output_0,/model.23/one2one_cv2.2/one2one_cv2.2.2/Conv_output_0,/model.23/one2one_cv3.2/one2one_cv3.2.2/Conv_output_0
```

### YOLO11 Segmentation / Pose
```
--output_names output0,output1    # seg
--output_names output0            # pose, add --quantize_table yolo11n_pose_qtable
```

### BiSeNetv2
```
--output_names preds
--input_shapes [[1,3,512,1024]]
--mean 123.675,116.28,103.53 --scale 0.01712475,0.01750700,0.01742919
```

### PP-LiteSeg
```
--output_names p2o.pd_op.bilinear_interp.6.0
--input_shapes [[1,3,512,1024]]
--mean 123.675,116.28,103.53 --scale 0.01712475,0.01750700,0.01742919
```

## Conversion Decisions

- If a model may not quantize cleanly, start from BF16 to prove graph support.
- If BF16 hits reCamera ION OOM, try INT8 calibration or a smaller input.
- Respect model architecture constraints. ViT-style models usually need dimensions divisible by the patch size; e.g. Depth Anything ViT uses multiples of `14`.
- Match `--input_shapes` to the exported ONNX graph, usually in NCHW format. With fused RGB preprocessing, the reCamera runtime input may still report as packed NHWC `U8`.
- `--fuse_preprocess --customization_format RGB_PACKED` makes the runtime input raw `uint8` RGB NHWC. Do not feed normalized float NCHW in C++.
- For quick feasibility tests a small calibration set is acceptable; for quality claims use 100+ representative frames from the target camera domain.

## ION Memory Optimization (--quant_output)

CV181x ION memory is shared by the TPU, VPSS and VENC, totaling about 60MB. An INT8 model with float32 outputs gets dequantized on-chip, consuming a lot of ION.

Use `--quant_output` to keep int8 outputs and avoid dequantization:

| Variant | Model size | ION memory | Output type |
|------|----------|----------|----------|
| BiSeNetv2 BF16 | 14 MB | 87.59 MB | float32 |
| BiSeNetv2 INT8 | 6.0 MB | 62.89 MB | float32 |
| **BiSeNetv2 INT8_qout** | **5.9 MB** | **34.27 MB** | **int8** |
| PP-LiteSeg INT8 | 10.3 MB | 59.00 MB | float32 |
| **PP-LiteSeg INT8_qout** | **9.8 MB** | **26.73 MB** | **int8** |

**When to use `--quant_output`**: ION memory is tight and post-processing needs only relative ranking (argmax/classification/segmentation masks).

**When not to use it**: you need absolute float32 confidence values (e.g. detection confidence against specific thresholds).

## Hybrid Quantization (qtable)

YOLO pose/seg use a qtable for hybrid quantization, with better accuracy:

```bash
model_deploy.py ... --quantize_table yolo11n_pose_qtable \
  --model yolo11n-pose_cv181x_mix.cvimodel
```

## PP-LiteSeg ONNX Graph Surgery

PP-LiteSeg's ONNX graph must be modified before conversion:

1. **Remove the ArgMax + Cast nodes**: the original output is `[1,H,W] int32` (the label map after argmax); TPU-MLIR needs the pre-argmax logits `[1,19,H,W] float32`.
2. **Fix AveragePool `count_include_pad`**: PP-LiteSeg has 5 AveragePool nodes with `count_include_pad=0`; cv181x requires `=1`.
3. **Pre-simplify the ONNX locally**: onnxsim inside Docker introduces broken Squeeze ops, so run `onnxsim` on the host first.

```python
import onnx, onnxsim
from onnx import TensorProto, helper

model = onnx.load("pp_liteseg.onnx")
# find the last Resize output and add it as a graph output
for node in model.graph.node:
    if node.op_type == 'Resize':
        resize_out = node.output[0]
new_out = helper.make_tensor_value_info(resize_out, TensorProto.FLOAT, [1, 19, 512, 1024])
model.graph.output.insert(0, new_out)
# simplify locally
model, check = onnxsim.simplify(model, test_input_shapes={'x': [1, 3, 512, 1024]})
# fix AveragePool
for node in model.graph.node:
    if node.op_type == 'AveragePool':
        for attr in node.attribute:
            if attr.name == 'count_include_pad':
                attr.i = 1
onnx.save(model, "pp_liteseg_prepared.onnx")
```

The PP-LiteSeg cvimodel is detected by sscma-model as `MA_MODEL_TYPE_BISENETV2` (type 17) and can be used directly, but it must use the `RGB_PACKED` format.

## PaddleOCR / Paddle Inference Notes

The `*_infer` packages released by PaddleOCR usually contain `inference.pdmodel` and `inference.pdiparams`; export them to ONNX first, then enter TPU-MLIR. `sophgo/tpuc_dev:v3.1` already ships `paddle2onnx`; even when the `paddle` Python package cannot be imported due to `libssl.so.1.1`, the `paddle2onnx` CLI can still convert these inference models.

```bash
paddle2onnx --model_dir <infer_dir> \
  --model_filename inference.pdmodel \
  --params_filename inference.pdiparams \
  --opset_version 11 \
  --save_file <model>.onnx
```

PP-OCR's ONNX is often dynamic-shape; it must be fixed when converting to CVIMODEL:

- The Chinese detection model commonly uses `--input_shapes [[1,3,640,640]]` with outputs like `sigmoid_0.tmp_0`; DBNet post-processing needs the probability map's absolute values, so do not blindly enable `--quant_output`.
- The Chinese recognition model commonly uses `--input_shapes [[1,3,48,320]]` or the wider `[[1,3,48,640]]` with outputs like `softmax_*.tmp_0`; when CTC greedy decoding depends only on argmax you can evaluate `--quant_output`, but the confidence semantics change.
- PaddleOCR standard preprocessing is usually RGB, `mean 127.5,127.5,127.5`, `scale 0.0078125,0.0078125,0.0078125`; after fused preprocessing, the reCamera C++ side should feed contiguous `uint8` RGB NHWC.
- If the C++ side of a detection model uses letterbox, the TPU-MLIR conversion should also use `--keep_aspect_ratio --pad_value 128 --pad_type center`, otherwise coordinate back-mapping and padding semantics will not match.

When ION memory approaches the cv181x budget, try a smaller input or INT8 first; `--quant_output` can greatly reduce output-dequantization memory, but only suits models whose post-processing does not depend on absolute float confidence.

## Depth Anything Historical Results (not_run, reference only)

The following results come from an early project and were not reproduced in the current skill environment:

- Source checkpoint used: `<download_dir>/depth_anything_v2_vits.pth`.
- Working directory: `$DEMO_DIR/recamera_depth_anything`.
- `224x224` BF16 compiled successfully but failed on the reCamera with the error `ion ioctl fail:: Out of memory`.
- `168x168` BF16 ran, NPU time about `656 ms`.
- `224x224` INT8 ran with a minimal calibration set of 8 images: model `$DEMO_DIR/recamera_depth_anything/models/depth_anything_v2_vits_224_int8_min8.cvimodel`, about `1494 ms` per image, live camera `1693 ms/frame`.
- Report/evidence: `$DEMO_DIR/recamera_depth_anything/DEPLOY_REPORT.md`, `<evidence_dir>/recamera_live_depth_anything_224_int8_jpeg.mp4`.

## Device Runtime Pattern For Custom Models

If `ModelFactory` does not support the task type, use `ma::engine::EngineCVI` directly:

- `engine->init()`;
- `engine->load(model_path)`;
- Check the input/output `shape`, `type`, `size` and quantization metadata;
- Fill the input tensor;
- `engine->setInput(0, tensor)`;
- `engine->run()`;
- `engine->getOutput(0)`;
- Manually decode `F32`, `BF16`, `S8` and `U8` outputs.

For custom models with fused preprocessing, allocate a CPU-packed input:

- `is_physical = false`;
- `tensor.data.data = rgb.data`;
- Make sure a contiguous `uint8` RGB NHWC buffer matches the engine input shape, usually `[1,H,W,3]`.

For official detector demos that consume physical camera buffers, `is_physical = true` may be valid. Do not mix that path with CPU-decoded JPEG buffers.

## Camera Input And Visual Evidence

When the direct RGB888 VPSS frame is treated as a packed OpenCV RGB image, it may look visually wrong. For custom demos that need trustworthy visual evidence:

- Configure camera channel `1` as `MA_PIXEL_FORMAT_JPEG`, e.g. `640x480`;
- Retrieve JPEG frames and decode with `cv::imdecode`;
- Resize to the model input;
- Convert `BGR -> RGB` before the NPU input;
- Produce side-by-side evidence such as `input | heatmap/mask/detections`;
- Save frames on the reCamera, pull them back to the host, then encode with `ffmpeg`.

OpenCV is already available in `sscma-example-sg200x`; link only the parts you need, usually `opencv_core`, `opencv_imgcodecs` and `opencv_imgproc`. If OpenCV `cv` conflicts with `ma::cv`, use `::cv::`.

## C++ Build And Deployment Template

Build on the PC:

```bash
export SG200X_SDK_PATH=$SDK_ROOT
export PATH=$TOOLCHAIN_BIN:$PATH
cd <demo source dir>
cmake -B build -DCMAKE_BUILD_TYPE=Release .
cmake --build build -j$(nproc)
```

Deploy to a task directory on the reCamera, e.g. `/home/recamera/<demo_name>`, then run with `sudo` and the standard `LD_LIBRARY_PATH` after stopping the camera-occupying services (see "C++ build and deployment baseline" in `overview.md` for the stop-services commands and runtime environment variables).

Verification ladder:

1. PyTorch/ONNX export plus ONNX Runtime shape check.
2. TPU-MLIR compilation to `cvimodel`.
3. reCamera `EngineCVI` load/run, printing shapes, data types, timing and failure text.
4. Real-device visual artifacts from the camera or representative inputs.
5. Restore services/reboot and verify `http://192.168.42.1/api/version`.

## ONNX vs CVIMODEL Validation

After conversion, verify that ONNX and CVIMODEL outputs are consistent:

```python
# ONNX inference (standard float32 pipeline)
import onnxruntime as ort, numpy as np, cv2
sess = ort.InferenceSession("model.onnx")
img = cv2.imread("test.jpg")
img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
img_float = cv2.resize(img_rgb, (W, H)).astype(np.float32) / 255.0
img_norm = (img_float - mean) / std
img_nchw = np.transpose(img_norm, (2, 0, 1))[np.newaxis].astype(np.float32)
onnx_out = sess.run(None, {input_name: img_nchw})[0]

# CVIMODEL inference (uint8 RGB, fuse_preprocess handles normalization)
import pyruntime_cvi as cvi
model = cvi.Model("model_cv181x_int8_qout.cvimodel", output_all_tensors=True)
img_rgb2 = cv2.cvtColor(cv2.imread("test.jpg"), cv2.COLOR_BGR2RGB)
input_data = np.ascontiguousarray(cv2.resize(img_rgb2, (W, H)), dtype=np.uint8)
model.inputs[0].data[:] = input_data.reshape(model.inputs[0].data.shape)
model.forward()

# Compare
agreement = np.sum(label_onnx == label_cvi) / label_onnx.size * 100
print(f"Pixel agreement: {agreement:.2f}%")
```

Historical consistency results (early project records, not reproduced in this skill environment):
- BiSeNetv2: pixel agreement 98-99%, ION 34.27MB, inference 436ms.
- PP-LiteSeg: pixel agreement 92-96%, ION 26.73MB.

## Historical Conversion Records (not verified by this skill)

| Model | ONNX | CVIMODEL | Conversion notes |
|------|------|----------|----------|
| YOLO11n detect | 10.7 MB | 3.0 MB | 6 alternating outputs, see Model-Specific Output Names |
| YOLO11n pose | 11.3 MB | 3.6 MB | single output output0 + `--quantize_table` hybrid quantization |
| YOLO11n seg | 11.7 MB | 4.0 MB | two outputs output0,output1, needs a qtable |
| YOLO11n cls | 10.8 MB | 3.0 MB | single output |
| YOLO26n detect | 9.4 MB | 2.9 MB | one2one_ prefixed output names |
| YOLO26n cls | 11.3 MB | 3.0 MB | single output |
| BiSeNetv2 seg | 13 MB | 5.9 MB (INT8_qout) | ImageNet mean/scale, `--quant_output` saves ION |
| PP-LiteSeg seg | 32 MB | 9.8 MB (INT8_qout) | do ONNX graph surgery first, `--quant_output` saves ION |

## Troubleshooting

| Error | Cause | Fix |
|------|------|------|
| `operand xxx not found` (model_transform) | Docker onnxsim introduces broken Squeeze ops | run onnxsim on the host first, not inside Docker |
| `AvgPooling2d: assertion count_include_pad=true` | PP-LiteSeg AveragePool has count_include_pad=0 | fix all AveragePool nodes before conversion |
| `Op not support: Mod` | YOLO26 pose/seg use the Mod operation | switch to YOLO11 |
| `model_transform: command not found` | TPU-MLIR environment not initialized | `source /workspace/tpu-mlir/envsetup.sh` inside the container |
| Wrong output order / abnormal detection results | detection outputs not ordered Box,Class alternating | ensure the 6 outputs are ordered Box,Class,Box,Class,Box,Class |
| ION memory exceeded | INT8 output dequantized to float32 | add `--quant_output` |
| CVIMODEL results completely wrong | input preprocessing mismatch | ensure fuse_preprocess receives uint8 RGB NHWC, not float32 NCHW |
| `/workspace/tpu-mlir` empty | local TPU-MLIR not mounted | `-v /path/to/tpu-mlir:/workspace/tpu-mlir` |
| sscma-model outputs are bbox instead of segmentation | cvimodel uses RGB_PLANAR but sscma-model feeds HWC | switch to `--customization_format RGB_PACKED` |

## Quick ONNX Export

```python
from ultralytics import YOLO
YOLO('yolo11n.pt').export(format='onnx', imgsz=640, simplify=False, opset=12)     # Detection
YOLO('yolo11n-cls.pt').export(format='onnx', imgsz=224, simplify=False, opset=12)  # Classification
YOLO('yolo11n-pose.pt').export(format='onnx', imgsz=640, simplify=False, opset=12) # Pose
YOLO('yolo11n-seg.pt').export(format='onnx', imgsz=640, simplify=False, opset=12)  # Segmentation
```

## Key Points

1. YOLO26 pose/seg are unsupported; use YOLO11.
2. Detection needs 6 alternating outputs: Box, Class, Box, Class, Box, Class.
3. BiSeNetv2 uses ImageNet preprocessing; mean/scale differ from YOLO.
4. PP-LiteSeg needs ONNX graph surgery (remove ArgMax/Cast, fix AveragePool, pre-simplify locally).
5. Do not run onnxsim inside Docker; run it on the host first.
6. When ION is tight use `--quant_output` (PP-LiteSeg 59→27MB, BiSeNetV2 63→34MB).
7. For sscma-model compatibility use `RGB_PACKED`.
8. You must mount the local tpu-mlir into Docker.
9. After fuse_preprocess the model expects uint8 RGB NHWC input.
10. Use a qtable for hybrid quantization of YOLO pose/seg.
11. Use 100+ calibration images for production.
12. After conversion, always verify ONNX vs CVIMODEL pixel agreement.
