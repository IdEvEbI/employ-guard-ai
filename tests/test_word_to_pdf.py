"""Word 转 PDF。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from employ_guard.cli import app
from employ_guard.word_to_pdf import WordToPdfError, convert_word_to_pdf

runner = CliRunner()

_MINIMAL_DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>R13 fixture</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


def _write_minimal_docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        archive.writestr("word/document.xml", _MINIMAL_DOCUMENT_XML)


def test_help_lists_word_to_pdf() -> None:
    result = runner.invoke(app, ["word-to-pdf", "--help"])
    assert result.exit_code == 0
    assert "不评价" in result.stdout


def test_rejects_non_word(tmp_path: Path) -> None:
    fake = tmp_path / "resume.pdf"
    fake.write_text("%PDF", encoding="utf-8")
    with pytest.raises(WordToPdfError, match="不是 Word"):
        convert_word_to_pdf(fake, root=tmp_path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.docx"
    with pytest.raises(WordToPdfError, match="找不到"):
        convert_word_to_pdf(missing, root=tmp_path)


def test_rejects_when_no_converter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = tmp_path / "data" / "input" / "sample.docx"
    _write_minimal_docx(docx)
    monkeypatch.setattr("employ_guard.word_to_pdf.find_soffice", lambda: None)
    monkeypatch.setattr("employ_guard.word_to_pdf.msword_available", lambda: False)
    with pytest.raises(WordToPdfError, match="未找到转换器"):
        convert_word_to_pdf(docx, root=tmp_path)


def test_convert_writes_pdf_and_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = tmp_path / "data" / "input" / "resumes" / "word" / "demo.docx"
    _write_minimal_docx(docx)
    target_pdf = docx.with_suffix(".pdf")

    def _fake_lo(src: Path, pdf: Path, soffice: Path) -> None:  # noqa: ARG001
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake for test\n")

    monkeypatch.setattr(
        "employ_guard.word_to_pdf.find_soffice",
        lambda: Path("/usr/bin/soffice"),
    )
    monkeypatch.setattr(
        "employ_guard.word_to_pdf._convert_via_libreoffice",
        _fake_lo,
    )

    result = convert_word_to_pdf(docx, root=tmp_path)
    assert result == target_pdf
    assert target_pdf.is_file()
    record_path = (
        tmp_path / "data" / "output" / "resumes" / "word" / "demo" / "word-to-pdf.json"
    )
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["tool"] == "word-to-pdf"
    assert record["evaluates_content"] is False
    assert record["converter"] == "libreoffice"
    assert record["output"] == str(target_pdf)


def test_cli_writes_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    docx = tmp_path / "data" / "input" / "demo.docx"
    _write_minimal_docx(docx)

    def _fake_convert(source: Path, *, out_dir=None, root=None):  # noqa: ANN001
        pdf = (out_dir or source.parent) / f"{source.stem}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        return pdf.resolve()

    monkeypatch.setattr("employ_guard.cli.convert_word_to_pdf", _fake_convert)
    result = runner.invoke(app, ["word-to-pdf", str(docx)])
    assert result.exit_code == 0, result.output
    assert "已写出 PDF" in result.stdout
    assert "不评价" in result.stdout


def test_cli_rejects_non_word(tmp_path: Path) -> None:
    fake = tmp_path / "note.txt"
    fake.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["word-to-pdf", str(fake)])
    assert result.exit_code == 1
    assert "不是 Word" in result.output
