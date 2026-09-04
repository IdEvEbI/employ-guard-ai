"""查排版几何规则：页图行占用率等，作过密 / 文字墙兜底（不替代视觉）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

# 近白像素阈值高于此视为「纸面」。
_INK_LUMA_MAX = 245
# 有墨迹的扫描行占比达到此值 → 记过密细项（兜底）。
DENSE_ROW_OCCUPANCY = 0.45
# 达到此值 → 规则层可拉低 P4（文字墙兜底）。
WALL_ROW_OCCUPANCY = 0.65


@dataclass(frozen=True)
class GeometryScan:
    """各页行占用率与过密 / 文字墙页码（1-based）。"""

    page_row_occupancy: list[float]
    page_avg_gap: list[float]
    dense_pages: list[int]
    wall_pages: list[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_row_occupancy": [round(r, 4) for r in self.page_row_occupancy],
            "page_avg_gap": [round(g, 4) for g in self.page_avg_gap],
            "dense_pages": list(self.dense_pages),
            "wall_pages": list(self.wall_pages),
            "thresholds": {
                "dense_row_occupancy": DENSE_ROW_OCCUPANCY,
                "wall_row_occupancy": WALL_ROW_OCCUPANCY,
            },
            "method": "rule",
        }


def _row_metrics(path: Path) -> tuple[float, float]:
    """返回 (有墨迹行占比, 相邻墨迹行平均空隙行数)。"""
    try:
        pix = pymupdf.Pixmap(str(path))
    except Exception:  # noqa: BLE001
        return 0.0, 999.0
    try:
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)  # type: ignore[assignment]
        if pix.n != 1:
            gray = pymupdf.Pixmap(pymupdf.csGRAY, pix)
            pix = None  # type: ignore[assignment]
            pix = gray
        width, height = pix.width, pix.height
        samples = pix.samples
        if not samples or width < 1 or height < 1:
            return 0.0, 999.0
        ink_rows: list[int] = []
        for y in range(height):
            row = samples[y * width : (y + 1) * width]
            if any(byte < _INK_LUMA_MAX for byte in row):
                ink_rows.append(y)
        occupancy = len(ink_rows) / height
        if len(ink_rows) < 2:
            return occupancy, 999.0
        gaps = [b - a - 1 for a, b in zip(ink_rows, ink_rows[1:], strict=False)]
        avg_gap = sum(gaps) / len(gaps)
        return occupancy, avg_gap
    finally:
        pix = None


def scan_page_geometry(pages: list[Path]) -> GeometryScan:
    """对 pages/ 下页图做行占用率扫描。"""
    occupancies: list[float] = []
    gaps: list[float] = []
    dense: list[int] = []
    wall: list[int] = []
    for index, path in enumerate(pages, start=1):
        occ, avg_gap = _row_metrics(path)
        occupancies.append(occ)
        gaps.append(avg_gap)
        if occ >= DENSE_ROW_OCCUPANCY:
            dense.append(index)
        if occ >= WALL_ROW_OCCUPANCY:
            wall.append(index)
    return GeometryScan(
        page_row_occupancy=occupancies,
        page_avg_gap=gaps,
        dense_pages=dense,
        wall_pages=wall,
    )


def apply_geometry_fallback(
    pass_line: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    scan: GeometryScan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """用几何规则兜底补强过密细项与极端 P4；不撤销视觉已判未过。"""
    pass_out = [dict(item) for item in pass_line]
    defects_out = [dict(item) for item in defects]

    if scan.dense_pages:
        pages_label = "、".join(f"第{p}页" for p in scan.dense_pages)
        note = (
            f"规则层兜底：{pages_label}内容行过密（扫描行占用率过高，疑似文字挤成墙）。"
        )
        for item in defects_out:
            if str(item.get("code")) != "tight_spacing":
                continue
            if item.get("found"):
                existing = {
                    int(p)
                    for p in (item.get("pages") or [])
                    if isinstance(p, int) or str(p).isdigit()
                }
                item["pages"] = sorted(existing | set(scan.dense_pages))
                if "规则层" not in str(item.get("note") or ""):
                    item["note"] = f"{item.get('note') or ''}；{note}".strip("；")
                item["method"] = "vision+rule"
            else:
                item["found"] = True
                item["pages"] = list(scan.dense_pages)
                item["note"] = note
                item["method"] = "rule"
            break

    if scan.wall_pages:
        pages_label = "、".join(f"第{p}页" for p in scan.wall_pages)
        wall_note = f"规则层兜底：{pages_label}接近文字墙（扫描行占用率过高）。"
        for item in pass_out:
            if str(item.get("id")) != "P4":
                continue
            if item.get("pass"):
                item["pass"] = False
                item["note"] = wall_note
                item["method"] = "rule"
            elif "规则层" not in str(item.get("note") or ""):
                item["note"] = f"{item.get('note') or ''}；{wall_note}".strip("；")
                item["method"] = "vision+rule"
            break

    return pass_out, defects_out
