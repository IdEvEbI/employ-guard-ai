"""只凭页图查排版。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.check_layout import (
    CheckLayoutError,
    build_revision_tips,
    check_layout,
    evaluate_p1,
    FONT_HUMAN_REVIEW_TIP,
)
from employ_guard.cli import app
from employ_guard.layout_geometry import apply_geometry_fallback, scan_page_geometry
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
            {"code": "font_inconsistent", "found": False, "pages": [], "note": ""},
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


def test_revision_tips_always_include_font_human_review() -> None:
    from employ_guard.check_layout import FONT_HUMAN_REVIEW_TIP, build_revision_tips

    tips = build_revision_tips(
        page_count=3,
        pass_line=[{"id": "P1", "pass": True}],
        defects=[
            {"code": "font_inconsistent", "found": False, "pages": [], "note": ""},
        ],
    )
    assert FONT_HUMAN_REVIEW_TIP in tips

    tips_found = build_revision_tips(
        page_count=3,
        pass_line=[{"id": "P1", "pass": True}],
        defects=[
            {
                "code": "font_inconsistent",
                "found": True,
                "pages": [1, 2],
                "note": "第1页技能区字号大于第2页项目正文。",
            },
        ],
    )
    assert any("技能区字号" in tip for tip in tips_found)
    assert FONT_HUMAN_REVIEW_TIP not in tips_found


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
    assert result.revision_tips
    assert any("4 页" in tip for tip in result.revision_tips)
    assert any("人工再看" in tip for tip in result.revision_tips)
    report = result.report_md.read_text(encoding="utf-8")
    assert "未过合格线" in report
    assert "改稿要点" in report
    assert "人工再看" in report


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
    assert "人工再看" in result.stdout or "字体" in result.stdout
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


def _write_dense_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for y in range(36, 820, 9):
        page.insert_text((36, y), ("密排文字墙示例ABCDEFG" * 6), fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def test_geometry_flags_dense_wall_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "dense.pdf"
    _write_dense_pdf(pdf)
    document = pymupdf.open(pdf)
    pix = document[0].get_pixmap(dpi=100)
    page_png = tmp_path / "001.png"
    pix.save(page_png)
    document.close()
    scan = scan_page_geometry([page_png])
    assert scan.dense_pages == [1]
    assert scan.wall_pages == [1]


def test_geometry_fallback_marks_tight_and_p4_when_vision_misses() -> None:
    from employ_guard.layout_geometry import GeometryScan

    scan = GeometryScan(
        page_row_occupancy=[0.7],
        page_avg_gap=[0.2],
        dense_pages=[1],
        wall_pages=[1],
    )
    pass_line = [
        {"id": "P2", "pass": True, "note": "ok", "method": "vision"},
        {"id": "P3", "pass": True, "note": "ok", "method": "vision"},
        {"id": "P4", "pass": True, "note": "留白正常", "method": "vision"},
        {"id": "P5", "pass": True, "note": "ok", "method": "vision"},
    ]
    defects = [
        {"code": "leading_punct", "found": False, "pages": [], "note": ""},
        {"code": "bullet_inconsistent", "found": False, "pages": [], "note": ""},
        {"code": "alignment", "found": False, "pages": [], "note": ""},
        {"code": "tight_spacing", "found": False, "pages": [], "note": ""},
        {"code": "font_inconsistent", "found": False, "pages": [], "note": ""},
    ]
    new_pass, new_defects = apply_geometry_fallback(pass_line, defects, scan)
    p4 = next(item for item in new_pass if item["id"] == "P4")
    tight = next(item for item in new_defects if item["code"] == "tight_spacing")
    assert p4["pass"] is False
    assert p4["method"] == "rule"
    assert tight["found"] is True
    assert tight["method"] == "rule"


def test_check_layout_geometry_fallback_on_dense_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "wall.pdf"
    _write_dense_pdf(pdf)
    render_pdf_to_images(pdf, root=tmp_path, dpi=100)
    result = check_layout(pdf, root=tmp_path, visual_assessor=_pass_visual)
    assert result.layout_pass is False
    tight = next(item for item in result.defects if item["code"] == "tight_spacing")
    assert tight["found"] is True
    p4 = next(item for item in result.pass_line if item["id"] == "P4")
    assert p4["pass"] is False
    payload = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert "geometry" in payload
    assert payload["geometry"]["wall_pages"] == [1]
