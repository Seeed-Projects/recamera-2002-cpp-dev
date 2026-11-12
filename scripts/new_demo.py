#!/usr/bin/env python3
"""Scaffold a reCamera 2002 demo workspace.

    python3 scripts/new_demo.py --name fall_detection --task pose
    python3 scripts/new_demo.py --name hello --kind cmake --out ~/reCamera_demo
    python3 scripts/new_demo.py --name vendor_demo --kind prebuilt --artifact ./demo.riscv

The generated workspace passes validate_demo.py in demo mode and includes a
host-side smoke test, so a developer can run a green offline pipeline before a
device is connected.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_lib import (  # noqa: E402
    SKILL_ROOT,
    NAME_RE,
    load_contracts,
    load_manifest,
    print_result,
    read_json,
    sha256_file,
    validate_manifest,
    write_json,
)

TEMPLATES = SKILL_ROOT / "templates"
TEXT_SUFFIXES = {".json", ".c", ".h", ".cc", ".cpp", ".hpp", ".md", ".txt",
                 ".sh", ".cmake", ".py", ".yml", ".yaml"}
KINDS = ("cpp", "cmake", "script", "prebuilt")
DEFAULT_VERSION = "1.0.0"


def render_tree(root: Path, values: dict[str, str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "CMakeLists.txt":
            continue
        text = path.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        path.write_text(text, encoding="utf-8")


def copy_template(kind: str, target: Path, values: dict[str, str], force: bool) -> None:
    source = TEMPLATES / kind
    if not source.is_dir():
        raise FileNotFoundError(f"template missing: {source}")
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source)
        dest = target / rel
        if dest.exists() and not force:
            raise FileExistsError(f"{dest} already exists; use --force")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
    common = TEMPLATES / "common"
    for item in sorted(common.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(common)
        dest = target / (".gitignore" if rel.name == "gitignore" else rel)
        if dest.exists() and not force:
            raise FileExistsError(f"{dest} already exists; use --force")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)


def overlay_live(target: Path, kind: str) -> None:
    """Copy the live UDP transport over a cpp/cmake scaffold."""
    source = TEMPLATES / "live"
    if not source.is_dir():
        raise FileNotFoundError(f"live template missing: {source}")
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source)
        if rel.name == "CMakeLists.txt":
            if kind != "cmake":
                continue
            dest = target / "src" / "CMakeLists.txt"
        elif rel.name == "README.md":
            dest = target / "LIVE.md"
        else:
            dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
    # The base cpp/cmake template ships main.c; the live overlay supplies a
    # C++ main.cpp. Keeping both would confuse CMake and source listings.
    stale = target / "src" / "main.c"
    if stale.is_file() and (target / "src" / "main.cpp").is_file():
        stale.unlink()


def apply_live_manifest(target: Path) -> None:
    """Turn the generated demo.json into a live-capable manifest."""
    manifest = read_json(target / "demo.json")
    build = manifest.setdefault("build", {})
    build["source_dir"] = "src"
    build["sources"] = ["main.cpp", "live_udp.cpp"]

    def add_unique(key: str, values: list[str]) -> None:
        current = list(build.get(key, []))
        for value in values:
            if value not in current:
                current.append(value)
        build[key] = current

    add_unique("cflags", ["-std=c++17"])
    add_unique("ldflags", ["-lpthread", "-latomic"])
    add_unique("defines", ["LIVE_UDP"])

    runtime = manifest.setdefault("runtime", {})
    runtime.update({
        "long_running": True,
        "timeout_s": 30,
        "expect_stdout": ["live: started"],
        "requires_camera": True,
        "sudo": True,
        "services_stop": ["S03node-red", "S91sscma-node", "S93sscma-supervisor"],
        "services_start": ["S03node-red", "S91sscma-node", "S93sscma-supervisor"],
    })

    smoke = manifest.setdefault("smoke", {})
    smoke["timeout_s"] = 60
    smoke["expect_stdout"] = ["self-test ok", "live self-test ok"]

    manifest["live"] = {
        "transport": "udp",
        "implemented": False,
        "video": {
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "bitrate_kbps": 8000,
        },
        "udp": {
            "video_port": 5000,
            "control_port": 5001,
            "max_payload": 1200,
            "snapshot_fps": 1,
        },
        # Performance gate: launch/serve report "degraded" when the measured
        # device-side result age or update rate misses these.  Tune per demo.
        "perf": {
            "max_result_age_ms": 500,
            "min_detect_fps": 1,
            "min_video_fps": 15,
        },
        "model": {
            "input_width": 640,
            "input_height": 640,
            "preprocess": "stretch",
            "scale": 1.0,
            "pad_x": 0,
            "pad_y": 0,
        },
        "params": {
            "confidence": {
                "type": "number", "min": 0.05, "max": 0.95, "step": 0.05,
                "default": 0.60, "label": "Confidence",
            },
            "nms": {
                "type": "number", "min": 0.05, "max": 0.95, "step": 0.05,
                "default": 0.45, "label": "NMS IoU",
            },
            "max_det": {
                "type": "integer", "min": 1, "max": 100, "step": 1,
                "default": 20, "label": "Max detections",
            },
            "lane_thr": {
                "type": "number", "min": 0.05, "max": 0.95, "step": 0.05,
                "default": 0.55, "label": "Lane confidence",
            },
            "roi_top": {
                "type": "number", "min": 0.0, "max": 0.9, "step": 0.05,
                "default": 0.45, "label": "ROI top edge",
            },
            "min_lane_px": {
                "type": "integer", "min": 20, "max": 1080, "step": 10,
                "default": 150, "label": "Min lane length",
            },
            "lane_ema": {
                "type": "number", "min": 0.0, "max": 0.95, "step": 0.05,
                "default": 0.60, "label": "Smoothing factor",
            },
            "max_lane_pts": {
                "type": "integer", "min": 10, "max": 80, "step": 10,
                "default": 40, "label": "Lane points",
            },
            "show_roi": {"type": "boolean", "default": True, "label": "Overlay ROI"},
            "show_lanes": {"type": "boolean", "default": True, "label": "Show lanes"},
            "show_boxes": {"type": "boolean", "default": True, "label": "Show detection boxes"},
        },
    }
    inputs = manifest.setdefault("inputs", {})
    inputs["camera"] = True
    inputs["video"] = True
    write_json(target / "demo.json", manifest)


def prebuilt_manifest(name: str, version: str, task: str, artifact: Path) -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "version": version,
        "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
        "build": {
            "kind": "prebuilt",
            "artifact": f"assets/{artifact.name}",
            "binary": name,
            "sha256": sha256_file(artifact),
        },
        "runtime": {
            "executable": name,
            "args": [],
            "env": {},
            "sudo": False,
            "timeout_s": 10,
            "expect_stdout": [],
        },
        "evaluation": {"task": task, "thresholds": {}},
        "model": None,
        "inputs": {"camera": False, "video": False, "images": False},
        "smoke": {"kind": "none"},
        "evidence": {"required_stages": ["static", "offline", "device-upload", "device-run"]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="demo name (starts the remote directory name)")
    parser.add_argument("--kind", choices=KINDS, default="cpp")
    parser.add_argument("--live", choices=("none", "udp"), default="none",
                        help="add the live UDP transport and host-viewer manifest (cpp/cmake only)")
    parser.add_argument("--task", default="custom", help="evaluation task from configs/evaluation-contract.json")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--out", type=Path, default=Path.home() / "reCamera_demo")
    parser.add_argument("--artifact", type=Path, help="required for --kind prebuilt")
    parser.add_argument("--force", action="store_true", help="overwrite template files in an existing directory")
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args(argv)

    if not NAME_RE.match(args.name):
        print("ERROR: name must match ^[a-z0-9][a-z0-9_-]{1,63}$", file=sys.stderr)
        return 2
    device_contract, eval_contract = load_contracts(SKILL_ROOT)
    tasks = eval_contract.get("tasks", {})
    if args.task not in tasks:
        print(f"ERROR: unknown task {args.task!r}; choose from {sorted(tasks)}", file=sys.stderr)
        return 2
    if args.kind == "prebuilt":
        if not args.artifact or not args.artifact.is_file():
            print("ERROR: --kind prebuilt requires --artifact <existing RISC-V binary>", file=sys.stderr)
            return 2
    if args.live != "none" and args.kind not in ("cpp", "cmake"):
        print("ERROR: --live udp is only supported for --kind cpp or cmake", file=sys.stderr)
        return 2

    target = (args.out / args.name).expanduser()
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"ERROR: {target} is not empty; use --force or pick another name", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)

    values = {"NAME": args.name, "BINARY": args.name, "VERSION": args.version, "TASK": args.task}
    try:
        if args.kind == "prebuilt":
            assets = target / "assets"
            assets.mkdir(exist_ok=True)
            shutil.copy2(args.artifact, assets / args.artifact.name)
            manifest = prebuilt_manifest(args.name, args.version, args.task, args.artifact)
            write_json(target / "demo.json", manifest)
            common = TEMPLATES / "common"
            for item in common.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(common)
                    dest = target / (".gitignore" if rel.name == "gitignore" else rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists() or args.force:
                        shutil.copy2(item, dest)
            render_tree(target, values)
        else:
            copy_template(args.kind, target, values, args.force)
            if args.live != "none":
                overlay_live(target, args.kind)
            render_tree(target, values)
            if args.live != "none":
                apply_live_manifest(target)
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for script in ("build.sh", "smoke.sh"):
        path = target / script
        if path.is_file():
            path.chmod(0o755)

    print(f"created: {target}")
    if not args.no_validate:
        manifest, _ = load_manifest(target)
        result = validate_manifest(manifest, target, (device_contract, eval_contract), mode="demo", skill_root=SKILL_ROOT)
        print_result(result, "scaffold validation")
        if not result.ok:
            print("Scaffold written but validation failed; inspect the errors above.", file=sys.stderr)
            return 1

    print("\nnext:")
    print(f"  python3 scripts/build_demo.py   --demo-dir {target}")
    print(f"  python3 scripts/smoke_demo.py   --demo-dir {target}")
    print(f"  python3 scripts/package_demo.py --demo-dir {target}")
    print(f"  python3 scripts/deploy_demo.py  --demo-dir {target} --host 192.168.42.1 --action install --replace")
    if args.live != "none":
        print(f"  python3 scripts/launch_demo.py  --demo-dir {target} --host 192.168.42.1 --allow-actions")
        print("  # after implementing the camera/detection pipeline, set live.implemented=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())