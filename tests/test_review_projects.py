"""项目审阅（含金量 / 难度档）。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.read_resume import extract_resume_text
from employ_guard.review_projects import (
    DEFAULT_SCOPE,
    ReviewProjectsError,
    _normalize_projects,
    _normalize_tier,
    review_projects,
)

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
        "面向某岗位方向审阅，仍不是录用结论"
        if job
        else DEFAULT_SCOPE
    )
    return {
        "scope": scope,
        "summary": "宜把智能客服 RAG 作为主打练习项目。",
        "projects": [
            {
                "name": "智能客服 RAG",
                "role": "primary",
                "why_selected": "篇幅最长、链路较完整。",
                "value_tier": "high",
                "value_evidence": "写清了检索、重排与评测对比。",
                "difficulty_tier": "mid",
                "difficulty_evidence": "有链路但工程落地写得较少。",
                "fixes": ["补一条失败降级怎么做。"],
            }
        ],
    }


def test_help_lists_review_projects() -> None:
    result = runner.invoke(app, ["review-projects", "--help"])
    assert result.exit_code == 0
    assert "含金量" in result.stdout or "难度" in result.stdout


def test_normalize_tier_aliases() -> None:
    assert _normalize_tier("高") == "high"
    assert _normalize_tier("中") == "mid"
    assert _normalize_tier("low") == "low"
    assert _normalize_tier("unknown") == "mid"


def test_normalize_projects_filters_empty() -> None:
    data = {
        "projects": [
            {
                "name": "A",
                "role": "主项目",
                "value_tier": "高",
                "difficulty_tier": "低",
                "fixes": ["改1", "改2", "改3应丢"],
            },
            {"name": "", "value_tier": "high"},
        ]
    }
    projects = _normalize_projects(data)
    assert len(projects) == 1
    assert projects[0]["name"] == "A"
    assert projects[0]["role"] == "primary"
    assert projects[0]["value_tier"] == "high"
    assert projects[0]["difficulty_tier"] == "low"
    assert projects[0]["fixes"] == ["改1", "改2"]


def test_review_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("# 简历文本\n\n智能客服 RAG 项目经历。\n", encoding="utf-8")
    result = review_projects(
        md, root=tmp_path, projects_assessor=_fake_assessor
    )
    assert result.project_count == 1
    report = result.report_md.read_text(encoding="utf-8")
    assert "不替代" in report
    assert "不含薪资" in report or "薪资" in report
    assert "智能客服 RAG" in report
    assert "含金量" in report
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["tool"] == "review-projects"
    assert data["includes_salary"] is False
    assert data["judges_content"] is False
    assert data["not_in_triage_by_default"] is True
    assert data["not_in_resume_by_default"] is True
    assert result.report_md.name.endswith(".projects.md")


def test_review_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "LLM RAG Agent 微调")
    extract_resume_text(pdf, root=tmp_path)
    result = review_projects(
        pdf, root=tmp_path, projects_assessor=_fake_assessor
    )
    assert result.report_md.is_file()
    assert result.report_json.is_file()


def test_empty_body_fails(tmp_path: Path) -> None:
    md = tmp_path / "empty.resume.md"
    md.write_text("   \n", encoding="utf-8")
    with pytest.raises(ReviewProjectsError, match="为空"):
        review_projects(md, root=tmp_path, projects_assessor=_fake_assessor)


def test_cli_review_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "cli.resume.md"
    md.write_text("主项目：RAG\n", encoding="utf-8")
    monkeypatch.setattr(
        "employ_guard.cli.review_projects",
        lambda source, job_description=None: review_projects(
            source,
            job_description=job_description,
            root=tmp_path,
            projects_assessor=_fake_assessor,
        ),
    )
    result = runner.invoke(app, ["review-projects", str(md)])
    assert result.exit_code == 0
    assert "项目审阅" in result.stdout
    assert "含金量" in result.stdout
