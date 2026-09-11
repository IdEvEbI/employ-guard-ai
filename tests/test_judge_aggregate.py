"""judge 汇总前置分项（R25）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from employ_guard.judge_aggregate import (
    apply_profile_c1,
    apply_skills_projects_c3_c8,
    build_time_summary,
    overlay_pass_line,
)
from employ_guard.judge_resume import judge_resume


def _pass_assessor(_text: str, _job: str | None) -> dict:
    pass_line = [
        {
            "id": f"C{i}",
            "pass": True,
            "doubtful": False,
            "note": f"C{i} 通过",
            "method": "llm",
        }
        for i in range(1, 10)
    ]
    # 模拟 LLM 误把 C1 判过，但正文无首页岗位；规则层本会打回
    return {
        "scope": "测试范围",
        "pass_line": pass_line,
        "level_line": [
            {"id": f"H{i}", "level": "mid", "note": "n", "method": "llm"}
            for i in range(1, 9)
        ],
        "main_blockers": [],
    }


def test_profile_wins_c1_over_homepage_rule(tmp_path: Path) -> None:
    """无首页岗位类表述，但 profile 过 → C1 过，不因规则层打回。"""
    run_dir = tmp_path / "data" / "output" / "resumes" / "agg"
    run_dir.mkdir(parents=True)
    stem = "agg"
    md = run_dir / f"{stem}.resume.md"
    md.write_text(
        "# 简历文本\n\n姓名：测\n电话：13800000000\n\n项目：RAG Agent\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.profile.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "target_role": "算法工程师",
                "homepage_evidence": "工作经历岗位：算法工程师",
                "notes": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    c1 = next(item for item in result.pass_line if item["id"] == "C1")
    assert c1["pass"] is True
    assert "check-profile" in c1["note"]
    assert result.content_pass is True
    assert result.overall_pass is True
    assert f"{stem}.profile.json" in result.upstream_sources
    report = result.report_md.read_text(encoding="utf-8")
    assert "时间检测汇总" in report
    assert "排版与文字表达" in report


def test_skills_fail_maps_to_c8(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "output" / "resumes" / "sk"
    run_dir.mkdir(parents=True)
    stem = "sk"
    md = run_dir / f"{stem}.resume.md"
    md.write_text(
        "# 简历文本\n\n求职意向：大模型工程师\n技能堆砌无项目\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.skills.json").write_text(
        json.dumps(
            {
                "status": "fail",
                "project_coverage": {"status": "fail", "note": "大段技能无支撑"},
                "market_alignment": {"status": "pass", "note": "可见"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    c8 = next(item for item in result.pass_line if item["id"] == "C8")
    assert c8["pass"] is False
    assert "check-skills" in c8["note"]
    assert result.content_pass is False
    assert result.overall_pass is False


def test_layout_writing_in_report_and_overall(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "output" / "resumes" / "lw"
    run_dir.mkdir(parents=True)
    stem = "lw"
    md = run_dir / f"{stem}.resume.md"
    md.write_text(
        "# 简历文本\n\n求职意向：大模型工程师\nRAG Agent\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.layout.json").write_text(
        json.dumps({"layout_pass": True}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.writing.json").write_text(
        json.dumps(
            {
                "writing_pass": False,
                "findings": [{"id": "W1", "note": "错别字示例"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    assert result.content_pass is True
    assert result.layout_pass is True
    assert result.writing_pass is False
    assert result.overall_pass is False
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["overall_pass"] is False
    assert data["writing_pass"] is False
    assert "有待改进" in result.report_md.read_text(encoding="utf-8")


def test_time_summary_from_projects_and_future_end() -> None:
    from employ_guard.judge_aggregate import UpstreamBundle

    text = "工作经历 2024.01-2027.06 某司\n项目 2023.01-2023.12"
    projects = {
        "credibility_flags": [
            {
                "code": "P5",
                "severity": "doubtful",
                "note": "项目结束晚于任职结束",
            }
        ]
    }
    lines, rows = build_time_summary(
        resume_text=text,
        projects=projects,
        today=date(2026, 9, 8),
    )
    assert any("2027.06" in line or "晚于" in line for line in lines)
    assert any(r.get("code") == "P5" for r in rows)

    bundle = UpstreamBundle(projects=projects)
    updated, time_lines, _ = overlay_pass_line(
        [
            {
                "id": "C7",
                "pass": True,
                "doubtful": False,
                "note": "ok",
                "method": "llm",
            }
        ],
        bundle,
        text,
        today=date(2026, 9, 8),
    )
    c7 = next(item for item in updated if item["id"] == "C7")
    assert c7["doubtful"] is True
    assert time_lines


def test_apply_profile_and_skills_helpers() -> None:
    pass_line = [
        {"id": "C1", "pass": False, "doubtful": False, "note": "旧", "method": "llm"},
        {"id": "C8", "pass": True, "doubtful": False, "note": "旧", "method": "llm"},
    ]
    updated = apply_profile_c1(
        pass_line,
        {
            "status": "pass",
            "target_role": "算法工程师",
            "homepage_evidence": "工作岗位",
            "notes": [],
        },
    )
    assert next(i for i in updated if i["id"] == "C1")["pass"] is True
    updated = apply_skills_projects_c3_c8(
        updated,
        skills={
            "status": "doubtful",
            "project_coverage": {"status": "doubtful", "note": "部分无支撑"},
            "market_alignment": {"status": "pass", "note": "ok"},
        },
        projects=None,
    )
    c8 = next(i for i in updated if i["id"] == "C8")
    assert c8["pass"] is True
    assert c8["doubtful"] is True


def test_degraded_projects_c3_is_doubtful_not_fail() -> None:
    pass_line = [
        {
            "id": "C3",
            "pass": True,
            "doubtful": False,
            "note": "原注",
            "method": "llm",
        }
    ]
    updated = apply_skills_projects_c3_c8(
        pass_line,
        skills=None,
        projects={"llm_degraded": True, "projects": []},
    )
    c3 = next(item for item in updated if item["id"] == "C3")
    assert c3["pass"] is True
    assert c3["doubtful"] is True
    assert "降级" in c3["note"]
    assert "不能投" in c3["note"]
