import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))


@pytest.fixture
def skill_root():
    return SKILL


@pytest.fixture
def base_manifest():
    return {
        "schema_version": 1,
        "name": "hello_cpp",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
        "build": {
            "kind": "cpp",
            "source_dir": "src",
            "sources": ["main.c"],
            "binary": "hello_cpp",
        },
        "runtime": {
            "executable": "hello_cpp",
            "args": [],
            "env": {},
            "sudo": False,
            "timeout_s": 10,
        },
        "evaluation": {"task": "custom", "thresholds": {}},
        "model": None,
        "inputs": {"camera": False, "video": False, "images": False},
    }


@pytest.fixture
def make_demo(tmp_path, base_manifest):
    def make(manifest=None, source="int main(void){return 0;}\n", extra=None):
        demo = tmp_path / "demo"
        demo.mkdir(exist_ok=True)
        (demo / "src").mkdir(exist_ok=True)
        (demo / "src" / "main.c").write_text(source, encoding="utf-8")
        data = manifest if manifest is not None else base_manifest
        (demo / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        for name, content in (extra or {}).items():
            path = demo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return demo
    return make


@pytest.fixture
def contracts():
    from demo_lib import load_contracts
    return load_contracts(SKILL)