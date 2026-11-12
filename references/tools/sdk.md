# Using the SSCMA SDK (secondary development)

The toolkit handles the workspace, build, smoke test, packaging, and acceptance; the real camera/NPU code comes from
`sscma-example-sg200x`. Do not copy SDK headers into the demo, and do not rewrite the media pipeline inside the demo.

## Host platform

`sscma-example-sg200x` depends on the **Linux x86_64** toolchain provided by ReCamera-OS
(`host-tools/gcc/riscv64-linux-musl-x86_64/bin`). Native macOS/Windows cannot build SDK demos directly;
use a Linux host, Docker/Dev Container, or WSL2.
Host-side live viewing, validation, packaging, scoring, and (SSH key) deployment do not require Linux.

## Recommended starting points

| Need | Reference location |
|---|---|
| Load a `.cvimodel` and decode outputs manually | `components/sscma-micro/sscma-micro/sscma/core/engine/ma_engine_cvi.h` |
| Full example: model factory + camera + inference | `solutions/model_detector/main/main.cpp` |
| Pose model | `components/sscma-micro/.../core/model/ma_model_yolo11_pose.cpp` |
| Camera JPEG frames + OpenCV validation | `solutions/cls_test/main/camera_test.cpp` |
| Overlaying boxes/keypoints | `solutions/sesg-project-lab/onvif_yolo/main/main.cpp` |

## Minimal EngineCVI pattern

```cpp
auto* engine = new ma::engine::EngineCVI();
if (engine->init() != MA_OK) return 1;
if (engine->load(model_path) != MA_OK) return 1;

ma_shape_t in = engine->getInputShape(0);
ma_quant_param_t q = engine->getInputQuantParam(0);
// Print shape/type/size/quant; confirm it matches the conversion recipe

// Fused preprocessing: uint8 RGB NHWC, is_physical=false
ma_tensor_t tensor{};
tensor.is_physical = false;
tensor.data.data = rgb.data();
engine->setInput(0, tensor);
engine->run();

ma_tensor_t out = engine->getOutput(0);
// Decode manually per F32/BF16/S8/U8
```

## Engineering constraints

- The input layout follows the `model_transform.py` recipe; the runtime `ma_tensor_t` is usually
  `uint8 RGB NHWC` — do not normalize twice.
- Camera frames are valid only for the current iteration; copy them before using across frames.
- When the camera must be exclusive, declare `runtime.requires_camera` and
  `runtime.services_stop` in `demo.json` and authorize explicitly in the deploy command.
- Prefer reusing the SDK's existing `sscma-micro.a` and `cvi_mpi` libraries; do not bypass
  `EngineCVI` to poke `/dev/video*` directly or rewrite VI/VPSS configuration.

## Putting SDK code into the demo

1. Generate the workspace with `new_demo.py --kind cmake`.
2. Write the business logic in `src/`, referencing SDK headers via `build.include_dirs`.
3. Declare SDK libraries in `build.lib_dirs` / `build.libs`.
4. Keep pure logic (postprocessing, state machines, coordinate transforms) in separate files and exercise the unit paths
   with the host compiler in `smoke.sh`.
5. Run `build_demo.py --check-only` first to verify paths, then do the real cross-compile.

Example declaration:

```json
"build": {
  "kind": "cmake",
  "source_dir": "src",
  "binary": "my_demo",
  "include_dirs": ["../sscma-example-sg200x/components/sscma-micro/sscma-micro/sscma"],
  "lib_dirs": ["../sscma-example-sg200x/solutions/model_detector/build"],
  "libs": ["sscma-micro"],
  "cmake_args": ["-DCMAKE_CXX_STANDARD=17"]
}
```

Use demo-relative paths or environment variables; never hard-code absolute paths from a developer's machine.

## Validation ladder

1. ONNX shape/preprocessing check (`convert_model.py inspect`).
2. `.cvimodel` compilation (`convert_model.py convert`).
3. `EngineCVI` load on the device, printing shape/type/quant/elapsed time.
4. Visual outputs for a fixed input.
5. Restore services and confirm the device is still usable.

Each step is reported separately; they cannot substitute for one another.
