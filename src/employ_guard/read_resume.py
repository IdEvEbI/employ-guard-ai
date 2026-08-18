"""从投递用 PDF 抽出文本。本步不判断能不能投，也不评价排版。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf

from employ_guard.paths import output_run_dir, resolve_input_file

_INTERNAL_SPACE = re.compile(r"[ \t]+")


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


def _markdown(pages: list[dict[str, object]]) -> str:
    body = "\n\n".join(str(item["text"]) for item in pages if str(item["text"]).strip())
    parts = [
        "# 简历文本",
        "",
        "> 本文件由 `read-resume` 从 PDF 抽出，供后续工具阅读。本步不判断能不能投，也不评价排版。页码只写在同目录的 JSON 中。分栏或页眉页脚可能导致文字顺序与版面不一致，查排版请看页图。",
        "",
        body,
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def extract_resume_text(
    source: Path,
    *,
    root: Path | None = None,
) -> Path:
    """抽出文本，写出 `{stem}.resume.md` 与 `{stem}.resume.json`，返回运行目录。"""
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
    try:
        if document.page_count < 1:
            raise ReadResumeError("这份 PDF 没有页面，无法抽文本。")
        for index in range(document.page_count):
            page = document.load_page(index)
            text = normalize_extracted_text(page.get_text("text", sort=True) or "")
            pages.append({"page": index + 1, "text": text, "char_count": len(text)})
    except ReadResumeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ReadResumeError(f"抽文本失败：{exc}") from exc
    finally:
        document.close()

    if not any(int(item["char_count"]) > 0 for item in pages):
        raise ReadResumeError(
            "未能抽出文字。若是扫描件或纯图片 PDF，本期抽文本无法处理；请改用可选中文字的 PDF。"
        )

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
        "pages": pages,
        "pymupdf": pymupdf.VersionBind,
    }
    (run_dir / md_name).write_text(_markdown(pages), encoding="utf-8")
    (run_dir / json_name).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir
