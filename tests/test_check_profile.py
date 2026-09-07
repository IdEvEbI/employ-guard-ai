"""基础信息检测（首页岗位类表述 / C1）。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.check_profile import (
    CheckProfileError,
    check_profile,
    normalize_profile_fields,
)
from employ_guard.cli import app
from employ_guard.read_resume import extract_resume_text

runner = CliRunner()


def _write_pdf(path: Path, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _fake_pass(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "pass",
        "target_role": "大模型工程师",
        "homepage_evidence": "求职意向：大模型工程师",
        "conflict_note": None,
        "fixes": [],
        "notes": [],
    }


def _fake_fail(_text: str, _hint: dict | None) -> dict:
    return {
        "status": "fail",
        "target_role": None,
        "homepage_evidence": "首页仅有姓名与项目名，无岗位类表述",
        "conflict_note": None,
        "fixes": ["在首页基本信息写明岗位类表述。"],
        "notes": [],
    }


def test_help_lists_check_profile() -> None:
    result = runner.invoke(app, ["check-profile", "--help"])
    assert result.exit_code == 0
    assert "岗位" in result.stdout or "C1" in result.stdout or "基本信息" in result.stdout


def test_normalize_profile_fields_defaults_fail() -> None:
    fields = normalize_profile_fields({"status": "unknown"})
    assert fields["status"] == "fail"
    assert fields["fixes"]


def test_check_profile_pass_from_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text(
        "# 简历文本\n\n姓名：麦丽华\n求职意向：大模型工程师\n",
        encoding="utf-8",
    )
    result = check_profile(md, root=tmp_path, profile_assessor=_fake_pass)
    assert result.status == "pass"
    assert result.target_role == "大模型工程师"
    assert result.report_md.is_file()
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["tool"] == "check-profile"
    assert data["judges_content"] is False
    assert data["status"] == "pass"
    assert "不替代" in result.report_md.read_text(encoding="utf-8")


def test_check_profile_fail_from_md(tmp_path: Path) -> None:
    md = tmp_path / "emptyrole.resume.md"
    md.write_text("# 简历文本\n\n姓名：柯文\n项目：ICR 智库\n", encoding="utf-8")
    result = check_profile(md, root=tmp_path, profile_assessor=_fake_fail)
    assert result.status == "fail"
    assert result.target_role is None


def test_check_profile_uses_norm_and_parsed_hint(tmp_path: Path) -> None:
    md = tmp_path / "hint.resume.md"
    md.write_text("姓名：测\n", encoding="utf-8")
    (tmp_path / "hint.resume.norm.md").write_text(
        "# 简历文本（规范化）\n\n## 基本信息\n\n求职意向：算法工程师\n",
        encoding="utf-8",
    )
    (tmp_path / "hint.parsed.json").write_text(
        json.dumps(
            {
                "fields": {
                    "name": "测",
                    "target_role": "算法工程师",
                    "parse_incomplete": False,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def assessor(text: str, hint: dict | None) -> dict:
        seen["text"] = text
        seen["hint"] = hint
        return _fake_pass(text, hint)

    result = check_profile(md, root=tmp_path, profile_assessor=assessor)
    assert result.status == "pass"
    assert "算法工程师" in str(seen.get("text"))
    assert isinstance(seen.get("hint"), dict)
    assert seen["hint"]["target_role"] == "算法工程师"
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["used_normalized"] is True
    assert data["used_parsed_hint"] is True


def test_check_profile_from_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "姓名 测试 大模型工程师")
    extract_resume_text(pdf, root=tmp_path)
    result = check_profile(pdf, root=tmp_path, profile_assessor=_fake_pass)
    assert result.report_json.is_file()


def test_empty_body_fails(tmp_path: Path) -> None:
    md = tmp_path / "empty.resume.md"
    md.write_text("   \n", encoding="utf-8")
    with pytest.raises(CheckProfileError, match="为空"):
        check_profile(md, root=tmp_path, profile_assessor=_fake_pass)


def test_cli_check_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "cli.resume.md"
    md.write_text("姓名：测\n求职意向：大模型工程师\n", encoding="utf-8")
    monkeypatch.setattr(
        "employ_guard.cli.check_profile",
        lambda source: check_profile(
            source, root=tmp_path, profile_assessor=_fake_pass
        ),
    )
    result = runner.invoke(app, ["check-profile", str(md)])
    assert result.exit_code == 0
    assert "基础信息检测" in result.stdout
    assert "过本项" in result.stdout or "pass" in result.stdout
