"""技能结构检测（分组 / 意向 / 覆盖 / 市场高频 / 排序）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from employ_guard.check_skills import (
    CheckSkillsError,
    check_skills,
    normalize_skills_fields,
)
from employ_guard.cli import app

runner = CliRunner()


def _sub(status: str, note: str) -> dict[str, str]:
    return {"status": status, "note": note}


def _fake_pass(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "pass",
        "target_role": "大模型应用工程师",
        "grouping": _sub("pass", "按 RAG / Agent 分组"),
        "role_alignment": _sub("pass", "与应用岗同向"),
        "project_coverage": _sub("pass", "项目写了 LangGraph 与混合检索"),
        "market_alignment": _sub("pass", "可见 RAG、Agent、评测类高频"),
        "skill_order": _sub("pass", "岗位相关靠前，Docker 靠后"),
        "verbosity_note": None,
        "fixes": [],
        "notes": [],
    }


def _fake_fail(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "fail",
        "target_role": "大模型工程师",
        "grouping": _sub("doubtful", "一整段堆砌"),
        "role_alignment": _sub("pass", "大致同向"),
        "project_coverage": _sub("fail", "技能列了数十项，项目无法支撑"),
        "market_alignment": _sub("doubtful", "几乎只有通用语言"),
        "skill_order": _sub("doubtful", "Python / Docker 置顶"),
        "verbosity_note": "多处「熟练掌握」",
        "fixes": ["删去项目未用到的技能，按类别分组。"],
        "notes": [],
    }


def test_help_lists_check_skills() -> None:
    result = runner.invoke(app, ["check-skills", "--help"])
    assert result.exit_code == 0
    assert "技能" in result.stdout


def test_normalize_skills_fields_derives_fail() -> None:
    fields = normalize_skills_fields(
        {
            "grouping": _sub("pass", "ok"),
            "role_alignment": _sub("pass", "ok"),
            "project_coverage": _sub("fail", "脱节"),
            "market_alignment": _sub("pass", "ok"),
            "skill_order": _sub("pass", "ok"),
        }
    )
    assert fields["status"] == "fail"
    assert fields["fixes"]


def test_normalize_skills_fields_derives_doubtful_from_order() -> None:
    fields = normalize_skills_fields(
        {
            "grouping": _sub("pass", "ok"),
            "role_alignment": _sub("pass", "ok"),
            "project_coverage": _sub("pass", "ok"),
            "market_alignment": _sub("pass", "ok"),
            "skill_order": _sub("doubtful", "Coze / Python 置顶"),
            "fixes": ["把 RAG / Agent 相关技能挪到技能区前部。"],
        }
    )
    assert fields["status"] == "doubtful"
    assert fields["skill_order"]["status"] == "doubtful"


def test_normalize_skills_fields_derives_doubtful_from_market() -> None:
    fields = normalize_skills_fields(
        {
            "grouping": _sub("pass", "ok"),
            "role_alignment": _sub("pass", "ok"),
            "project_coverage": _sub("pass", "ok"),
            "market_alignment": _sub("doubtful", "缺 RAG/Agent 高频"),
            "skill_order": _sub("pass", "ok"),
        }
    )
    assert fields["status"] == "doubtful"


def test_check_skills_pass_from_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text(
        "# 简历文本\n\n求职意向：大模型应用工程师\n\n## 专业技能\n"
        "RAG：混合检索\nAgent：LangGraph\n",
        encoding="utf-8",
    )
    result = check_skills(md, root=tmp_path, skills_assessor=_fake_pass)
    assert result.status == "pass"
    assert result.report_md.is_file()
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["tool"] == "check-skills"
    assert data["market_alignment"]["status"] == "pass"
    assert data["skill_order"]["status"] == "pass"
    report = result.report_md.read_text(encoding="utf-8")
    assert "SK5" in report and "SK6" in report
    assert "001" in report


def test_check_skills_fail_writes_report(tmp_path: Path) -> None:
    md = tmp_path / "weak.resume.md"
    md.write_text("求职意向：大模型工程师\n技能：Python Java Vue 熟练掌握\n", encoding="utf-8")
    result = check_skills(md, root=tmp_path, skills_assessor=_fake_fail)
    assert result.status == "fail"
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["project_coverage"]["status"] == "fail"
    assert data["verbosity_note"]


def test_check_skills_empty_raises(tmp_path: Path) -> None:
    md = tmp_path / "empty.resume.md"
    md.write_text("   \n", encoding="utf-8")
    with pytest.raises(CheckSkillsError, match="为空"):
        check_skills(md, root=tmp_path, skills_assessor=_fake_pass)


def test_cli_check_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "data" / "output" / "x" / "x.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text("求职意向：大模型工程师\n## 专业技能\nRAG\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return check_skills(source, root=tmp_path, skills_assessor=_fake_pass, **kwargs)

    monkeypatch.setattr("employ_guard.cli.check_skills", _fake)
    result = runner.invoke(app, ["check-skills", str(md)])
    assert result.exit_code == 0, result.output
    assert "过本项" in result.output or "pass" in result.output
