import sys

import capture_evidence


def test_capture_files_copies_and_flags_missing(tmp_path):
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    out = tmp_path / "out"
    out.mkdir()
    checks = []
    copied = capture_evidence.capture_files([src, tmp_path / "missing.mp4"], out, checks)
    assert copied == [out / "input.mp4"]
    assert any(not check["ok"] and "missing" in check["detail"] for check in checks)


def test_capture_udp_accepts_receiver_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    checks = []
    receiver = [sys.executable, "-c", "open('frame.jpg','wb').write(b'jpeg')"]
    artifacts = capture_evidence.capture_udp(receiver, out, 10, checks)
    assert (out / "frame.jpg").is_file()
    assert any(path.name == "frame.jpg" for path in artifacts)
    assert all(check["ok"] for check in checks), checks


def test_capture_udp_fails_without_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    checks = []
    receiver = [sys.executable, "-c", "pass"]
    artifacts = capture_evidence.capture_udp(receiver, out, 10, checks)
    assert artifacts
    assert any(not check["ok"] for check in checks)