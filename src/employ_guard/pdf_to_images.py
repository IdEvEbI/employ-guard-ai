"""把投递用 PDF 按页转成图片。本步不评价排版。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from employ_guard.paths import output_run_dir, resolve_input_file

DEFAULT_DPI = 200
RECORD_NAME = "pdf-to-images.json"


class PdfToImagesError(Exception):
    """输入不是可用 PDF，或出图失败。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_pdf_to_images(
    source: Path,
    *,
    dpi: int = DEFAULT_DPI,
    root: Path | None = None,
) -> Path:
    """按页写出 PNG，返回 `pages/` 目录。"""
    if dpi < 72 or dpi > 600:
        raise PdfToImagesError("dpi 须在 72 到 600 之间。")

    try:
        pdf_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise PdfToImagesError(str(exc)) from exc

    if pdf_path.suffix.lower() != ".pdf":
        raise PdfToImagesError("输入不是 PDF，请先转成 PDF 再检查。")

    run_dir = output_run_dir(pdf_path, root=root)
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 — 转成可给老师看的说明
        raise PdfToImagesError(f"无法打开这份 PDF：{exc}") from exc

    zoom = dpi / 72
    matrix = pymupdf.Matrix(zoom, zoom)
    written: list[str] = []
    try:
        if document.page_count < 1:
            raise PdfToImagesError("这份 PDF 没有页面，无法出图。")
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            name = f"{index + 1:03d}.png"
            pixmap.save(pages_dir / name)
            written.append(name)
    except PdfToImagesError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PdfToImagesError(f"按页出图失败：{exc}") from exc
    finally:
        document.close()

    leftover = [
        path
        for path in pages_dir.glob("*.png")
        if path.name not in written
    ]
    for path in leftover:
        path.unlink()

    record = {
        "tool": "pdf-to-images",
        "evaluates_layout": False,
        "input": str(pdf_path),
        "sha256": _sha256(pdf_path),
        "page_count": len(written),
        "dpi": dpi,
        "pages_dir": str(pages_dir),
        "pages": written,
        "pymupdf": pymupdf.VersionBind,
    }
    (run_dir / RECORD_NAME).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pages_dir
