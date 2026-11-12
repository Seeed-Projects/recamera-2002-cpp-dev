import json

import record_demo

# A legacy records file as written by older tool versions: Chinese section
# headings and field labels. The glyphs live in the source as \u5df2...
# escapes (never literal CJK here) so backward-compatible parsing stays
# covered while this file itself remains ASCII.
RECORDS = """# records

## \u5df2\u8dd1\u8fc7\u7684 demo

### old_demo (2026-01-01)
- \u72b6\u6001\uff1a✅ \u6210\u529f
- \u80fd\u529b\uff1aold
- \u6a21\u578b\u6587\u4ef6\uff1a\u65e0
- \u7279\u6b8a\u4f9d\u8d56\uff1a\u65e0
- \u7f16\u8bd1\u95ee\u9898\uff1a\u65e0
- \u9a8c\u8bc1\u7ed3\u679c\uff1aok
- \u5173\u952e\u7ecf\u9a8c\uff1anone

## \u80fd\u529b\u76ee\u5f55

text
"""


def make_result(tmp_path, **overrides):
    data = {
        "name": "new_demo",
        "date": "2026-10-22",
        "status": "success",
        "capability": "C++ hello demo",
        "model_files": "none",
        "dependencies": "none",
        "build_issues": "none",
        "verification": "stdout marker matched",
        "lessons": "use sudo -S with a password from env",
        "wiki_issues": "",
    }
    data.update(overrides)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_append_before_next_section(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    code = record_demo.main(["--result", str(make_result(tmp_path)), "--records", str(records)])
    assert code == 0
    text = records.read_text(encoding="utf-8")
    assert text.index("### new_demo (2026-10-22)") < text.index("## \u80fd\u529b\u76ee\u5f55")
    assert text.index("### old_demo") < text.index("### new_demo")
    assert "- Status: ✅ success" in text


def test_legacy_tokens_write_english(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    # Legacy result JSON may still carry Chinese status/none tokens.
    result = make_result(tmp_path, status="\u6210\u529f", model_files="\u65e0", lessons="\u65e0")
    assert record_demo.main(["--result", str(result), "--records", str(records)]) == 0
    text = records.read_text(encoding="utf-8")
    assert "- Status: ✅ success" in text
    assert "- Model files: none" in text
    assert "- Lessons: none" in text
    assert "\u6210\u529f" not in text.split("### new_demo")[1]


def test_english_section_heading_accepted(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text("# records\n\n## Demos run so far\n\n## Capability catalog\n\ntext\n",
                       encoding="utf-8")
    code = record_demo.main(["--result", str(make_result(tmp_path)), "--records", str(records)])
    assert code == 0
    text = records.read_text(encoding="utf-8")
    assert text.index("### new_demo (2026-10-22)") < text.index("## Capability catalog")


def test_duplicate_rejected(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    result = make_result(tmp_path)
    assert record_demo.main(["--result", str(result), "--records", str(records)]) == 0
    assert record_demo.main(["--result", str(result), "--records", str(records)]) == 1


def test_invalid_status_rejected(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    result = make_result(tmp_path, status="maybe")
    assert record_demo.main(["--result", str(result), "--records", str(records)]) == 1
    assert "### new_demo" not in records.read_text(encoding="utf-8")


def test_dry_run_does_not_write(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    before = records.read_text(encoding="utf-8")
    code = record_demo.main(["--result", str(make_result(tmp_path)), "--records", str(records), "--dry-run"])
    assert code == 0
    assert records.read_text(encoding="utf-8") == before


def test_missing_name_rejected(tmp_path):
    records = tmp_path / "demo-records.md"
    records.write_text(RECORDS, encoding="utf-8")
    result = make_result(tmp_path, name="")
    assert record_demo.main(["--result", str(result), "--records", str(records)]) == 1


def test_default_records_go_to_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAMERA_DATA_DIR", str(tmp_path / "data"))
    code = record_demo.main(["--result", str(make_result(tmp_path))])
    assert code == 0
    records = tmp_path / "data" / "demo-records.md"
    assert records.is_file()
    assert "### new_demo (2026-10-22)" in records.read_text(encoding="utf-8")
    assert "### new_demo (2026-10-22)" not in record_demo.SEED_RECORDS.read_text(encoding="utf-8")


def test_dry_run_does_not_seed_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAMERA_DATA_DIR", str(tmp_path / "data"))
    code = record_demo.main(["--result", str(make_result(tmp_path)), "--dry-run"])
    assert code == 0
    assert not (tmp_path / "data" / "demo-records.md").exists()
