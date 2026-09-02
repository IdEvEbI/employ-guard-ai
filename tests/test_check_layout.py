"""只凭页图查排版。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.check_layout import (
    CheckLayoutError,
    check_layout,
    evaluate_p1,
)
from employ_guard.cli import app
from employ_guard.pdf_to_images import render_pdf_to_images

runner = CliRunner()


def _write_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), label, fontsize=24)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def _pass_visual(_pages: list[Path]) -> dict:
    return {
        "pass_line": [
            {"id": "P2", "pass": True, "note": "无溢出", "method": "vision"},
            {"id": "P3", "pass": True, "note": "可扫读", "method": "vision"},
            {"id": "P4", "pass": True, "note": "留白正常", "method": "vision"},
            {"id": "P5", "pass": True, "note": "无校区/试用类贴图水印（本项通过）", "method": "vision"},
        ],
        "level_line": [
            {"id": "Q1", "signal": True, "note": "项目块左缘对齐", "method": "vision"},
            {"id": "Q2", "signal": False, "note": "列表符号偶有混用", "method": "vision"},
            {"id": "Q3", "signal": True, "note": "联系方式与正文分区清楚", "method": "vision"},
        ],
        "defects": [
            {"code": "leading_punct", "found": False, "pages": [], "note": ""},
            {"code": "bullet_inconsistent", "found": True, "pages": [1], "note": "圆点与方块混用"},
            {"code": "alignment", "found": False, "pages": [], "note": ""},
            {"code": "tight_spacing", "found": False, "pages": [], "note": ""},
        ],
    }


def _fail_visual(_pages: list[Path]) -> dict:
    data = _pass_visual(_pages)
    data["pass_line"][2] = {
        "id": "P4",
        "pass": False,
        "note": "第 1 页段前段后过密，文字墙",
        "method": "vision",
    }
    data["defects"][3] = {
        "code": "tight_spacing",
        "found": True,
        "pages": [1],
        "note": "模块间距几乎为零",
    }
    return data


def test_help_lists_check_layout() -> None:
    result = runner.invoke(app, ["check-layout", "--help"])
    assert result.exit_code == 0
    assert "页图" in result.stdout


def test_evaluate_p1_page_limit() -> None:
    assert evaluate_p1(4)["pass"] is True
    assert evaluate_p1(5)["pass"] is False


def test_requires_pages_first(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "alone.pdf"
    _write_pdf(pdf, ["ONLY"])
    with pytest.raises(CheckLayoutError, match="pdf-to-images"):
        check_layout(pdf, root=tmp_path, visual_assessor=_pass_visual)


def test_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "resume.docx"
    fake.write_text("x", encoding="utf-8")
    with pytest.raises(CheckLayoutError, match="不是 PDF"):
        check_layout(fake, root=tmp_path, visual_assessor=_pass_visual)


def test_writes_layout_when_pages_exist(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "ok.pdf"
    _write_pdf(pdf, ["A", "B"])
    render_pdf_to_images(pdf, root=tmp_path)
    result = check_layout(pdf, root=tmp_path, visual_assessor=_pass_visual)
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert result.layout_pass is True
    assert "排版合格" in result.report_md.read_text(encoding="utf-8")
    assert "列表符号不一致" in result.report_md.read_text(encoding="utf-8")
    assert data["judges_content"] is False
    assert data["page_count"] == 2
    assert data["pass_line"][0]["id"] == "P1"
    assert data["level_line"]
    assert data["defects"][1]["found"] is True


def test_fails_p1_when_too_many_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "long.pdf"
    _write_pdf(pdf, [f"P{i}" for i in range(5)])
    render_pdf_to_images(pdf, root=tmp_path)
    result = check_layout(pdf, root=tmp_path, visual_assessor=_pass_visual)
    data = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert result.layout_pass is False
    assert data["pass_line"][0]["pass"] is False
    assert data["level_line"] == []
    assert "未过合格线" in result.report_md.read_text(encoding="utf-8")


def test_cli_writes_report_and_pass_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, ["ONLY"])
    assert runner.invoke(app, ["pdf-to-images", str(pdf)]).exit_code == 0

    def _fake_check(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return check_layout(source, root=tmp_path, visual_assessor=_pass_visual)

    monkeypatch.setattr("employ_guard.cli.check_layout", _fake_check)
    result = runner.invoke(app, ["check-layout", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "排版报告" in result.stdout
    assert "排版达标" in result.stdout
    assert "不判断内容" in result.stdout
    assert (tmp_path / "data" / "output" / "demo" / "demo.layout.md").is_file()


def test_cli_fail_verdict_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, ["ONLY"])
    assert runner.invoke(app, ["pdf-to-images", str(pdf)]).exit_code == 0

    def _fake_check(source: Path, **kwargs):  # type: ignore[no-untyped-def]
        return check_layout(source, root=tmp_path, visual_assessor=_fail_visual)

    monkeypatch.setattr("employ_guard.cli.check_layout", _fake_check)
    result = runner.invoke(app, ["check-layout", str(pdf)])
    assert result.exit_code == 2, result.output
    assert "排版未达标" in result.output
    assert "P4" in result.output


def test_cli_rejects_without_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, ["ONLY"])
    result = runner.invoke(app, ["check-layout", str(pdf)])
    assert result.exit_code == 1
    assert "pdf-to-images" in result.output
