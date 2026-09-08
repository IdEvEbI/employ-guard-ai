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
    career_window_start,
    duration_months,
    evaluate_credibility,
    evaluate_g1t,
    gap_months,
    intervals_overlap,
    parse_project_interval,
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
                "structure_gaps": [],
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
    assert data["in_resume_full_mode"] is True
    assert data["g1t_doubtful"] is False
    assert result.report_md.name.endswith(".projects.md")
    assert "G1-T" in report


def test_evaluate_g1t_near_shallow_far_deep() -> None:
    doubtful, note = evaluate_g1t(
        [
            {"name": "近", "value_tier": "low"},
            {"name": "远", "value_tier": "high"},
        ]
    )
    assert doubtful is True
    assert "近浅远深" in note


def test_evaluate_g1t_ok_when_near_not_lower() -> None:
    doubtful, _note = evaluate_g1t(
        [
            {"name": "近", "value_tier": "high"},
            {"name": "远", "value_tier": "mid"},
        ]
    )
    assert doubtful is False


def test_parse_duration_and_overlap() -> None:
    interval = parse_project_interval(time_range="2025.04-2025.05")
    assert interval is not None
    assert duration_months(interval) == 1
    a = parse_project_interval(time_range="2024.01-2024.06")
    b = parse_project_interval(time_range="2024.05-2024.08")
    c = parse_project_interval(time_range="2024.07-2024.12")
    assert a and b and c
    assert intervals_overlap(a, b) is True
    assert intervals_overlap(a, c) is False


def test_evaluate_credibility_p2_p4_p5() -> None:
    short = {
        "name": "短项目",
        "category": "work",
        "claims_lead": True,
        "name_too_generic": False,
        "employer": "甲公司",
        "_interval": ((2025, 4), (2025, 5)),
    }
    p_a = {
        "name": "A",
        "category": "work",
        "claims_lead": True,
        "name_too_generic": False,
        "employer": "甲公司",
        "_interval": ((2024, 1), (2024, 8)),
    }
    p_b = {
        "name": "B",
        "category": "work",
        "claims_lead": True,
        "name_too_generic": False,
        "employer": "甲公司",
        "_interval": ((2024, 6), (2024, 12)),
    }
    p_c = {
        "name": "C",
        "category": "work",
        "claims_lead": True,
        "name_too_generic": False,
        "employer": "甲公司",
        "_interval": ((2024, 10), (2025, 3)),
    }
    flags = evaluate_credibility(
        [short, p_a, p_b, p_c],
        {"work_years": 1.5, "target_role": "", "work_spans": [], "education_spans": []},
    )
    codes = {f.code for f in flags}
    assert "P2" in codes
    assert "P4" in codes
    assert "P5" in codes


def test_evaluate_credibility_p1_generic_name() -> None:
    projects = _normalize_projects(
        {
            "projects": [
                {
                    "name": "健康管家",
                    "value_tier": "mid",
                    "difficulty_tier": "mid",
                    "name_too_generic": True,
                }
            ]
        }
    )
    flags = evaluate_credibility(
        projects,
        {"work_years": None, "target_role": "", "work_spans": [], "education_spans": []},
    )
    assert any(f.code == "P1" for f in flags)


def test_evaluate_credibility_p8_gap_after_graduation() -> None:
    edu = [
        {
            "label": "本科",
            "start_ym": "2018-09",
            "end_ym": "2022-06",
            "_interval": ((2018, 9), (2022, 6)),
        }
    ]
    work = [
        {
            "label": "甲公司",
            "start_ym": "2022-07",
            "end_ym": "2025-06",
            "_interval": ((2022, 7), (2025, 6)),
        }
    ]
    assert career_window_start(work, edu) == (2022, 6)
    assert gap_months((2023, 3), (2023, 6)) == 3
    projects = [
        {
            "name": "课设",
            "category": "course",
            "claims_lead": False,
            "name_too_generic": False,
            "_interval": ((2021, 1), (2021, 6)),
        },
        {
            "name": "项目甲",
            "category": "work",
            "claims_lead": False,
            "name_too_generic": False,
            "_interval": ((2022, 7), (2023, 3)),
        },
        {
            "name": "项目乙",
            "category": "work",
            "claims_lead": False,
            "name_too_generic": False,
            "_interval": ((2023, 6), (2024, 1)),
        },
    ]
    flags = evaluate_credibility(
        projects,
        {
            "work_years": 2,
            "target_role": "",
            "work_spans": work,
            "education_spans": edu,
        },
    )
    assert any(f.code == "P8" for f in flags)
    # 在校期相邻空窗不因 P8 触发（课设不入轴）
    school_only = evaluate_credibility(
        [
            {
                "name": "课设A",
                "category": "course",
                "claims_lead": False,
                "name_too_generic": False,
                "_interval": ((2020, 1), (2020, 3)),
            },
            {
                "name": "课设B",
                "category": "course",
                "claims_lead": False,
                "name_too_generic": False,
                "_interval": ((2021, 1), (2021, 6)),
            },
        ],
        {
            "work_years": None,
            "target_role": "",
            "work_spans": work,
            "education_spans": edu,
        },
    )
    assert not any(f.code == "P8" for f in school_only)


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
