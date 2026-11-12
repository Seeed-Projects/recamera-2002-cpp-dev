#!/usr/bin/env python3
"""ONNX -> TPU-MLIR -> .cvimodel conversion helper for reCamera 2002.

    python3 scripts/convert_model.py check-env
    python3 scripts/convert_model.py inspect model.onnx
    python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
        --quant i8 --dataset calibration.txt --out build/model-i8 --dry-run
    python3 scripts/convert_model.py convert --onnx model.onnx --recipe recipe.json \
        --quant i8 --dataset calibration.txt --out build/model-i8

The helper never downloads anything and never edits the source ONNX. It records
the exact commands, input hashes and tool versions so a build can be audited.
A successful conversion proves compilation only; numerical and device accuracy
remain separate stages.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import SKILL_ROOT, now_iso, read_json, sha256_file, validate_schema, write_json  # noqa: E402

TOOLS = ("model_transform.py", "run_calibration.py", "model_deploy.py")


def discover_tools(root: Path | None, python: str) -> tuple[Path | None, list[str]]:
    problems: list[str] = []
    candidates: list[Path] = []
    if root:
        candidates.append(root)
    env_root = os.environ.get("TPU_MLIR_PATH")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(Path.home() / "tpu-mlir")
    for base in candidates:
        tools_dir = base / "python" / "tools"
        if all((tools_dir / name).is_file() for name in TOOLS):
            return tools_dir, problems
        if base.is_dir():
            problems.append(f"{base}: missing python/tools/{TOOLS[0]}")
    problems.append("TPU-MLIR not found; set --tpu-mlir-root or TPU_MLIR_PATH")
    return None, problems


def module_available(python: str) -> tuple[bool, str]:
    proc = subprocess.run([python, "-c", "import tpu_mlir; print(getattr(tpu_mlir, '__file__', 'ok'))"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return True, proc.stdout.strip()
    return False, (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "import failed"


def cmd_check_env(args) -> int:
    tools_dir, problems = discover_tools(args.tpu_mlir_root, args.python)
    available, detail = module_available(args.python)
    report = {
        "schema_version": 1,
        "kind": "convert-env",
        "generated_at": now_iso(),
        "python": args.python,
        "tools_dir": str(tools_dir) if tools_dir else None,
        "tpu_mlir_import": available,
        "tpu_mlir_detail": detail,
        "problems": problems,
        "target_chip": args.chip,
    }
    print(f"python:      {args.python}")
    print(f"tools_dir:   {tools_dir or 'not found'}")
    print(f"tpu_mlir:    {'importable' if available else 'not importable'}")
    for problem in problems:
        print(f"  ISSUE {problem}")
    if args.report:
        write_json(args.report, report)
    if tools_dir and available:
        return 0
    # Tools can exist in a container even when the host python cannot import
    # tpu_mlir; report that as unverified rather than a hard failure.
    return 2 if not tools_dir else 2


def cmd_inspect(args) -> int:
    try:
        import onnx  # type: ignore
    except ImportError:
        print("ERROR: the 'onnx' package is required for inspect (run it in the conversion env)",
              file=sys.stderr)
        return 2
    path = args.onnx.resolve()
    model = onnx.load(str(path))
    onnx.checker.check_model(model)
    graph = model.graph
    inputs = []
    for value in graph.input:
        dims = []
        for dim in value.type.tensor_type.shape.dim:
            dims.append(dim.dim_value if dim.dim_value else dim.dim_param or "?")
        inputs.append({"name": value.name, "shape": dims,
                       "dtype": value.type.tensor_type.elem_type})
    outputs = []
    for value in graph.output:
        dims = []
        for dim in value.type.tensor_type.shape.dim:
            dims.append(dim.dim_value if dim.dim_value else dim.dim_param or "?")
        outputs.append({"name": value.name, "shape": dims,
                        "dtype": value.type.tensor_type.elem_type})
    ops: dict[str, int] = {}
    for node in graph.node:
        ops[node.op_type] = ops.get(node.op_type, 0) + 1
    report = {
        "schema_version": 1,
        "kind": "onnx-inspect",
        "generated_at": now_iso(),
        "path": str(path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "ir_version": model.ir_version,
        "opset": [{"domain": op.domain, "version": op.version} for op in model.opset_import],
        "inputs": inputs,
        "outputs": outputs,
        "operators": dict(sorted(ops.items())),
    }
    print(f"file:     {path}")
    print(f"sha256:   {report['sha256']}")
    print(f"ir/opset: {model.ir_version} / {report['opset']}")
    for item in inputs:
        print(f"input:    {item['name']} {item['shape']}")
    for item in outputs:
        print(f"output:   {item['name']} {item['shape']}")
    print(f"operators ({len(ops)} kinds): {', '.join(sorted(ops))}")
    if args.report:
        write_json(args.report, report)
    return 0


def load_recipe(path: Path) -> dict:
    recipe = read_json(path)
    schema = read_json(SKILL_ROOT / "schemas" / "recipe.schema.json")
    errors = validate_schema(recipe, schema)
    if errors:
        raise ValueError("recipe invalid:\n  " + "\n  ".join(errors))
    return recipe


def _join(values) -> str:
    return ",".join(str(v) for v in values)


def build_commands(args, recipe: dict, tools_dir: Path, out_dir: Path) -> list[list[str]]:
    name = recipe["model_name"]
    mlir = out_dir / f"{name}.mlir"
    quant = args.quant.lower()
    std = recipe["std"]
    scale = [1.0 / value if value else 1.0 for value in std]
    transform = [
        args.python, str(tools_dir / "model_transform.py"),
        "--model_name", name,
        "--model_def", str(args.onnx.resolve()),
        "--input_shapes", str(recipe["input_shape"]).replace(" ", ""),
        "--mean", _join(recipe["mean"]),
        "--scale", _join(f"{v:.8g}" for v in scale),
        "--pixel_format", recipe["color"].lower(),
        "--mlir", str(mlir),
    ]
    if (recipe.get("resize") or {}).get("mode") == "letterbox":
        transform.append("--keep_aspect_ratio")
    if recipe.get("output_names"):
        transform += ["--output_names", _join(recipe["output_names"])]
    if args.sample:
        transform += ["--test_input", str(Path(args.sample).resolve())]

    commands = [transform]
    calib_table = None
    if quant == "i8":
        calib_table = out_dir / f"{name}_cali_table"
        calibrate = [
            args.python, str(tools_dir / "run_calibration.py"),
            str(mlir),
            "--dataset", str(Path(args.dataset).resolve()),
            "--input_num", str(recipe.get("input_num", 100)),
            "-o", str(calib_table),
        ]
        commands.append(calibrate)
    deploy = [
        args.python, str(tools_dir / "model_deploy.py"),
        "--mlir", str(mlir),
        "--quantize", "INT8" if quant == "i8" else "F16",
        "--chip", args.chip,
        "--model", str(out_dir / f"{name}.cvimodel"),
    ]
    if calib_table:
        deploy += ["--calibration_table", str(calib_table)]
    if recipe.get("quantize_table"):
        deploy += ["--quantize_table", str((args.recipe.parent / recipe["quantize_table"]).resolve())]
    if args.sample:
        deploy += ["--test_input", str(Path(args.sample).resolve())]
    commands.append(deploy)
    return commands


def run_command(command: list[str], cwd: Path, log_path: Path) -> int:
    print("+ " + " ".join(command))
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(command) + "\n\n[stdout]\n" + (proc.stdout or "") +
        "\n[stderr]\n" + (proc.stderr or ""),
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def cmd_convert(args) -> int:
    onnx_path = args.onnx.resolve()
    if not onnx_path.is_file():
        print(f"ERROR: ONNX not found: {onnx_path}", file=sys.stderr)
        return 2
    if not args.recipe.is_file():
        print(f"ERROR: recipe not found: {args.recipe}", file=sys.stderr)
        return 2
    try:
        recipe = load_recipe(args.recipe)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.quant == "i8":
        if not args.dataset or not Path(args.dataset).is_file():
            print("ERROR: --quant i8 requires --dataset <calibration.txt>", file=sys.stderr)
            return 2

    tools_dir, problems = discover_tools(args.tpu_mlir_root, args.python)
    if not tools_dir:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 2

    out_dir = args.out.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"ERROR: output directory must be new or empty: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    commands = build_commands(args, recipe, tools_dir, out_dir)
    if args.dry_run:
        print("dry-run: no command was executed")
        for command in commands:
            print("  " + " ".join(command))
        write_json(out_dir / "conversion-plan.json", {
            "schema_version": 1, "kind": "conversion-plan", "generated_at": now_iso(),
            "commands": commands, "onnx_sha256": sha256_file(onnx_path),
        })
        return 0

    logs = out_dir / "logs"
    executed: list[dict] = []
    for index, command in enumerate(commands, 1):
        code = run_command(command, out_dir, logs / f"{index:02d}.log")
        executed.append({"command": command, "exit_code": code})
        if code != 0:
            write_json(out_dir / "conversion-report.json", {
                "schema_version": 1, "kind": "model-conversion", "generated_at": now_iso(),
                "status": "failed", "onnx": str(onnx_path),
                "onnx_sha256": sha256_file(onnx_path), "commands": executed,
            })
            print(f"ERROR: conversion step {index} failed; log: {logs / f'{index:02d}.log'}",
                  file=sys.stderr)
            return 1

    model = out_dir / f"{recipe['model_name']}.cvimodel"
    if not model.is_file():
        print(f"ERROR: conversion finished but {model} is missing", file=sys.stderr)
        return 1
    report = {
        "schema_version": 1,
        "kind": "model-conversion",
        "generated_at": now_iso(),
        "status": "passed",
        "onnx": str(onnx_path),
        "onnx_sha256": sha256_file(onnx_path),
        "recipe": str(args.recipe.resolve()),
        "recipe_sha256": sha256_file(args.recipe),
        "quantize": args.quant,
        "chip": args.chip,
        "model": {"path": str(model), "size": model.stat().st_size, "sha256": sha256_file(model)},
        "commands": executed,
        "numerical_validation": "run_with_test_input" if args.sample else "not_run",
        "device_validation": "not_run",
    }
    write_json(out_dir / "conversion-report.json", report)
    fragment = {
        "model": {
            "file": f"models/{model.name}",
            "sha256": report["model"]["sha256"],
            "size": report["model"]["size"],
            "task": args.task,
            "conversion": {
                "onnx_sha256": report["onnx_sha256"],
                "dataset": str(args.dataset) if args.dataset else "",
                "input_size": recipe["input_shape"][-2:],
                "layout": recipe["input_layout"],
            },
        }
    }
    write_json(out_dir / "manifest.fragment.json", fragment)
    if args.report:
        write_json(args.report, report)
    print(f"model:    {model}")
    print(f"sha256:   {report['model']['sha256']}")
    print(f"report:   {out_dir / 'conversion-report.json'}")
    print(f"fragment: {out_dir / 'manifest.fragment.json'}")
    if not args.sample:
        print("note: numerical comparison not run; pass --sample <held-out image>")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="python for TPU-MLIR tools")
    parser.add_argument("--report", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-env", help="report TPU-MLIR availability")
    check.add_argument("--tpu-mlir-root", type=Path)
    check.add_argument("--chip", default="cv181x")
    check.add_argument("--report", type=Path)
    check.set_defaults(func=cmd_check_env)

    inspect = sub.add_parser("inspect", help="inspect ONNX inputs/outputs/operators")
    inspect.add_argument("onnx", type=Path)
    inspect.add_argument("--report", type=Path)
    inspect.set_defaults(func=cmd_inspect)

    convert = sub.add_parser("convert", help="run model_transform/run_calibration/model_deploy")
    convert.add_argument("--onnx", required=True, type=Path)
    convert.add_argument("--recipe", required=True, type=Path)
    convert.add_argument("--quant", choices=("fp", "i8"), default="i8")
    convert.add_argument("--dataset", type=Path, help="calibration.txt (required for i8)")
    convert.add_argument("--out", required=True, type=Path)
    convert.add_argument("--chip", default="cv181x")
    convert.add_argument("--task", default="detection")
    convert.add_argument("--sample", type=Path, help="held-out image for ONNX/INT8 comparison")
    convert.add_argument("--tpu-mlir-root", type=Path)
    convert.add_argument("--dry-run", action="store_true")
    convert.add_argument("--report", type=Path)
    convert.set_defaults(func=cmd_convert)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())