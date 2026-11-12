import json

import convert_model


def _tools(tmp_path):
    root = tmp_path / "tpu"
    tools = root / "python/tools"
    tools.mkdir(parents=True)
    for name in convert_model.TOOLS:
        (tools / name).write_text("")
    return root


def _recipe(tmp_path, **overrides):
    recipe = {
        "model_name": "tiny",
        "input_name": "images",
        "input_layout": "NCHW",
        "input_shape": [1, 3, 64, 64],
        "color": "RGB",
        "mean": [0, 0, 0],
        "std": [255, 255, 255],
        "resize": {"mode": "letterbox", "pad_value": 114},
        "output_names": ["output0"],
        "input_num": 4,
    }
    recipe.update(overrides)
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(recipe))
    return path


def test_convert_dry_run_builds_plan(tmp_path):
    root = _tools(tmp_path)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"not really onnx")
    calib = tmp_path / "calibration.txt"
    calib.write_text("a.jpg\n")
    out = tmp_path / "out"
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(_recipe(tmp_path)),
        "--quant", "i8", "--dataset", str(calib), "--out", str(out),
        "--tpu-mlir-root", str(root), "--dry-run",
    ])
    assert code == 0
    plan = json.loads((out / "conversion-plan.json").read_text())
    assert len(plan["commands"]) == 3
    transform = " ".join(plan["commands"][0])
    assert "--keep_aspect_ratio" in transform
    assert "--scale 0.0039215686" in transform
    deploy = " ".join(plan["commands"][2])
    assert "--quantize INT8" in deploy
    assert "--chip cv181x" in deploy


def test_convert_fp_skips_calibration(tmp_path):
    root = _tools(tmp_path)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    out = tmp_path / "out"
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(_recipe(tmp_path)),
        "--quant", "fp", "--out", str(out), "--tpu-mlir-root", str(root), "--dry-run",
    ])
    assert code == 0
    plan = json.loads((out / "conversion-plan.json").read_text())
    assert len(plan["commands"]) == 2
    assert "--quantize F16" in " ".join(plan["commands"][1])


def test_convert_i8_requires_dataset(tmp_path):
    root = _tools(tmp_path)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(_recipe(tmp_path)),
        "--quant", "i8", "--out", str(tmp_path / "out"), "--tpu-mlir-root", str(root), "--dry-run",
    ])
    assert code == 2


def test_convert_rejects_non_empty_output(tmp_path):
    root = _tools(tmp_path)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    calib = tmp_path / "calibration.txt"
    calib.write_text("a.jpg\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale").write_text("x")
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(_recipe(tmp_path)),
        "--quant", "i8", "--dataset", str(calib), "--out", str(out),
        "--tpu-mlir-root", str(root), "--dry-run",
    ])
    assert code == 2


def test_convert_rejects_bad_recipe(tmp_path):
    root = _tools(tmp_path)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    bad = _recipe(tmp_path, input_layout="NHWC")
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(bad),
        "--quant", "fp", "--out", str(tmp_path / "out"), "--tpu-mlir-root", str(root), "--dry-run",
    ])
    assert code == 2


def test_convert_missing_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TPU_MLIR_PATH", raising=False)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    code = convert_model.main([
        "convert", "--onnx", str(onnx), "--recipe", str(_recipe(tmp_path)),
        "--quant", "fp", "--out", str(tmp_path / "out"),
        "--tpu-mlir-root", str(tmp_path / "nope"), "--dry-run",
    ])
    assert code == 2


def test_inspect_reads_onnx(tmp_path):
    import pytest
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper
    node = helper.make_node("Relu", ["x"], ["y"])
    graph = helper.make_graph(
        [node], "tiny",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3, 8, 8])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    path = tmp_path / "tiny.onnx"
    onnx.save(model, str(path))
    report = tmp_path / "inspect.json"
    assert convert_model.main(["inspect", str(path), "--report", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["inputs"][0]["name"] == "x"
    assert data["operators"] == {"Relu": 1}