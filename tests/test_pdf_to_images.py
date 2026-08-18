"""PDF 按页出图。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.pdf_to_images import PdfToImagesError, render_pdf_to_images

runner = CliRunner()


def _write_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), label, fontsize=24)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def test_help_lists_pdf_to_images() -> None:
    result = runner.invoke(app, ["pdf-to-images", "--help"])
    assert result.exit_code == 0
    assert "不评价排版" in result.stdout


def test_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "resume.docx"
    fake.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(PdfToImagesError, match="不是 PDF"):
        render_pdf_to_images(fake, root=tmp_path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.pdf"
    with pytest.raises(PdfToImagesError, match="找不到"):
        render_pdf_to_images(missing, root=tmp_path)


def test_renders_pages_in_order(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "sample.pdf"
    _write_pdf(pdf, ["PAGE-A", "PAGE-B"])
    pages_dir = render_pdf_to_images(pdf, root=tmp_path)
    names = sorted(path.name for path in pages_dir.glob("*.png"))
    assert names == ["001.png", "002.png"]
    assert (tmp_path / "data" / "output" / "resumes" / "sample" / "pdf-to-images.json").is_file()
    assert (pages_dir / "001.png").stat().st_size > 0
    assert (pages_dir / "002.png").stat().st_size > 0


def test_cli_writes_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, ["ONLY"])
    result = runner.invoke(app, ["pdf-to-images", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "已按页写出 1 张图片" in result.stdout
    assert "不评价排版" in result.stdout
    assert (tmp_path / "data" / "output" / "demo" / "pages" / "001.png").is_file()


def test_cli_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "note.txt"
    fake.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["pdf-to-images", str(fake)])
    assert result.exit_code == 1
    assert "不是 PDF" in result.output
