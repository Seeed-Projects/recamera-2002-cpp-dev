import copy

from demo_lib import validate_elf_riscv, validate_manifest, ValidationResult


def test_valid_cpp_demo(make_demo, contracts):
    result = validate_manifest(
        __import__("json").loads((make_demo() / "demo.json").read_text()),
        make_demo(), contracts, mode="demo",
    )
    assert result.ok, result.errors
    assert any("unverified" in item.lower() or "device" in item.lower() for item in result.unverified)


def test_missing_source_fails(make_demo, contracts, base_manifest):
    import json
    demo = make_demo()
    manifest = copy.deepcopy(base_manifest)
    manifest["build"]["sources"] = ["missing.c"]
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok
    assert any("source missing" in error for error in result.errors)


def test_model_sha_mismatch(make_demo, contracts, base_manifest):
    demo = make_demo(extra={"models/model.cvimodel": "not a real model"})
    manifest = copy.deepcopy(base_manifest)
    manifest["model"] = {"file": "models/model.cvimodel", "sha256": "0" * 64, "task": "detection"}
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok
    assert any("model sha256 mismatch" in error for error in result.errors)


def test_secret_is_rejected(make_demo, contracts):
    demo = make_demo(source="// sshpass -p hunter2\nint main(void){return 0;}\n")
    import json
    result = validate_manifest(json.loads((demo / "demo.json").read_text()), demo, contracts)
    assert not result.ok
    assert any("sshpass" in error for error in result.errors)


def test_bad_evaluation_task(make_demo, contracts, base_manifest):
    demo = make_demo()
    manifest = copy.deepcopy(base_manifest)
    manifest["evaluation"]["task"] = "magic"
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok


def test_forbidden_service_control(make_demo, contracts, base_manifest):
    demo = make_demo()
    manifest = copy.deepcopy(base_manifest)
    manifest["runtime"]["services_stop"] = ["S50sshd"]
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok
    assert any("allowed service control" in error for error in result.errors)


def test_shell_metacharacter_in_args(make_demo, contracts, base_manifest):
    demo = make_demo()
    manifest = copy.deepcopy(base_manifest)
    manifest["runtime"]["args"] = ["a; reboot"]
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok
    assert any("metacharacter" in error for error in result.errors)


def test_missing_runtime_file(make_demo, contracts, base_manifest):
    demo = make_demo()
    manifest = copy.deepcopy(base_manifest)
    manifest["runtime"]["files"] = ["assets/labels.txt"]
    result = validate_manifest(manifest, demo, contracts)
    assert not result.ok
    assert any("runtime.files" in error for error in result.errors)


def test_bundled_example_validates(skill_root, contracts):
    import json
    demo = skill_root / "examples" / "hello_cpp"
    result = validate_manifest(json.loads((demo / "demo.json").read_text()), demo, contracts, mode="deliver")
    assert result.ok, result.errors


def _elf(machine: int) -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2  # ELF64
    header[5] = 1  # little endian
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header)


def test_elf_riscv_accepts_243(tmp_path):
    path = tmp_path / "ok.elf"
    path.write_bytes(_elf(243))
    result = ValidationResult()
    assert validate_elf_riscv(path, result, "test")
    assert not result.errors


def test_elf_rejects_x86(tmp_path):
    path = tmp_path / "bad.elf"
    path.write_bytes(_elf(62))
    result = ValidationResult()
    assert not validate_elf_riscv(path, result, "test")
    assert any("e_machine" in error for error in result.errors)