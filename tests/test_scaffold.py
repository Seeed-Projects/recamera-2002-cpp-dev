import json

import new_demo


def _elf() -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (243).to_bytes(2, "little")
    return bytes(header)


def test_cpp_scaffold_validates(tmp_path):
    code = new_demo.main(["--name", "hello_scaffold", "--out", str(tmp_path)])
    assert code == 0
    demo = tmp_path / "hello_scaffold"
    manifest = json.loads((demo / "demo.json").read_text())
    assert manifest["name"] == "hello_scaffold"
    assert manifest["smoke"]["command"] == ["sh", "smoke.sh"]
    assert "hello_scaffold" in (demo / "src/main.c").read_text()
    assert (demo / "eval/input/manifest.json").is_file()
    assert (demo / ".gitignore").is_file()
    assert (demo / "smoke.sh").stat().st_mode & 0o111


def test_cmake_and_script_scaffold(tmp_path):
    assert new_demo.main(["--name", "cmake_demo", "--kind", "cmake", "--out", str(tmp_path)]) == 0
    assert new_demo.main(["--name", "script_demo", "--kind", "script", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "cmake_demo/src/CMakeLists.txt").is_file()
    assert (tmp_path / "script_demo/build.sh").is_file()


def test_prebuilt_scaffold_records_hash(tmp_path):
    artifact = tmp_path / "vendor.riscv"
    artifact.write_bytes(_elf())
    out = tmp_path / "out"
    assert new_demo.main(["--name", "vendor_demo", "--kind", "prebuilt",
                          "--artifact", str(artifact), "--out", str(out)]) == 0
    manifest = json.loads((out / "vendor_demo/demo.json").read_text())
    assert manifest["build"]["kind"] == "prebuilt"
    assert manifest["build"]["artifact"] == "assets/vendor.riscv"
    assert len(manifest["build"]["sha256"]) == 64
    assert manifest["smoke"]["kind"] == "none"


def test_refuses_non_empty_directory(tmp_path):
    assert new_demo.main(["--name", "dup", "--out", str(tmp_path)]) == 0
    assert new_demo.main(["--name", "dup", "--out", str(tmp_path)]) == 2


def test_unknown_task_is_rejected(tmp_path):
    assert new_demo.main(["--name", "x", "--task", "magic", "--out", str(tmp_path)]) == 2


def test_bad_name_is_rejected(tmp_path):
    assert new_demo.main(["--name", "Bad Name", "--out", str(tmp_path)]) == 2