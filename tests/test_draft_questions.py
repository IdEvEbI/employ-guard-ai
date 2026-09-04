"""按项目出基础题与追问。"""

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
    _normalize_projects,
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
        "projects": [
            {
                "name": "智能客服 RAG",
                "why_selected": "简历主项目篇幅最长。",
                "basics": [
                    {
                        "id": "P1-B1",
                        "question": "这个项目里检索链路怎么串？",
                        "focus": "讲清召回与重排。",
                        "follow_ups": ["为什么选混合检索？", "失败如何降级？"],
                    },
                    {
                        "id": "P1-B2",
                        "question": "你负责哪一段？",
                        "focus": "角色边界。",
                        "follow_ups": ["和同学如何交接？"],
                    },
                ],
                "deep_dives": [
                    {
                        "id": "P1-D1",
                        "question": "指标掉了你会怎么定位？",
                        "focus": "可观测与复现。",
                        "why": "简历写了评测数字。",
                    }
                ],
            }
        ],
    }


def test_help_lists_draft_questions() -> None:
    result = runner.invoke(app, ["draft-questions", "--help"])
    assert result.exit_code == 0
    assert "按" in result.stdout or "项目" in result.stdout


def test_normalize_projects_filters_empty() -> None:
    data = {
        "projects": [
            {
                "name": "A",
                "basics": [
                    {"question": "有效？", "focus": "f", "follow_ups": ["追问"]},
                    {"question": "  ", "focus": "x", "follow_ups": []},
                ],
                "deep_dives": [{"question": "深挖？", "why": "依据"}],
            },
            {"name": "", "basics": [{"question": "应丢弃"}]},
            {"name": "空题", "basics": [], "deep_dives": []},
        ]
    }
    projects = _normalize_projects(data)
    assert len(projects) == 1
    assert projects[0]["name"] == "A"
    assert len(projects[0]["basics"]) == 1
    assert projects[0]["basics"][0]["follow_ups"] == ["追问"]
    assert len(projects[0]["deep_dives"]) == 1


def test_draft_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("# 简历文本\n\n智能客服 RAG 项目经历。\n", encoding="utf-8")
    result = draft_questions(
        md, root=tmp_path, questions_assessor=_fake_assessor
    )
    assert result.project_count == 1
    assert result.question_count == 3
    report = result.report_md.read_text(encoding="utf-8")
    assert "仅供练习" in report
    assert "智能客服 RAG" in report
    assert "可能追问" in report
    assert "resume" in report
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["tool"] == "draft-questions"
    assert data["is_practice_only"] is True
    assert data["not_in_triage_by_default"] is True
    assert data["judges_content"] is False
    assert result.report_md.name.endswith(".questions.md")


def test_draft_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "LLM RAG Agent 微调")
    extract_resume_text(pdf, root=tmp_path)
    result = draft_questions(
        pdf, root=tmp_path, questions_assessor=_fake_assessor
    )
    assert result.report_md.is_file()
    assert result.report_json.is_file()


def test_empty_body_fails(tmp_path: Path) -> None:
    md = tmp_path / "empty.resume.md"
    md.write_text("   \n", encoding="utf-8")
    with pytest.raises(DraftQuestionsError, match="为空"):
        draft_questions(
            md, root=tmp_path, questions_assessor=_fake_assessor
        )


def test_cli_draft_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "cli.resume.md"
    md.write_text("主项目：RAG\n", encoding="utf-8")
    monkeypatch.setattr(
        "employ_guard.cli.draft_questions",
        lambda source, job_description=None: draft_questions(
            source,
            job_description=job_description,
            root=tmp_path,
            questions_assessor=_fake_assessor,
        ),
    )
    result = runner.invoke(app, ["draft-questions", str(md)])
    assert result.exit_code == 0
    assert "按项目练习题" in result.stdout
    assert "智能客服 RAG" in result.stdout
