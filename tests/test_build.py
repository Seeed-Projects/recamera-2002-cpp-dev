import json

import build_demo


def test_find_toolchain_explicit(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "riscv64-unknown-linux-musl-gcc").write_text("#!/bin/sh\n", encoding="utf-8")
    found, error = build_demo.find_toolchain(str(bin_dir), None)
    assert error is None
    assert found == bin_dir


def test_prebuilt_copies_to_binary(tmp_path, base_manifest):
    demo = tmp_path / "demo"
    (demo / "dist").mkdir(parents=True)
    (demo / "assets").mkdir()
    elf = bytearray(64)
    elf[:4] = b"\x7fELF"
    elf[4] = 2
    elf[18:20] = (243).to_bytes(2, "little")
    (demo / "assets" / "tool.riscv").write_bytes(bytes(elf))
    manifest = {
        "schema_version": 1,
        "name": "prebuilt_demo",
        "version": "1.0.0",
        "device": {"model": "reCamera 2002"},
        "build": {"kind": "prebuilt", "artifact": "assets/tool.riscv", "binary": "tool"},
        "runtime": {"executable": "tool"},
        "evaluation": {"task": "custom"},
    }
    out = build_demo.build_prebuilt(demo, manifest)
    assert out == demo / "dist" / "tool"
    assert out.read_bytes() == (demo / "assets" / "tool.riscv").read_bytes()