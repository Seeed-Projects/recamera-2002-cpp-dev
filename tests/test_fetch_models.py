import hashlib
import json

import fetch_models


def test_list_assets(tmp_path):
    assert fetch_models.main(["--list"]) == 0


def test_dry_run_prints_url(tmp_path, capsys):
    code = fetch_models.main(["--name", "yolo11n", "--out", str(tmp_path / "models"), "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "yolo11n_cv181x_int8.cvimodel" in out
    assert "files.seeedstudio.com" in out
    assert not (tmp_path / "models").exists()


def test_unknown_asset_is_rejected(tmp_path):
    assert fetch_models.main(["--name", "nope", "--out", str(tmp_path)]) == 2


def test_cached_file_with_matching_hash(tmp_path):
    payload = b"model-bytes"
    asset = {
        "file": "m.cvimodel",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    out = tmp_path / "models"
    out.mkdir()
    (out / "m.cvimodel").write_bytes(payload)
    result = fetch_models.fetch_one("m", asset, "https://example.invalid", out,
                                    force=False, allow_unpinned=False, dry_run=False)
    assert result["ok"] and result["cached"]


def test_cached_file_with_wrong_hash_is_refused(tmp_path):
    payload = b"model-bytes"
    asset = {"file": "m.cvimodel", "sha256": "0" * 64, "size": len(payload)}
    out = tmp_path / "models"
    out.mkdir()
    (out / "m.cvimodel").write_bytes(payload)
    result = fetch_models.fetch_one("m", asset, "https://example.invalid", out,
                                    force=False, allow_unpinned=False, dry_run=False)
    assert not result["ok"]
    assert "hash differs" in result["error"]


def test_unpinned_is_refused_without_flag(tmp_path):
    asset = {"file": "m.cvimodel"}
    result = fetch_models.fetch_one("m", asset, "https://example.invalid", tmp_path,
                                    force=False, allow_unpinned=False, dry_run=True)
    assert not result["ok"]
    result = fetch_models.fetch_one("m", asset, "https://example.invalid", tmp_path,
                                    force=False, allow_unpinned=True, dry_run=True)
    assert result["ok"] and result["dry_run"]


def test_lock_entries_are_complete():
    lock = fetch_models.load_assets()
    for name, asset in lock["assets"].items():
        assert asset["file"].endswith(".cvimodel"), name
        assert len(asset["sha256"]) == 64, name
        assert asset["size"] > 0, name