from __future__ import annotations

import json
import shutil

import pytest

import new_demo
import smoke_demo


def test_live_scaffold_manifest(tmp_path):
    assert new_demo.main([
        "--name", "live_scaffold", "--kind", "cpp", "--live", "udp", "--out", str(tmp_path),
    ]) == 0
    demo = tmp_path / "live_scaffold"
    manifest = json.loads((demo / "demo.json").read_text(encoding="utf-8"))
    assert manifest["live"]["transport"] == "udp"
    assert manifest["live"]["implemented"] is False
    assert manifest["live"]["video"]["codec"] == "h264"
    assert manifest["live"]["video"]["width"] == 1920
    assert manifest["live"]["video"]["fps"] == 30
    assert manifest["live"]["model"]["input_width"] == 640
    assert manifest["live"]["params"]["lane_thr"]["label"] == "Lane confidence"
    assert manifest["live"]["params"]["show_lanes"]["type"] == "boolean"
    assert manifest["build"]["sources"] == ["main.cpp", "live_udp.cpp"]
    assert "-lpthread" in manifest["build"]["ldflags"]
    assert "-latomic" in manifest["build"]["ldflags"]
    assert manifest["runtime"]["long_running"] is True
    assert manifest["runtime"]["requires_camera"] is True
    assert "S93sscma-supervisor" in manifest["runtime"]["services_stop"]
    assert (demo / "src" / "live_udp.cpp").is_file()
    assert (demo / "src" / "live_udp.h").is_file()
    assert (demo / "src" / "live_camera.hpp").is_file()
    assert manifest["live"]["perf"]["max_result_age_ms"] == 500
    assert manifest["live"]["perf"]["min_detect_fps"] == 1
    assert (demo / "src" / "main.cpp").is_file()
    assert not (demo / "src" / "main.c").exists()
    assert (demo / "LIVE.md").is_file()
    assert "--live-self-test" in (demo / "smoke.sh").read_text(encoding="utf-8")


def test_live_rejects_non_cpp_kinds(tmp_path):
    assert new_demo.main([
        "--name", "bad_live", "--kind", "script", "--live", "udp", "--out", str(tmp_path),
    ]) == 2
    assert not (tmp_path / "bad_live").exists()


@pytest.mark.skipif(shutil.which("c++") is None, reason="no C++ compiler on PATH")
def test_live_smoke_runs_on_host(tmp_path):
    assert new_demo.main([
        "--name", "live_smoke", "--kind", "cpp", "--live", "udp", "--out", str(tmp_path),
    ]) == 0
    assert smoke_demo.main(["--demo-dir", str(tmp_path / "live_smoke")]) == 0