import hashlib

import pytest

from devkit.artifacts import deterministic_tar
from devkit.reports import STAGES, new_report, report_ok, set_stage


def test_deterministic_tar_is_reproducible(tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "b.txt").write_text("beta")
    files = [(tmp_path / "a.txt", "a.txt"), (tmp_path / "b.txt", "b.txt")]
    first = tmp_path / "one.tar.gz"
    second = tmp_path / "two.tar.gz"
    manifest_one = deterministic_tar(files, first, "pkg")
    manifest_two = deterministic_tar(list(reversed(files)), second, "pkg")
    assert first.read_bytes() == second.read_bytes()
    assert manifest_one == manifest_two
    assert [entry["path"] for entry in manifest_one] == ["a.txt", "b.txt"]
    assert manifest_one[0]["sha256"] == hashlib.sha256(b"alpha").hexdigest()


def test_tar_sets_executable_bit(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n")
    archive = tmp_path / "pkg.tar.gz"
    deterministic_tar([(script, "run.sh")], archive, "pkg", executable_suffixes=(".sh",))
    import tarfile
    with tarfile.open(archive) as tar:
        member = tar.getmember("pkg/run.sh")
    assert member.mode == 0o755
    assert member.mtime == 0
    assert member.uid == 0 and member.gid == 0


def test_new_report_has_all_stages_not_run():
    report = new_report("demo-smoke", "x")
    assert set(report["stages"]) == set(STAGES)
    assert all(entry["status"] == "not_run" for entry in report["stages"].values())
    assert report_ok(report)


def test_set_stage_records_status_and_evidence():
    report = new_report("demo-package", "x")
    set_stage(report, "offline", "passed", "archive verified", {"sha256": "abc"})
    assert report["stages"]["offline"]["status"] == "passed"
    assert report["stages"]["offline"]["evidence"]["sha256"] == "abc"


def test_failed_stage_makes_report_not_ok():
    report = new_report("demo-package", "x")
    set_stage(report, "offline", "failed")
    assert not report_ok(report)


def test_unknown_stage_is_rejected():
    report = new_report("demo-package", "x")
    with pytest.raises(ValueError):
        set_stage(report, "magic", "passed")
    with pytest.raises(ValueError):
        set_stage(report, "offline", "maybe")