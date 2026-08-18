"""从 PDF 抽出文本。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.read_resume import ReadResumeError, extract_resume_text

runner = CliRunner()


def _write_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), label, fontsize=24)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def test_help_lists_read_resume() -> None:
    result = runner.invoke(app, ["read-resume", "--help"])
    assert result.exit_code == 0
    assert "不判断能不能投" in result.stdout


def test_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "resume.docx"
    fake.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(ReadResumeError, match="不是 PDF"):
        extract_resume_text(fake, root=tmp_path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.pdf"
    with pytest.raises(ReadResumeError, match="找不到"):
        extract_resume_text(missing, root=tmp_path)


def test_extracts_text_by_page(tmp_path: Path) -> None:
    pdf = tmp_path / "data" / "input" / "resumes" / "sample.pdf"
    _write_pdf(pdf, ["PAGE-A", "PAGE-B"])
    run_dir = extract_resume_text(pdf, root=tmp_path)
    md = (run_dir / "sample.resume.md").read_text(encoding="utf-8")
    assert "PAGE-A" in md
    assert "PAGE-B" in md
    assert "不判断能不能投" in md
    payload = (run_dir / "sample.resume.json").read_text(encoding="utf-8")
    assert '"judges_content": false' in payload
    assert '"evaluates_layout": false' in payload


def test_cli_writes_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    pdf = tmp_path / "data" / "input" / "demo.pdf"
    _write_pdf(pdf, ["HELLO-RESUME"])
    result = runner.invoke(app, ["read-resume", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "已抽出文本" in result.stdout
    assert "不判断能不能投" in result.stdout
    assert (tmp_path / "data" / "output" / "demo" / "demo.resume.md").is_file()
    assert "HELLO-RESUME" in (tmp_path / "data" / "output" / "demo" / "demo.resume.md").read_text(
        encoding="utf-8"
    )


def test_cli_rejects_non_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "note.txt"
    fake.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["read-resume", str(fake)])
    assert result.exit_code == 1
    assert "不是 PDF" in result.output
