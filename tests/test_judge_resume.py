"""判内容能不能投。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.llm import LLMError
from employ_guard.judge_resume import (
    JudgeResumeError,
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
    level_line = [
        {"id": f"H{i}", "signal": i % 2 == 1, "note": f"H{i} 信号", "method": "llm"}
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
                "level_line": [{"id": "H1", "signal": True, "note": "ok"}],
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


def test_judge_from_resume_md(tmp_path: Path) -> None:
    md = tmp_path / "data" / "output" / "demo" / "demo.resume.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 简历文本\n\n大模型工程师，RAG 与 Agent 项目经历。\n", encoding="utf-8")
    result = judge_resume(md, root=tmp_path, content_assessor=_pass_assessor)
    assert result.content_pass is True
    assert result.doubtful_items
    assert "内容合格" in result.report_md.read_text(encoding="utf-8")
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert data["judges_content"] is True
    assert data["evaluates_layout"] is False
    assert len(data["pass_line"]) == 9


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
