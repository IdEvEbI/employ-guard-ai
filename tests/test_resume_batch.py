"""目录批跑：逐份 resume + 本地总表。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.resume import ResumeError
from employ_guard.resume_batch import list_resume_pdfs, run_resume_batch

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
        "note": "留白过少",
        "method": "vision",
    }
    data["revision_tips"] = ["加大页边距"]
    return data


def _pass_writing(_text: str) -> dict:
    return {"writing_pass": True, "findings": [], "level_line": []}


def _pass_content(_text: str, _job: str | None) -> dict:
    return {
        "content_pass": True,
        "pass_line": [{"id": "C1", "pass": True, "note": "ok"}],
        "level_line": [],
        "main_blockers": [],
        "credibility_flags": [],
    }


def _pass_questions(_text: str, _job: str | None = None) -> dict:
    return {
        "scope": "通用技术面，不是某家公司的真题",
        "projects": [
            {
                "name": "主项目",
                "why_selected": "练习",
                "basics": [
                    {
                        "id": "P1-B1",
                        "question": "请介绍项目？",
                        "focus": "角色",
                        "follow_ups": ["你负责哪段？"],
                    }
                ],
                "deep_dives": [
                    {
                        "id": "P1-D1",
                        "question": "失败时怎么降级？",
                        "focus": "边界",
                        "why": "简历写了降级。",
                    }
                ],
            }
        ],
    }


def _inject(**kwargs):  # type: ignore[no-untyped-def]
    base = {
        "visual_assessor": _pass_visual,
        "writing_assessor": _pass_writing,
        "content_assessor": _pass_content,
        "questions_assessor": _pass_questions,
    }
    base.update(kwargs)
    return base


def test_list_pdfs_skips_non_pdf_and_nested(tmp_path: Path) -> None:
    folder = tmp_path / "stack"
    _write_pdf(folder / "a.pdf")
    _write_pdf(folder / "b.pdf")
    (folder / "note.docx").write_text("x", encoding="utf-8")
    nested = folder / "sub"
    _write_pdf(nested / "c.pdf")
    names = [p.name for p in list_resume_pdfs(folder)]
    assert names == ["a.pdf", "b.pdf"]


def test_batch_empty_dir_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ResumeError, match="没有 PDF"):
        run_resume_batch(empty, root=tmp_path, **_inject())


def test_batch_writes_summary_exit_0(tmp_path: Path) -> None:
    folder = tmp_path / "data" / "input" / "class-a"
    _write_pdf(folder / "one.pdf", "Agent RAG")
    _write_pdf(folder / "two.pdf", "Agent RAG")
    result = run_resume_batch(folder, root=tmp_path, triage=True, **_inject())
    assert result.exit_code == 0
    assert len(result.rows) == 2
    assert result.summary_md is not None and result.summary_md.is_file()
    assert result.summary_json is not None and result.summary_json.is_file()
    body = result.summary_md.read_text(encoding="utf-8")
    assert "one.pdf" in body
    assert "two.pdf" in body
    assert "不上门户" in body
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 0
    assert len(payload["rows"]) == 2


def test_batch_aggregate_exit_2_when_layout_fails(tmp_path: Path) -> None:
    folder = tmp_path / "data" / "input" / "weak-batch"
    _write_pdf(folder / "ok.pdf")
    _write_pdf(folder / "weak.pdf")

    def _visual(pages: list[Path]) -> dict:
        # 页图在 …/<stem>/pages/；用 stem 判断，避免临时路径含测试名误伤
        stem = pages[0].parent.parent.name if pages else ""
        if stem == "weak":
            return _fail_visual(pages)
        return _pass_visual(pages)

    result = run_resume_batch(
        folder,
        root=tmp_path,
        triage=True,
        **_inject(visual_assessor=_visual),
    )
    assert result.exit_code == 2
    by_name = {row.pdf_path.name: row for row in result.rows}
    assert by_name["ok.pdf"].exit_code == 0
    assert by_name["weak.pdf"].exit_code == 2


def test_cli_resume_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    folder = tmp_path / "data" / "input" / "cli-batch"
    _write_pdf(folder / "a.pdf")
    _write_pdf(folder / "b.pdf")

    def _fake(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return run_resume_batch(source, root=tmp_path, **_inject(**kwargs))

    monkeypatch.setattr("employ_guard.cli.run_resume_batch", _fake)
    result = runner.invoke(app, ["resume", str(folder), "--triage"])
    assert result.exit_code == 0, result.output
    assert "本地总表" in result.stdout
    assert "批跑份数：2" in result.stdout
