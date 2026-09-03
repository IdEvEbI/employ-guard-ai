"""老师命令 resume：按顺序调用并跳过已有文件。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.resume import ResumeError, run_resume

runner = CliRunner()


def _write_pdf(path: Path, text: str = "Resume body") -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _pass_visual(_pages: list[Path]) -> dict:
    return {
        "pass_line": [
            {"id": "P2", "pass": True, "note": "无溢出", "method": "vision"},
            {"id": "P3", "pass": True, "note": "可扫读", "method": "vision"},
            {"id": "P4", "pass": True, "note": "留白正常", "method": "vision"},
            {"id": "P5", "pass": True, "note": "无水印", "method": "vision"},
        ],
        "level_line": [],
        "defects": [
            {"code": "leading_punct", "found": False, "pages": [], "note": ""},
            {"code": "bullet_inconsistent", "found": False, "pages": [], "note": ""},
            {"code": "alignment", "found": False, "pages": [], "note": ""},
            {"code": "tight_spacing", "found": False, "pages": [], "note": ""},
            {"code": "font_inconsistent", "found": False, "pages": [], "note": ""},
        ],
    }


def _fail_visual(_pages: list[Path]) -> dict:
    data = _pass_visual(_pages)
    data["pass_line"][2] = {
        "id": "P4",
        "pass": False,
        "note": "文字墙",
        "method": "vision",
    }
    return data


def _pass_writing(_text: str) -> dict:
    return {"llm_findings": []}


def _pass_content(_text: str, _job: str | None) -> dict:
    pass_line = [
        {"id": f"C{i}", "pass": True, "doubtful": False, "note": "ok", "method": "llm"}
        for i in range(1, 10)
    ]
    return {
        "scope": "测试范围",
        "pass_line": pass_line,
        "level_line": [],
        "main_blockers": [],
    }


def _fail_content(_text: str, _job: str | None) -> dict:
    data = _pass_content(_text, _job)
    data["pass_line"][2] = {
        "id": "C3",
        "pass": False,
        "doubtful": False,
        "note": "缺证据",
        "method": "llm",
    }
    data["main_blockers"] = ["C3：缺证据"]
    return data


def _questions(_text: str, _job: str | None) -> dict:
    return {
        "scope": "通用技术面，不是某家公司的真题",
        "questions": [
            {
                "id": "Q1",
                "category": "项目深挖",
                "question": "主项目怎么做检索？",
                "why": "简历写了检索。",
                "focus": "讲清召回。",
            }
        ],
    }


def _inject(**kwargs):  # type: ignore[no-untyped-def]
    return {
        "visual_assessor": _pass_visual,
        "writing_assessor": _pass_writing,
        "content_assessor": _pass_content,
        "questions_assessor": _questions,
        **kwargs,
    }


def test_help_lists_resume() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "resume" in result.stdout
    detail = runner.invoke(app, ["resume", "--help"])
    assert detail.exit_code == 0
    assert "跳过" in detail.stdout or "顺序" in detail.stdout


def test_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "a.docx"
    fake.write_text("x", encoding="utf-8")
    with pytest.raises(ResumeError, match="不是 PDF"):
        run_resume(fake, root=tmp_path, **_inject())


def test_full_pass_exit_0(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, "Agent RAG project")
    result = run_resume(pdf, root=tmp_path, **_inject())
    assert result.exit_code == 0
    assert result.layout_pass is True
    assert result.content_pass is True
    assert result.questions_count == 1
    assert all(s.status == "ran" for s in result.steps if s.name != "draft-questions")
    assert result.steps[-1].name == "draft-questions"
    assert result.steps[-1].status == "ran"
    assert (result.run_dir / "pages").is_dir()
    assert (result.run_dir / "demo.layout.json").is_file()
    assert (result.run_dir / "demo.resume.md").is_file()
    assert (result.run_dir / "demo.writing.json").is_file()
    assert (result.run_dir / "demo.judge.json").is_file()
    assert (result.run_dir / "demo.questions.json").is_file()


def test_layout_fail_still_finishes_exit_2(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "weak.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject(visual_assessor=_fail_visual))
    assert result.exit_code == 2
    assert result.layout_pass is False
    assert result.content_pass is True
    assert result.questions_count == 1
    assert (result.run_dir / "weak.questions.json").is_file()


def test_content_fail_exit_2(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "content.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, **_inject(content_assessor=_fail_content))
    assert result.exit_code == 2
    assert result.layout_pass is True
    assert result.content_pass is False
    assert result.questions_count == 1


def test_skip_existing_artifacts(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "again.pdf"
    _write_pdf(pdf)
    first = run_resume(pdf, root=tmp_path, **_inject())
    assert first.exit_code == 0
    assert all(s.status == "ran" for s in first.steps)

    second = run_resume(pdf, root=tmp_path, **_inject())
    assert second.exit_code == 0
    assert all(s.status == "skipped" for s in second.steps)
    assert second.layout_pass is True
    assert second.content_pass is True
    assert second.questions_count == 1


def test_no_questions_flag(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "nq.pdf"
    _write_pdf(pdf)
    result = run_resume(pdf, root=tmp_path, skip_questions=True, **_inject())
    assert result.exit_code == 0
    assert result.questions_count is None
    q_step = next(s for s in result.steps if s.name == "draft-questions")
    assert q_step.status == "disabled"
    assert not (result.run_dir / "nq.questions.json").is_file()


def test_cli_resume_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "cli.pdf"
    _write_pdf(pdf)

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return run_resume(source, root=tmp_path, **_inject(**kwargs))

    monkeypatch.setattr("employ_guard.cli.run_resume", _fake)
    result = runner.invoke(app, ["resume", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "排版：达标" in result.stdout
    assert "内容：达标" in result.stdout
    assert "练习题" in result.stdout


def test_cli_resume_layout_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "cli-fail.pdf"
    _write_pdf(pdf)

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return run_resume(
            source,
            root=tmp_path,
            **_inject(visual_assessor=_fail_visual, **kwargs),
        )

    monkeypatch.setattr("employ_guard.cli.run_resume", _fake)
    result = runner.invoke(app, ["resume", str(pdf)])
    assert result.exit_code == 2, result.output
    assert "排版：未达标" in result.output
    assert "未过合格线" in result.output


def test_cli_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "x.docx"
    fake.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["resume", str(fake)])
    assert result.exit_code == 1
    assert "不是 PDF" in result.output
    assert "不能投" not in result.output
