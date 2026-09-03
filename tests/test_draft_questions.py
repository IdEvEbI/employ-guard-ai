"""出练习题。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.draft_questions import (
    DEFAULT_SCOPE,
    DraftQuestionsError,
    _normalize_questions,
    draft_questions,
)
from employ_guard.read_resume import extract_resume_text

runner = CliRunner()


def _write_pdf(path: Path, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _fake_assessor(_text: str, job: str | None) -> dict:
    scope = (
        "面向某岗位方向练习，仍不是该公司真题"
        if job
        else DEFAULT_SCOPE
    )
    return {
        "scope": scope,
        "questions": [
            {
                "id": "Q1",
                "category": "项目深挖",
                "question": "你在主项目里如何做混合检索？",
                "why": "简历写了混合检索。",
                "focus": "讲清召回与重排分工。",
            },
            {
                "id": "Q2",
                "category": "Agent工具",
                "question": "Agent 失败时如何降级？",
                "why": "简历提到降级。",
                "focus": "说清触发条件与兜底。",
            },
        ],
    }


def test_help_lists_draft_questions() -> None:
    result = runner.invoke(app, ["draft-questions", "--help"])
    assert result.exit_code == 0
    assert "练习题" in result.stdout


def test_normalize_filters_empty_and_unknown_category() -> None:
    data = {
        "questions": [
            {"id": "Q1", "category": "项目深挖", "question": "有效题？", "why": "a", "focus": "b"},
            {"id": "Q2", "category": "瞎分类", "question": "另一题？", "why": "c", "focus": "d"},
            {"id": "Q3", "category": "评测指标", "question": "  ", "why": "e", "focus": "f"},
        ]
    }
    items = _normalize_questions(data)
    assert len(items) == 2
    assert items[1]["category"] == "其它"


def test_draft_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("# 简历文本\n\nRAG 与 Agent 项目经历。\n", encoding="utf-8")
    result = draft_questions(md, root=tmp_path, questions_assessor=_fake_assessor)
    assert len(result.questions) == 2
    assert DEFAULT_SCOPE in result.scope or "真题" in result.scope
    report = result.report_md.read_text(encoding="utf-8")
    assert "仅供练习" in report
    assert "不是某家公司的真题" in report
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["is_practice_only"] is True
    assert data["not_company_real_questions"] is True
    assert data["judges_content"] is False


def test_draft_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "LLM RAG Agent 微调")
    extract_resume_text(pdf, root=tmp_path)
    result = draft_questions(pdf, root=tmp_path, questions_assessor=_fake_assessor)
    assert result.report_md.is_file()


def test_requires_resume_text(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "alone.pdf"
    _write_pdf(pdf, "ONLY")
    with pytest.raises(DraftQuestionsError, match="read-resume"):
        draft_questions(pdf, root=tmp_path, questions_assessor=_fake_assessor)


def test_cli_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "data" / "output" / "demo" / "demo.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 简历文本\n\n正文\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return draft_questions(source, root=tmp_path, questions_assessor=_fake_assessor)

    monkeypatch.setattr("employ_guard.cli.draft_questions", _fake)
    result = runner.invoke(app, ["draft-questions", str(md)])
    assert result.exit_code == 0, result.output
    assert "练习题" in result.stdout
    assert "不判能不能投" in result.stdout
