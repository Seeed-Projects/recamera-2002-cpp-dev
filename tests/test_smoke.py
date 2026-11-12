import json
import sys

import smoke_demo


def _demo(tmp_path, smoke):
    demo = tmp_path / "demo"
    (demo / "src").mkdir(parents=True)
    (demo / "src/main.c").write_text("int main(void){return 0;}\n")
    manifest = {
        "schema_version": 1,
        "name": "smoke_demo",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
        "build": {"kind": "cpp", "source_dir": "src", "sources": ["main.c"], "binary": "smoke_demo"},
        "runtime": {"executable": "smoke_demo", "args": [], "env": {}, "sudo": False, "timeout_s": 10},
        "evaluation": {"task": "custom", "thresholds": {}},
        "model": None,
        "inputs": {"camera": False, "video": False, "images": False},
    }
    if smoke is not None:
        manifest["smoke"] = smoke
    (demo / "demo.json").write_text(json.dumps(manifest))
    return demo


def test_smoke_passes_with_marker(tmp_path):
    demo = _demo(tmp_path, {"kind": "host-logic",
                            "command": [sys.executable, "-c", "print('self-test ok')"],
                            "timeout_s": 10, "expect_stdout": ["self-test ok"]})
    assert smoke_demo.main(["--demo-dir", str(demo)]) == 0
    report = json.loads((demo / "dist/smoke-report.json").read_text())
    assert report["stages"]["offline"]["status"] == "passed"


def test_smoke_fails_on_missing_marker(tmp_path):
    demo = _demo(tmp_path, {"kind": "host-logic",
                            "command": [sys.executable, "-c", "print('nope')"],
                            "expect_stdout": ["self-test ok"]})
    assert smoke_demo.main(["--demo-dir", str(demo)]) == 1


def test_smoke_fails_on_nonzero_exit(tmp_path):
    demo = _demo(tmp_path, {"kind": "host-logic",
                            "command": [sys.executable, "-c", "raise SystemExit(3)"]})
    assert smoke_demo.main(["--demo-dir", str(demo)]) == 1


def test_smoke_not_run_without_hook(tmp_path):
    demo = _demo(tmp_path, None)
    assert smoke_demo.main(["--demo-dir", str(demo)]) == 2


def test_smoke_not_run_for_none_kind(tmp_path):
    demo = _demo(tmp_path, {"kind": "none"})
    assert smoke_demo.main(["--demo-dir", str(demo)]) == 2


def test_smoke_timeout(tmp_path):
    demo = _demo(tmp_path, {"kind": "host-logic",
                            "command": [sys.executable, "-c", "import time; time.sleep(5)"],
                            "timeout_s": 1})
    assert smoke_demo.main(["--demo-dir", str(demo), "--timeout", "1"]) == 1


def test_smoke_runs_against_archive(tmp_path):
    from devkit.artifacts import deterministic_tar
    demo = _demo(tmp_path, {"kind": "host-logic",
                            "command": [sys.executable, "-c", "print('self-test ok')"],
                            "expect_stdout": ["self-test ok"]})
    archive = tmp_path / "pkg.tar.gz"
    deterministic_tar([(demo / "demo.json", "demo.json"),
                       (demo / "src/main.c", "src/main.c")], archive, "pkg")
    report = tmp_path / "report.json"
    assert smoke_demo.main(["--archive", str(archive), "--report", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["details"]["archive"] == str(archive)


def test_extract_archive_rejects_traversal(tmp_path):
    import io
    import tarfile
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../evil.json")
        payload = b"{}"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    import pytest
    with pytest.raises(ValueError):
        smoke_demo.extract_archive(archive)