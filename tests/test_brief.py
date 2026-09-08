"""教练摘要 brief：按源汇总未过 / 存疑（R26）。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.resume import (
    BRIEF_ACTION_LIMIT,
    ResumeRunResult,
    _collect_actions,
    collect_brief_inventory,
    rewrite_brief_from_artifacts,
    write_brief,
)

runner = CliRunner()


def test_collect_brief_inventory_rolls_up_writing(tmp_path: Path) -> None:
    stem = "demo"
    (tmp_path / f"{stem}.layout.json").write_text(
        json.dumps(
            {
                "layout_pass": False,
                "pass_line": [
                    {"id": "P2", "pass": False, "note": "页边距不足"},
                    {"id": "P4", "pass": False, "note": "文字墙"},
                ],
                "revision_tips": ["加大页边距"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.writing.json").write_text(
        json.dumps(
            {
                "writing_pass": False,
                "findings": [
                    {"id": "W1", "note": "错别字示例"},
                    {"id": "W1", "note": "又一处"},
                    {"id": "W4", "note": "技能熟练模板"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.projects.json").write_text(
        json.dumps(
            {
                "credibility_flags": [
                    {
                        "code": "P5",
                        "severity": "doubtful",
                        "note": "项目结束晚于任职",
                    }
                ],
                "projects": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.judge.json").write_text(
        json.dumps(
            {
                "content_pass": False,
                "overall_pass": False,
                "main_blockers": ["C1：首页未见岗位类表述"],
                "pass_line": [
                    {
                        "id": "C1",
                        "pass": False,
                        "doubtful": False,
                        "note": "首页未见岗位类表述",
                    },
                    {
                        "id": "C5",
                        "pass": True,
                        "doubtful": True,
                        "note": "多项目负责人注水",
                    },
                    {
                        "id": "C7",
                        "pass": True,
                        "doubtful": True,
                        "note": "时间检测汇总：P5 等",
                    },
                ],
                "time_summary": ["P5：项目结束晚于任职"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    inv = collect_brief_inventory(tmp_path, stem)
    assert len([x for x in inv.fails if x.startswith("文字表达")]) == 1
    assert "W1×2" in inv.fails[1] or any("W1×2" in x for x in inv.fails)
    assert any("排版未达标" in x and "P2" in x for x in inv.fails)
    assert any("内容 C1" in x for x in inv.fails)
    assert not any("基础信息" in x for x in inv.fails)
    assert any("内容 C5" in x for x in inv.doubts)
    assert not any(x.startswith("时间检测") for x in inv.doubts)
    assert not any("项目审阅 P5" in x for x in inv.doubts)

    actions = _collect_actions(tmp_path, stem)
    assert len(actions) <= BRIEF_ACTION_LIMIT
    assert actions


def test_write_brief_inventory_sections(tmp_path: Path) -> None:
    stem = "briefed"
    run_dir = tmp_path
    pdf = tmp_path / f"{stem}.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (run_dir / f"{stem}.layout.json").write_text(
        json.dumps({"layout_pass": True, "pass_line": []}) + "\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.layout.md").write_text("# layout\n", encoding="utf-8")
    (run_dir / f"{stem}.judge.json").write_text(
        json.dumps(
            {
                "content_pass": True,
                "overall_pass": False,
                "pass_line": [
                    {
                        "id": "C6",
                        "pass": True,
                        "doubtful": True,
                        "note": "指标存疑",
                    }
                ],
                "main_blockers": [],
                "time_summary": ["未发现规则层时间硬伤；若有项目时段存疑见上表。"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.judge.md").write_text("# judge\n", encoding="utf-8")
    (run_dir / f"{stem}.writing.json").write_text(
        json.dumps(
            {
                "writing_pass": False,
                "findings": [{"id": "W1", "note": "标点问题"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / f"{stem}.writing.md").write_text("# writing\n", encoding="utf-8")
    (run_dir / f"{stem}.profile.md").write_text("# profile\n", encoding="utf-8")

    result = ResumeRunResult(
        pdf_path=pdf,
        run_dir=run_dir,
        layout_pass=True,
        writing_pass=False,
        content_pass=True,
        exit_code=2,
        triage=False,
        actions=["文字表达：有待改进 1 条，见 writing 报告"],
    )
    path = write_brief(result)
    body = path.read_text(encoding="utf-8")
    assert "总览：未过合格线" in body
    assert "有待改进 1 条（W1×1）" in body
    assert "内容 C6" in body
    assert "按源汇总" in body


def test_rewrite_brief_from_output_dir(tmp_path: Path) -> None:
    stem = "alone"
    (tmp_path / f"{stem}.judge.json").write_text(
        json.dumps(
            {
                "content_pass": False,
                "pass_line": [
                    {"id": "C1", "pass": False, "doubtful": False, "note": "缺岗位"}
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.layout.json").write_text(
        json.dumps({"layout_pass": True, "pass_line": []}) + "\n",
        encoding="utf-8",
    )
    path = rewrite_brief_from_artifacts(tmp_path)
    assert path.name == f"{stem}.brief.md"
    body = path.read_text(encoding="utf-8")
    assert "内容 C1" in body
    assert "总览：未过合格线" in body


def test_cli_write_brief(tmp_path: Path) -> None:
    stem = "cli"
    (tmp_path / f"{stem}.judge.json").write_text(
        json.dumps(
            {
                "content_pass": True,
                "pass_line": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.layout.json").write_text(
        json.dumps({"layout_pass": True}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["write-brief", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "教练摘要" in result.output
    assert (tmp_path / f"{stem}.brief.md").is_file()
