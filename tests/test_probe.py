import json

import probe_device
from demo_lib import load_contracts, redact, semver_tuple, sha256_file

CONTRACT, _ = load_contracts()


def good_probe():
    lines = [
        "SECTION device",
        "hostname=reCamera",
        "kernel=Linux 5.10.4 riscv64",
        "arch=riscv64",
        "os_name=Buildroot",
        "os_version=-g0febb0d",
        "buildroot_version=2021.05",
        'api_raw={"code":0,"data":"0.2.4","msg":"OK"}',
        "uptime_s=100",
        "mac_eth0=2c:f7:f1:22:f0:18",
        "SECTION storage",
        "root_used_pct=95",
        "root_avail_kb=1000",
        "userdata_avail_kb=50000000",
        "SECTION tools",
    ]
    for tool in ("sh", "curl", "sha256sum", "setsid", "tar", "python3", "ffmpeg", "aplay"):
        lines.append(f"tool.{tool}=yes")
    lines.append("tool.gcc=no")
    lines.append("SECTION paths")
    for path in ("/userdata", "/userdata/Models", "/userdata/local", "/userdata/local/models",
                 "/mnt/system/sdk-release", "/mnt/system/lib", "/mnt/system/usr/lib",
                 "/mnt/system/usr/lib/3rd"):
        lines.append(f"path.{path}=yes")
    lines += ["SECTION services", "SECTION processes", "SECTION models", "model_count=2", "SECTION end"]
    return "\n".join(lines)


def test_parse_probe_sections():
    data = probe_device.parse_probe(good_probe())
    assert data["device"]["arch"] == "riscv64"
    assert data["storage"]["userdata_avail_kb"] == "50000000"
    assert data["tools"]["tool.ffmpeg"] == "yes"


def test_api_version_extracts_data():
    assert probe_device.api_version('{"code":0,"data":"0.2.4"}') == "0.2.4"
    assert probe_device.api_version("not json") is None


def test_contract_passes_on_good_inventory():
    inventory = probe_device.parse_probe(good_probe())
    checks, drift = probe_device.check_contract(inventory, CONTRACT, "2c:f7:f1:22:f0:18")
    errors = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    assert not errors, errors
    assert not drift


def test_contract_flags_missing_tool():
    inventory = probe_device.parse_probe(good_probe())
    inventory["tools"]["tool.ffmpeg"] = "no"
    checks, drift = probe_device.check_contract(inventory, CONTRACT, None)
    assert any(not c["ok"] and "ffmpeg" in c["detail"] for c in checks)
    assert drift


def test_wrong_mac_flags_identity():
    inventory = probe_device.parse_probe(good_probe())
    checks, _ = probe_device.check_contract(inventory, CONTRACT, "00:00:00:00:00:00")
    assert any(c["name"] == "mac_identity" and not c["ok"] for c in checks)


def test_contract_accepts_newer_os():
    inventory = probe_device.parse_probe(good_probe().replace('"0.2.4"', '"0.4.14"'))
    checks, drift = probe_device.check_contract(inventory, CONTRACT, None)
    os_checks = [c for c in checks if c["name"] == "os_version"]
    assert os_checks and all(c["ok"] for c in os_checks), os_checks
    assert not any("minimum" in d for d in drift)


def test_contract_flags_older_os():
    inventory = probe_device.parse_probe(good_probe().replace('"0.2.4"', '"0.1.0"'))
    checks, drift = probe_device.check_contract(inventory, CONTRACT, None)
    assert any(c["name"] == "os_version" and not c["ok"] for c in checks)
    assert any("minimum" in d for d in drift)


def test_redact_password_and_sshpass():
    text = "sshpass -p hunter2 ssh user@host password=hunter2"
    out = redact(text, "hunter2")
    assert "hunter2" not in out
    assert "***" in out


def test_semver_tuple():
    assert semver_tuple("0.2.4") == (0, 2, 4)
    assert semver_tuple("v1.2") == (1, 2)


def test_sha256_file(tmp_path):
    path = tmp_path / "a"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"