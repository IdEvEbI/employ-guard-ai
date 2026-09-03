"""判内容能不能投。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.llm import LLMError
from datetime import date

from employ_guard.judge_resume import (
    JudgeResumeError,
    _clean_note,
    find_future_end_dates,
    _parse_json_object,
    default_content_assessor,
    judge_resume,
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


def _pass_assessor(_text: str, _job: str | None) -> dict:
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": False, "note": f"{f'C{i}'} 通过", "method": "llm"}
        for i in range(1, 10)
    ]
    pass_line[6]["doubtful"] = True
    pass_line[6]["note"] = "年龄与工龄互算略紧，存疑"
    levels = ["high", "mid", "low", "high", "mid", "high", "mid", "low"]
    level_line = [
        {"id": f"H{i}", "level": levels[i - 1], "note": f"H{i} 说明", "method": "llm"}
        for i in range(1, 9)
    ]
    return {
        "scope": "通用大模型应用 / 应用算法面初筛，非针对某一企业",
        "pass_line": pass_line,
        "level_line": level_line,
        "main_blockers": [],
    }


def _fail_assessor(_text: str, _job: str | None) -> dict:
    data = _pass_assessor(_text, _job)
    data["pass_line"][2] = {
        "id": "C3",
        "pass": False,
        "doubtful": False,
        "note": "缺 Agent 可追问证据",
        "method": "llm",
    }
    data["main_blockers"] = ["C3：缺 Agent 可追问证据"]
    return data


def test_parse_json_with_fence_and_prose() -> None:
    raw = '说明如下：\n```json\n{"scope": "test", "pass_line": [], "level_line": [], "main_blockers": []}\n```'
    data = _parse_json_object(raw)
    assert data["scope"] == "test"

    wrapped = '前缀\n{"scope": "x", "pass_line": [], "level_line": [], "main_blockers": []}\n后缀'
    assert _parse_json_object(wrapped)["scope"] == "x"


def test_parse_json_empty_raises() -> None:
    with pytest.raises(JudgeResumeError, match="为空"):
        _parse_json_object("   ")


def test_default_assessor_retries_without_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def _fake_chat_completion(**kwargs):  # type: ignore[no-untyped-def]
        json_object = bool(kwargs.get("json_object", False))
        calls.append(json_object)
        if json_object:
            raise LLMError("LLM 返回内容为空（finish_reason=stop）。")
        return json.dumps(
            {
                "scope": "test",
                "pass_line": [{"id": "C1", "pass": True, "doubtful": False, "note": "ok"}],
                "level_line": [{"id": "H1", "level": "high", "note": "ok"}],
                "main_blockers": [],
            }
        )

    monkeypatch.setattr("employ_guard.judge_resume.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("employ_guard.judge_resume.time.sleep", lambda _sec: None)

    result = default_content_assessor("简历正文", None)
    assert result["scope"] == "test"
    assert calls == [True, True, False]


def test_help_lists_judge_resume() -> None:
    result = runner.invoke(app, ["judge-resume", "--help"])
    assert result.exit_code == 0
    assert "排版" in result.stdout or "内容" in result.stdout


def test_requires_read_resume_first(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "bare.pdf"
    _write_pdf(pdf, "hello resume")
    with pytest.raises(JudgeResumeError, match="read-resume"):
        judge_resume(pdf, root=tmp_path, content_assessor=_pass_assessor)


def test_clean_note_strips_field_assignments() -> None:
    raw = "有量化结果，但耗时过短，面试难辩护，故doubtful=true"
    cleaned = _clean_note(raw)
    assert "doubtful" not in cleaned.lower()
    assert "耗时过短" in cleaned
    assert cleaned.endswith("。")


def test_refine_level_caps_before_style_resume() -> None:
    from employ_guard.judge_resume import refine_level_line

    text = (
        "求职意向：大模型算法工程师\n"
        "2025.08-2026.08 某公司 大模型后端研发工程师\n"
        "1.多 Agent 编排与状态管理：基于 LangGraph\n"
        "2.四维度并发审查：asyncio\n"
        "单份合同审查耗时 <3s\n"
    )
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": i == 6, "note": "x", "method": "llm"}
        for i in range(1, 10)
    ]
    level_line = [
        {"id": f"H{i}", "level": "high", "note": "模型给高", "method": "llm"}
        for i in range(1, 9)
    ]
    refined = refine_level_line(level_line, text, pass_line)
    by_id = {item["id"]: item for item in refined}
    assert by_id["H1"]["level"] == "mid"
    assert by_id["H4"]["level"] == "mid"
    assert by_id["H5"]["level"] == "mid"
    assert by_id["H8"]["level"] == "mid"
    assert by_id["H2"]["level"] == "high"


def test_refine_level_keeps_after_style_highs() -> None:
    from employ_guard.judge_resume import refine_level_line

    text = (
        "求职意向：AI应用开发工程师|大模型算法工程师\n"
        "2024.09---至今 某公司 大模型应用开发工程师\n"
        "律所律师三类使用入口上传合同\n"
        "发现问题 → 查找依据 → 生成方案\n"
        "主修课程：高等数学\n"
        "自我评价\n拥有三年经验\n"
        "单份合同审查耗时 40-60s\n"
    )
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": False, "note": "x", "method": "llm"}
        for i in range(1, 10)
    ]
    level_line = [
        {"id": f"H{i}", "level": "high", "note": "模型给高", "method": "llm"}
        for i in range(1, 9)
    ]
    refined = refine_level_line(level_line, text, pass_line)
    by_id = {item["id"]: item for item in refined}
    assert by_id["H1"]["level"] == "high"
    assert by_id["H5"]["level"] == "high"
    assert by_id["H8"]["level"] == "high"
    assert by_id["H6"]["level"] == "mid"


def test_refine_level_caps_encyclopedia_resume() -> None:
    from employ_guard.judge_resume import refine_level_line

    duties = "\n".join(f"{i}.负责实现某模块并完成部署与监控配置细节说明。" for i in range(1, 15))
    text = (
        "个人优势\n"
        "Java 微服务生态方面，熟悉 Spring Boot / Spring Cloud。\n"
        "前端开发方面，熟练掌握 HTML5、Vue.js。\n"
        "同时深耕大模型应用开发，使用 LangChain。\n"
        "相关技能\n"
        "项目经历\n"
        f"{duties}\n"
    )
    # pad length
    text = text + ("补充说明技术细节。" * 400)
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": False, "note": "x", "method": "llm"}
        for i in range(1, 10)
    ]
    level_line = [
        {"id": f"H{i}", "level": "high", "note": "模型给高", "method": "llm"}
        for i in range(1, 9)
    ]
    refined = refine_level_line(level_line, text, pass_line)
    by_id = {item["id"]: item for item in refined}
    assert by_id["H1"]["level"] == "mid"
    assert by_id["H5"]["level"] == "mid"
    assert by_id["H6"]["level"] == "mid"


def test_find_future_end_dates() -> None:
    text = (
        "2023.09-2025.07 甲公司\n"
        "2025.08-2027.06 乙公司\n"
        "2024.09---至今 丙公司\n"
        "项目 2025.12 - 2027.03"
    )
    hits = find_future_end_dates(text, today=date(2026, 9, 3))
    assert any("2027.06" in item for item in hits)
    assert any("2027.03" in item for item in hits)
    assert not any("至今" in item for item in hits)
    assert not any("2025.07" in item for item in hits)


def test_apply_future_date_doubts_marks_c7(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("2025.08-2027.06 大模型后端\n", encoding="utf-8")
    result = judge_resume(
        md,
        root=tmp_path,
        content_assessor=_pass_assessor,
        today=date(2026, 9, 3),
    )
    c7 = next(item for item in result.pass_line if item["id"] == "C7")
    assert c7["pass"] is True
    assert c7["doubtful"] is True
    assert "晚于当日" in c7["note"]
    assert any("C7" in item for item in result.doubtful_items)


def test_apply_credibility_doubts_tu_style(tmp_path: Path) -> None:
    from employ_guard.judge_resume import find_credibility_issues

    text = (
        "项目一：金融研报生成系统 项目负责人\n"
        "基于 Qwen3.5-4B 的 lora 微调生成研报\n"
        "项目二：智慧健康管家 项目负责人\n"
        "以LangGraph+LangChain实现状态管理\n"
        "项目三：金融智投 项目负责人\n"
        "·Milvus 1095 向量，BGE-M3 编码\n"
        "项目四：智评平台 项目负责人\n"
    )
    issues = find_credibility_issues(text)
    codes = {code for code, _, _ in issues}
    assert "C5" in codes
    assert "C6" in codes
    assert any("小参数" in note or "4B" in note for _, note, _ in issues)
    assert any("LangGraph" in note for _, note, _ in issues)
    assert any("向量" in note for _, note, _ in issues)

    md = tmp_path / "cred.resume.md"
    md.write_text(text, encoding="utf-8")
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    c5 = next(item for item in result.pass_line if item["id"] == "C5")
    c6 = next(item for item in result.pass_line if item["id"] == "C6")
    assert c5["doubtful"] is True
    assert c6["doubtful"] is True
    by_id = {item["id"]: item for item in result.level_line}
    assert by_id["H4"]["level"] == "mid"
    assert by_id["H5"]["level"] == "mid"


def test_judge_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "data" / "output" / "demo" / "demo.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 简历文本\n\n大模型工程师，RAG 与 Agent 项目经历。\n", encoding="utf-8")
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    assert result.content_pass is True
    assert result.doubtful_items
    report = result.report_md.read_text(encoding="utf-8")
    assert "内容合格" in report
    assert "| 水平 |" in report
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["judges_content"] is True
    assert data["evaluates_layout"] is False
    assert len(data["pass_line"]) == 9
    assert data["level_line"][0]["level"] == "high"


def test_judge_from_pdf_after_read_resume(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, "LLM RAG Agent 微调 项目经历")
    extract_resume_text(pdf, root=tmp_path)
    result = judge_resume(pdf, root=tmp_path, content_assessor=_pass_assessor)
    assert result.content_pass is True
    assert (tmp_path / "data" / "output" / "resumes" / "ok" / "ok.judge.md").is_file()


def test_fail_clears_level_line(tmp_path: Path) -> None:
    md = tmp_path / "demo.resume.md"
    md.write_text("短文本\n", encoding="utf-8")
    result = judge_resume(md, root=tmp_path, content_assessor=_fail_assessor)
    assert result.content_pass is False
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["level_line"] == []
    assert "未过合格线" in result.report_md.read_text(encoding="utf-8")


def test_cli_pass_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "data" / "output" / "demo" / "demo.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 简历文本\n\n正文\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return judge_resume(source, root=tmp_path, content_assessor=_pass_assessor)

    monkeypatch.setattr("employ_guard.cli.judge_resume", _fake)
    result = runner.invoke(app, ["judge-resume", str(md)])
    assert result.exit_code == 0, result.output
    assert "内容达标" in result.stdout
    assert "水平线" in result.stdout


def test_cli_fail_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    md = tmp_path / "demo.resume.md"
    md.write_text("x\n", encoding="utf-8")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return judge_resume(source, root=tmp_path, content_assessor=_fail_assessor)

    monkeypatch.setattr("employ_guard.cli.judge_resume", _fake)
    result = runner.invoke(app, ["judge-resume", str(md)])
    assert result.exit_code == 2, result.output
    assert "内容未达标" in result.output
    assert "C3" in result.output
