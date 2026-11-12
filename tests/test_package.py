import json
import tarfile

import package_demo


def _elf() -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (243).to_bytes(2, "little")
    return bytes(header)


def _demo(tmp_path, with_binary=True):
    demo = tmp_path / "demo"
    (demo / "src").mkdir(parents=True)
    (demo / "src/main.c").write_text("int main(void){return 0;}\n")
    manifest = {
        "schema_version": 1,
        "name": "pkg_demo",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002", "os_min": "0.2.4"},
        "build": {"kind": "cpp", "source_dir": "src", "sources": ["main.c"], "binary": "pkg_demo"},
        "runtime": {"executable": "pkg_demo", "args": [], "env": {}, "sudo": False,
                    "timeout_s": 10, "expect_stdout": ["hello"]},
        "evaluation": {"task": "custom", "thresholds": {}},
        "model": None,
        "inputs": {"camera": False, "video": False, "images": False},
    }
    (demo / "demo.json").write_text(json.dumps(manifest))
    if with_binary:
        (demo / "dist").mkdir()
        (demo / "dist/pkg_demo").write_bytes(_elf())
    return demo


def test_package_creates_verified_archive(tmp_path):
    demo = _demo(tmp_path)
    assert package_demo.main(["--demo-dir", str(demo)]) == 0
    archive = demo / "dist/pkg_demo-1.0.0.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "pkg_demo-1.0.0/demo.json" in names
    assert "pkg_demo-1.0.0/files.sha256" in names
    assert "pkg_demo-1.0.0/dist/pkg_demo" in names
    report = json.loads((demo / "dist/package-report.json").read_text())
    assert report["stages"]["offline"]["status"] == "passed"
    assert report["stages"]["device-run"]["status"] == "not_run"


def test_package_is_deterministic(tmp_path):
    demo = _demo(tmp_path)
    package_demo.main(["--demo-dir", str(demo)])
    first = (demo / "dist/pkg_demo-1.0.0.tar.gz").read_bytes()
    package_demo.main(["--demo-dir", str(demo)])
    second = (demo / "dist/pkg_demo-1.0.0.tar.gz").read_bytes()
    assert first == second


def test_package_requires_built_binary(tmp_path):
    demo = _demo(tmp_path, with_binary=False)
    assert package_demo.main(["--demo-dir", str(demo)]) == 2


def test_package_rejects_non_riscv_binary(tmp_path):
    demo = _demo(tmp_path)
    bad = bytearray(_elf())
    bad[18:20] = (62).to_bytes(2, "little")
    (demo / "dist/pkg_demo").write_bytes(bytes(bad))
    assert package_demo.main(["--demo-dir", str(demo)]) == 1


def test_package_includes_provenance_but_not_outputs(tmp_path):
    from devkit.artifacts import package_files
    demo = _demo(tmp_path)
    for rel in ("eval/input/manifest.json", "eval/labels/labels.jsonl",
                "eval/reference/reference.py", "eval/baseline/baseline.jsonl",
                "eval/recamera/recamera.jsonl"):
        path = demo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    rels = {rel for _, rel in package_files(demo, {}, None)}
    assert "eval/input/manifest.json" in rels
    assert "eval/labels/labels.jsonl" in rels
    assert "eval/reference/reference.py" in rels
    assert "eval/baseline/baseline.jsonl" not in rels
    assert "eval/recamera/recamera.jsonl" not in rels


def test_package_publish_requires_readme(tmp_path):
    demo = _demo(tmp_path)
    assert package_demo.main(["--demo-dir", str(demo), "--mode", "publish"]) == 1
    (demo / "README.md").write_text("# demo\n")
    assert package_demo.main(["--demo-dir", str(demo), "--mode", "publish"]) == 0