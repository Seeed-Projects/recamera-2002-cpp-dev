import json
from pathlib import Path

import check_knowledge


def test_real_skill_knowledge_passes(skill_root):
    code = check_knowledge.main(["--skill-root", str(skill_root)])
    assert code == 0


def test_lock_update_is_stable(tmp_path):
    hub = tmp_path / "knowhubs/reCamera_KnowHub"
    hub.mkdir(parents=True)
    # The shipped overview.md keeps its Chinese snapshot marker; the fixture
    # encodes it as \u8def\u5f84\u5feb\u7167 via escapes so this file stays ASCII.
    (hub / "overview.md").write_text(
        "# overview\n\n\u8def\u5f84\u5feb\u7167\uff1a2026-09-18\n\n| Area | Wiki | Use |\n|---|---|---|\n"
        "| Quick start | `reCamera_Basic/a.md` | x |\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    pages = checker.check_lock(update=True)
    assert pages
    lock = json.loads((tmp_path / "configs/wiki-snapshot.lock.json").read_text(encoding="utf-8"))
    assert lock["pages"][0]["path"] == "reCamera_Basic/a.md"
    assert lock["snapshot_date"] == "2026-09-18"


def test_broken_link_detected(tmp_path):
    (tmp_path / "a.md").write_text("[missing](nope.md)\n", encoding="utf-8")
    checker = check_knowledge.Checker(tmp_path)
    checker.check_links()
    assert any("broken link" in error for error in checker.errors)


def test_record_missing_field_detected(tmp_path):
    hub = tmp_path / "knowhubs/reCamera_KnowHub"
    hub.mkdir(parents=True)
    # Legacy Chinese run-records heading, English entry labels: every required
    # field except Status is missing.
    (hub / "demo-records.md").write_text(
        "# records\n\n## \u5df2\u8dd1\u8fc7\u7684 demo\n\n### bad (2026-10-22)\n- Status: ✅ success\n\n## Capability catalog\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    checker.check_records()
    assert any("missing Capability" in error for error in checker.errors)


def test_record_bad_status_detected(tmp_path):
    hub = tmp_path / "knowhubs/reCamera_KnowHub"
    hub.mkdir(parents=True)
    (hub / "demo-records.md").write_text(
        "# records\n\n## Demos run so far\n\n### bad (2026-10-22)\n"
        "- Status: maybe\n- Capability: c\n- Model files: none\n- Dependencies: none\n"
        "- Build issues: none\n- Verification: ok\n- Lessons: none\n\n## Capability catalog\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    checker.check_records()
    assert any("status must start" in error for error in checker.errors)


def test_english_record_accepted(tmp_path):
    hub = tmp_path / "knowhubs/reCamera_KnowHub"
    hub.mkdir(parents=True)
    (hub / "demo-records.md").write_text(
        "# records\n\n## Demos run so far\n\n### good (2026-10-22)\n"
        "- Status: ✅ success\n- Capability: c\n- Model files: none\n- Dependencies: none\n"
        "- Build issues: none\n- Verification: ok\n- Lessons: none\n\n## Capability catalog\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    checker.check_records()
    assert not checker.errors


def test_legacy_chinese_record_still_accepted(tmp_path):
    hub = tmp_path / "knowhubs/reCamera_KnowHub"
    hub.mkdir(parents=True)
    # Old-format record (Chinese labels/status) written by earlier versions.
    (hub / "demo-records.md").write_text(
        "# records\n\n## \u5df2\u8dd1\u8fc7\u7684 demo\n\n### good (2026-01-01)\n"
        "- \u72b6\u6001\uff1a✅ \u6210\u529f\n- \u80fd\u529b\uff1ac\n- \u6a21\u578b\u6587\u4ef6\uff1a\u65e0\n- \u7279\u6b8a\u4f9d\u8d56\uff1a\u65e0\n"
        "- \u7f16\u8bd1\u95ee\u9898\uff1a\u65e0\n- \u9a8c\u8bc1\u7ed3\u679c\uff1aok\n- \u5173\u952e\u7ecf\u9a8c\uff1anone\n\n## \u80fd\u529b\u76ee\u5f55\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    checker.check_records()
    assert not checker.errors


def test_secret_detected(tmp_path):
    (tmp_path / "doc.md").write_text("sshpass -p hunter2 ssh recamera@host\n", encoding="utf-8")
    checker = check_knowledge.Checker(tmp_path)
    checker.check_secrets()
    assert any("sshpass" in error for error in checker.errors)


def test_chinese_password_detected(tmp_path):
    # Fixture carries a legacy Chinese password phrase; glyphs appear only as
    # \u8bbe\u5907\u5bc6\u7801… escapes, never literal CJK.
    (tmp_path / "doc.md").write_text(
        "\u8bbe\u5907\u5bc6\u7801 `hunter2`\uff0c\u8bf7\u52ff\u5916\u4f20\u3002\n",
        encoding="utf-8",
    )
    checker = check_knowledge.Checker(tmp_path)
    checker.check_secrets()
    assert any("password" in error for error in checker.errors)


def test_password_assignment_detected(tmp_path):
    (tmp_path / "doc.md").write_text("password: hunter2\n", encoding="utf-8")
    checker = check_knowledge.Checker(tmp_path)
    checker.check_secrets()
    assert any("password" in error for error in checker.errors)


def test_personal_home_path_detected(tmp_path):
    (tmp_path / "doc.md").write_text("see /home/alice/work/demo\n", encoding="utf-8")
    checker = check_knowledge.Checker(tmp_path)
    checker.check_secrets()
    assert any("absolute path" in error for error in checker.errors)


def test_device_home_path_is_allowed(tmp_path):
    (tmp_path / "doc.md").write_text("see /home/recamera/demo\n", encoding="utf-8")
    checker = check_knowledge.Checker(tmp_path)
    checker.check_secrets()
    assert not checker.errors


def test_agent_contract_accepts_real_skill(skill_root):
    checker = check_knowledge.Checker(skill_root)
    checker.check_agent_contract()
    assert not checker.errors


def test_agent_contract_requires_name_and_description(tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    checker = check_knowledge.Checker(skill)
    checker.check_agent_contract()
    assert any("description" in error for error in checker.errors)


def test_agent_contract_rejects_name_mismatch(tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text('---\nname: other\ndescription: "x"\n---\n', encoding="utf-8")
    checker = check_knowledge.Checker(skill)
    checker.check_agent_contract()
    assert any("match directory" in error for error in checker.errors)


def test_agent_contract_checks_openai_yaml(tmp_path):
    skill = tmp_path / "demo"
    (skill / "agents").mkdir(parents=True)
    (skill / "SKILL.md").write_text('---\nname: demo\ndescription: "x"\n---\n', encoding="utf-8")
    (skill / "agents/openai.yaml").write_text("interface:\n  display_name: Demo\n", encoding="utf-8")
    checker = check_knowledge.Checker(skill)
    checker.check_agent_contract()
    assert any("short_description" in error for error in checker.errors)
