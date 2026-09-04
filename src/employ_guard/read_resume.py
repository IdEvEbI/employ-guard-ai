"""从投递用 PDF 抽出文本。本步不判断能不能投，也不评价排版。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pymupdf

from employ_guard.paths import output_run_dir, resolve_input_file

_INTERNAL_SPACE = re.compile(r"[ \t]+")

# 整份文字层为空时视为扫描件，触发 OCR（只挂在本工具）。
DEFAULT_OCR_LANGUAGE = "chi_sim+eng"

PageOcr = Callable[[pymupdf.Page], str]


class ReadResumeError(Exception):
    """输入不是可用 PDF，或抽文本失败。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_extracted_text(text: str) -> str:
    """去掉版面空格，连续空行最多保留一个。不猜测字段名，不拼接视觉折行。"""
    lines: list[str] = []
    previous_blank = False
    for raw in text.splitlines():
        line = _INTERNAL_SPACE.sub(" ", raw).strip()
        if not line:
            if lines and not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    return "\n".join(lines).strip()


def tesseract_available() -> bool:
    """本机是否找得到 tesseract（扫描件 OCR 依赖）。"""
    return shutil.which("tesseract") is not None


def _default_ocr_page(page: pymupdf.Page, *, language: str = DEFAULT_OCR_LANGUAGE) -> str:
    if not tesseract_available():
        raise ReadResumeError(
            "这份 PDF 几乎抽不出文字层（常见于扫描件或纯图片 PDF）。"
            "需要本机 OCR：请安装 tesseract 及中文语言包后重试"
            "（macOS：brew install tesseract tesseract-lang）。"
            "本步失败不等于内容不能投。"
        )
    try:
        textpage = page.get_textpage_ocr(language=language, dpi=200, full=True)
        raw = page.get_text("text", textpage=textpage) or ""
    except Exception as exc:  # noqa: BLE001
        raise ReadResumeError(
            f"OCR 抽文本失败：{exc}。"
            "请确认 tesseract 可用且已安装中文语言包（如 chi_sim）。"
            "本步失败不等于内容不能投。"
        ) from exc
    return normalize_extracted_text(raw)


def _markdown(pages: list[dict[str, object]], *, used_ocr: bool) -> str:
    body = "\n\n".join(str(item["text"]) for item in pages if str(item["text"]).strip())
    source_note = (
        "部分或全部页面经 OCR（扫描件兜底）抽出，"
        if used_ocr
        else "由 PDF 文字层抽出，"
    )
    parts = [
        "# 简历文本",
        "",
        f"> 本文件由 `read-resume` {source_note}"
        "供后续工具阅读。本步不判断能不能投，也不评价排版。"
        "页码只写在同目录的 JSON 中。分栏或页眉页脚可能导致文字顺序与版面不一致，查排版请看页图。",
        "",
        body,
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def extract_resume_text(
    source: Path,
    *,
    root: Path | None = None,
    ocr_page: PageOcr | None = None,
    ocr_language: str = DEFAULT_OCR_LANGUAGE,
) -> Path:
    """抽出文本，写出 `{stem}.resume.md` 与 `{stem}.resume.json`，返回运行目录。

    文字层过少时对本页做 OCR（默认 tesseract，可注入 `ocr_page` 便于测试）。
    """
    try:
        pdf_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise ReadResumeError(str(exc)) from exc

    if pdf_path.suffix.lower() != ".pdf":
        raise ReadResumeError("输入不是 PDF，请先转成 PDF 再检查。")

    run_dir = output_run_dir(pdf_path, root=root)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 — 转成可给老师看的说明
        raise ReadResumeError(f"无法打开这份 PDF：{exc}") from exc

    pages: list[dict[str, object]] = []
    ocr_page_numbers: list[int] = []
    run_ocr = ocr_page or (lambda page: _default_ocr_page(page, language=ocr_language))

    try:
        if document.page_count < 1:
            raise ReadResumeError("这份 PDF 没有页面，无法抽文本。")
        native_pages: list[str] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            text = normalize_extracted_text(page.get_text("text", sort=True) or "")
            native_pages.append(text)

        total_native = sum(len(text) for text in native_pages)
        # 仅当整份文字层为空时走 OCR，避免短样例 / 数字 PDF 误触发。
        document_looks_scanned = total_native == 0

        for index, native in enumerate(native_pages):
            page_no = index + 1
            text = native
            source = "native"
            if document_looks_scanned:
                page = document.load_page(index)
                text = run_ocr(page)
                source = "ocr"
                ocr_page_numbers.append(page_no)
            pages.append(
                {
                    "page": page_no,
                    "text": text,
                    "char_count": len(text),
                    "source": source,
                }
            )
    except ReadResumeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ReadResumeError(f"抽文本失败：{exc}") from exc
    finally:
        document.close()

    if not any(int(item["char_count"]) > 0 for item in pages):
        raise ReadResumeError(
            "未能抽出文字（文字层与 OCR 均为空）。"
            "请换可选中文字的 PDF，或检查扫描件清晰度与 tesseract 语言包。"
            "本步失败不等于内容不能投。"
        )

    used_ocr = bool(ocr_page_numbers)
    if used_ocr and len(ocr_page_numbers) == len(pages):
        extraction = "ocr"
    elif used_ocr:
        extraction = "mixed"
    else:
        extraction = "native"

    stem = pdf_path.stem
    md_name = f"{stem}.resume.md"
    json_name = f"{stem}.resume.json"
    record = {
        "tool": "read-resume",
        "judges_content": False,
        "evaluates_layout": False,
        "input": str(pdf_path),
        "sha256": _sha256(pdf_path),
        "page_count": len(pages),
        "extraction": extraction,
        "ocr_pages": ocr_page_numbers,
        "pages": pages,
        "pymupdf": pymupdf.VersionBind,
    }
    (run_dir / md_name).write_text(_markdown(pages, used_ocr=used_ocr), encoding="utf-8")
    (run_dir / json_name).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir
