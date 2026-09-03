"""查文字表达。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.check_writing import (
    CheckWritingError,
    check_writing,
    rule_check_english_punctuation,
    rule_check_list_punctuation,
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


def _clean_assessor(_text: str) -> dict:
    return {"llm_findings": []}


def _llm_assessor(_text: str) -> dict:
    return {
        "llm_findings": [
            {
                "id": "W1",
                "category": "错别字",
                "line": 3,
                "excerpt": "调优参树",
                "note": "应为「参数」",
                "suggestion": "调优参数",
                "method": "llm",
            },
            {
                "id": "W4",
                "category": "用语专业",
                "line": 4,
                "excerpt": "熟悉 Vue 全家桶",
                "note": "口语化；建议改为具体技术栈",
                "suggestion": "Vue、Vue Router、Pinia",
                "method": "llm",
            },
        ]
    }


def test_rule_long_blocks_suggests_bullets() -> None:
    from employ_guard.check_writing import rule_check_long_blocks

    lines = [
        "1." + ("负责实现检索增强生成链路并完成向量化与重排优化，" * 6),
    ]
    findings = rule_check_long_blocks(lines)
    assert findings
    assert findings[0]["id"] == "W4"
    assert "项目符号" in findings[0]["note"]


def test_rule_skill_templates_and_grouping() -> None:
    from employ_guard.check_writing import rule_check_skill_templates

    lines = [
        "●熟练运用 Numpy 做分析；",
        "●熟练掌握 Python 与 Linux；",
        "●熟练使用 PyTorch 训练；",
        "●熟练掌握深度学习理论；",
        "●熟练使用模型优化方法；",
        "●熟练掌握日志与版本管理；",
        "工作经历",
        "2024.09-至今 某公司",
    ]
    findings = rule_check_skill_templates(lines)
    notes = " ".join(item["note"] for item in findings)
    assert any(item["id"] == "W4" for item in findings)
    assert "熟练" in notes
    assert "分组" in notes


def test_rule_list_punctuation_inconsistent() -> None:
    lines = [
        "项目经历",
        "1.负责 RAG 检索；",
        "2.实现 Agent 编排。",
        "3.部署上线",
    ]
    findings = rule_check_list_punctuation(lines)
    assert len(findings) == 1
    assert findings[0]["id"] == "W2"


def test_rule_list_punctuation_semicolon_last_period_ok() -> None:
    lines = [
        "1.负责 RAG；",
        "2.实现 Agent。",
    ]
    assert rule_check_list_punctuation(lines) == []


def test_rule_en_punct_in_chinese_sentence() -> None:
    lines = ["负责开发, 实现检索增强"]
    findings = rule_check_english_punctuation(lines)
    assert len(findings) == 1
    assert findings[0]["id"] == "W3"


def test_rule_en_punct_skips_ascii_skill_line() -> None:
    lines = ["Python, Flask, FastAPI"]
    assert rule_check_english_punctuation(lines) == []


def test_rule_en_punct_skips_date_line() -> None:
    lines = ["2026.03-至今 某项目 python 开发"]
    assert rule_check_list_punctuation(lines) == []


def test_check_writing_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "data" / "output" / "demo" / "demo.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "\n".join(
            [
                "# 简历文本",
                "",
                "负责开发, 实现 RAG。",
                "1.负责检索；",
                "2.实现生成",
            ]
        ),
        encoding="utf-8",
    )
    result = check_writing(md, root=tmp_path, writing_assessor=_clean_assessor)
    assert result.writing_pass is False
    ids = {item["id"] for item in result.findings}
    assert "W2" in ids
    assert "W3" in ids
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["evaluates_content_bar"] is False


def test_check_writing_llm_findings(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("正文\n", encoding="utf-8")
    result = check_writing(md, root=tmp_path, writing_assessor=_llm_assessor)
    assert result.writing_pass is False
    assert any(item["id"] == "W1" for item in result.findings)
    assert "用语专业" in result.report_md.read_text(encoding="utf-8")


def test_requires_read_resume_first(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "bare.pdf"
    _write_pdf(pdf, "hello resume")
    with pytest.raises(CheckWritingError, match="read-resume"):
        check_writing(pdf, root=tmp_path, writing_assessor=_clean_assessor)


def test_check_writing_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "Python RAG Agent")
    extract_resume_text(pdf, root=tmp_path)
    result = check_writing(pdf, root=tmp_path, writing_assessor=_clean_assessor)
    assert result.writing_pass is True
    assert (tmp_path / "data" / "output" / "resumes" / "ok" / "ok.writing.md").is_file()


def test_cli_pass_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "demo.resume.md"
    md.write_text("干净正文\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return check_writing(source, root=tmp_path, writing_assessor=_clean_assessor)

    monkeypatch.setattr("employ_guard.cli.check_writing", _fake)
    result = runner.invoke(app, ["check-writing", str(md)])
    assert result.exit_code == 0, result.output
    assert "未发现明显问题" in result.stdout


def test_cli_findings_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "demo.resume.md"
    md.write_text("x\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return check_writing(source, root=tmp_path, writing_assessor=_llm_assessor)

    monkeypatch.setattr("employ_guard.cli.check_writing", _fake)
    result = runner.invoke(app, ["check-writing", str(md)])
    assert result.exit_code == 0, result.output
    assert "有待改进" in result.stdout
