"""把 Word 原稿转成投递用 PDF。本步不评价排版或内容。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from employ_guard.paths import output_run_dir, resolve_input_file

RECORD_NAME = "word-to-pdf.json"
ALLOWED_SUFFIXES = {".docx", ".doc"}
CONVERT_TIMEOUT_SEC = 180


class WordToPdfError(Exception):
    """输入不是可用 Word，或转换失败。失败不得写成内容不能投。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_applescript_string(path: Path) -> str:
    text = str(path.resolve())
    return text.replace("\\", "\\\\").replace('"', '\\"')


def find_soffice() -> Path | None:
    """查找 LibreOffice 可执行文件。"""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac.is_file():
        return mac
    return None


def msword_available() -> bool:
    """本机是否装有 Microsoft Word（macOS）。"""
    return Path("/Applications/Microsoft Word.app").is_dir()


def describe_converter() -> str:
    """给人读的转换器状态（供 check / 报错）。"""
    soffice = find_soffice()
    if soffice is not None:
        return f"LibreOffice：{soffice}"
    if msword_available():
        return "Microsoft Word（macOS AppleScript）"
    return (
        "未找到转换器。请安装 LibreOffice（推荐：brew install --cask libreoffice），"
        "或在 macOS 安装 Microsoft Word。"
    )


def _convert_via_libreoffice(docx: Path, pdf: Path, soffice: Path) -> None:
    out_dir = pdf.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    # LibreOffice 按 stem 命名；先转到临时旁路名再移到目标，避免覆盖冲突难排查。
    cmd = [
        str(soffice),
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise WordToPdfError(
            f"LibreOffice 转换超时（>{CONVERT_TIMEOUT_SEC}s）。转换失败，尚未评价内容。"
        ) from exc
    except OSError as exc:
        raise WordToPdfError(f"无法启动 LibreOffice：{exc}。转换失败，尚未评价内容。") from exc

    produced = out_dir / f"{docx.stem}.pdf"
    # 等待文件系统落盘（偶发延迟）
    deadline = time.monotonic() + 5
    while not produced.is_file() and time.monotonic() < deadline:
        time.sleep(0.1)

    if completed.returncode != 0 or not produced.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        hint = f"（{detail}）" if detail else ""
        raise WordToPdfError(
            f"LibreOffice 未能写出 PDF{hint}。转换失败，尚未评价内容。"
        )

    if produced.resolve() != pdf.resolve():
        if pdf.exists():
            pdf.unlink()
        produced.replace(pdf)


def _convert_via_msword(docx: Path, pdf: Path) -> None:
    pdf.parent.mkdir(parents=True, exist_ok=True)
    if pdf.exists():
        pdf.unlink()
    docx_s = _as_applescript_string(docx)
    pdf_s = _as_applescript_string(pdf)
    script = f'''
set docxFile to POSIX file "{docx_s}"
set pdfFile to POSIX file "{pdf_s}"
tell application "Microsoft Word"
  open docxFile
  set theDoc to active document
  save as theDoc file name pdfFile file format format PDF
  close theDoc saving no
end tell
'''
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise WordToPdfError(
            f"Microsoft Word 转换超时（>{CONVERT_TIMEOUT_SEC}s）。转换失败，尚未评价内容。"
        ) from exc
    except OSError as exc:
        raise WordToPdfError(f"无法调用 osascript：{exc}。转换失败，尚未评价内容。") from exc

    if completed.returncode != 0 or not pdf.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        hint = f"（{detail}）" if detail else ""
        raise WordToPdfError(
            f"Microsoft Word 未能写出 PDF{hint}。转换失败，尚未评价内容。"
        )


def convert_word_to_pdf(
    source: Path,
    *,
    out_dir: Path | None = None,
    root: Path | None = None,
) -> Path:
    """将 Word 转为 PDF，返回 PDF 路径。

    默认把 `{stem}.pdf` 写在 Word 同目录，便于接着跑 `resume`。
    运行记录写在 `data/output/.../word-to-pdf.json`。
    """
    try:
        docx_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise WordToPdfError(str(exc)) from exc

    suffix = docx_path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise WordToPdfError(
            "输入不是 Word（.docx / .doc）。请先转成 PDF，或改用本工具转换后再检查。"
            "检查对象仍是 PDF，本步不评价内容。"
        )

    target_dir = (out_dir or docx_path.parent).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / f"{docx_path.stem}.pdf"

    soffice = find_soffice()
    if soffice is not None:
        converter = "libreoffice"
        _convert_via_libreoffice(docx_path, pdf_path, soffice)
    elif msword_available():
        converter = "msword"
        _convert_via_msword(docx_path, pdf_path)
    else:
        raise WordToPdfError(describe_converter())

    if not pdf_path.is_file() or pdf_path.stat().st_size < 1:
        raise WordToPdfError("转换后未得到有效 PDF。转换失败，尚未评价内容。")

    run_dir = output_run_dir(docx_path, root=root)
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "tool": "word-to-pdf",
        "evaluates_layout": False,
        "evaluates_content": False,
        "input": str(docx_path),
        "output": str(pdf_path),
        "sha256_input": _sha256(docx_path),
        "sha256_output": _sha256(pdf_path),
        "converter": converter,
        "bytes": pdf_path.stat().st_size,
    }
    (run_dir / RECORD_NAME).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pdf_path
