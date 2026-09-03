"""只凭页图查排版。对照 docs/04-standard/004 §3；不评价内容能不能投。"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from employ_guard.paths import output_run_dir, resolve_input_file
from employ_guard.vision import VisionError, chat_with_images

MAX_PASS_PAGES = 4
PDF_TO_IMAGES_RECORD = "pdf-to-images.json"

SYSTEM_PROMPT = """你是简历「排版」检查员。只根据页图判断版式。
禁止：评价项目写得好不好；判断内容能不能投递；把 Q1～Q3 改写成「信息密度 / 行距观感 / 多页风格统一」等其它含义。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "pass_line": [
    {"id": "P2", "pass": true, "note": "须点名页码；对照下方 P2 定义"},
    {"id": "P3", "pass": true, "note": "须点名页码；对照下方 P3 定义"},
    {"id": "P4", "pass": true, "note": "须点名页码；对照下方 P4 定义"},
    {"id": "P5", "pass": true, "note": "无校区/试用类贴图水印，或虽有但不影响判读（本项通过）"}
  ],
  "level_line": [
    {"id": "Q1", "signal": true, "note": "只谈栏位与块对齐"},
    {"id": "Q2", "signal": true, "note": "只谈标题层级与列表符号"},
    {"id": "Q3", "signal": true, "note": "只谈联系方式与正文分区"}
  ],
  "defects": [
    {"code": "leading_punct", "found": false, "pages": [], "note": ""},
    {"code": "bullet_inconsistent", "found": false, "pages": [], "note": ""},
    {"code": "alignment", "found": false, "pages": [], "note": ""},
    {"code": "tight_spacing", "found": false, "pages": [], "note": ""},
    {"code": "font_inconsistent", "found": false, "pages": [], "note": ""}
  ]
}

## 合格线（任一 pass=false → 排版未合格；硬伤须举页码）
- P2 无严重溢出：无大面积出框、叠字、切字；正文不被边框/装饰切断。
- P3 结构可扫读：能较快定位技能、项目或工作等主块；版式不致无法扫读。若「行首标点」或「列表符号混乱」严重到阻碍扫读 → P3 判未过。
- P4 留白不过分极端：不是近乎空白凑页，也不是完全无层级的文字墙。若段前段后/行距过密导致大片文字贴死、难以换气 → P4 判未过（末页过短可写「可改」但仍可 pass=true）。
- P5 水印例外：只检查「校区或试用类贴图水印」。有此类水印时仍判 pass=true，note 写「第X页有校区/试用类水印，按标准不因此判不合格」。无此类水印时 note 写「无校区/试用类贴图水印（本项通过）」。P5 禁止改写为「无切字 / 留白正常」等其它项。

## 水平线（不单独决定合格；signal 可为 false；禁止全绿敷衍）
- Q1：单栏或稳定双栏，项目块/日期列是否对齐（同列左缘是否一条直线）。
- Q2：标题层级是否清楚；列表符号是否全文一致（• / ■ / - / ① 等勿混用）；条目缩进是否一致。
- Q3：联系方式与正文是否分区清楚，不与项目主文争抢视线。

## 必须逐项查看的排版缺陷（写入 defects，并映射到上表）
1. leading_punct：是否出现标点（，。；：、.!?)等）落在行首（中文排版忌行首标点）。多处或醒目 → found=true；若严重妨碍扫读，P3.pass=false。
2. bullet_inconsistent：列表符号种类或缩进是否前后不一致。明显混用 → found=true，且 Q2.signal 倾向 false；严重混乱 → P3.pass=false。
3. alignment：正文是否应左对齐却出现随意居中/错位；是否误用两端对齐导致字距忽大忽小；同级标题或条目左缘是否不齐。明显问题 → found=true，且 Q1.signal 倾向 false；大面积错位 → P3.pass=false。
4. tight_spacing：段前段后或行距是否过密，模块之间几乎无呼吸感，文字挤成墙。明显过密 → found=true；达到「文字墙」程度 → P4.pass=false。多页简历若单页内大段正文挤成墙、关键信息淹没 → found=true。
5. long_blocks（可选写入 note 到 tight_spacing）：页内是否出现大段连续正文、要点不短、扫读吃力；若明显 → tight_spacing.found=true，note 写明「长段宜拆成项目符号短句」。
6. font_inconsistent（**须逐页对照，宁严勿松**）：逐页比较——① 技能区正文 vs 项目 / 工作区正文的**字号与字重**是否跳变；② 同级小节标题条（蓝底条 / 加粗标题）样式是否前后不一；③ 跨页正文字体族或字号是否明显变大变小；④ 同一模块内中英混排是否忽大忽小。只要有一处肉眼可辨的前后不一致 → **found=true**，pages 写页码，note 写「哪里与哪里不一致」。拿不准时优先 found=true。**单独字体不一致不得因此把 P2～P5 判未过**，除非已严重到无法扫读（那时用 P3）。禁止在「看起来还整齐」时一律 found=false 敷衍。

## 行业常见版式要点（仅作视觉参照，仍只评版式）
- 正文宜统一左对齐；避免正文大段居中或两端对齐造成字距不匀。
- 列表符号全文一种风格；条目缩进一致；**宜用短要点，避免整页长段墙**。
- 模块之间保留可感知间距；行距过紧会降低扫读效率。
- 正文与同级标题宜全文统一字号与字体；前后跳变记入 font_inconsistent。
- 边距过窄易导致切字（归入 P2）。
- 投递成品宜 ≤4 页；超页时仍须指出长段、过密等可改点（P1 由规则层计页）。
- 不评价照片、配色喜好或文案质量。
"""

USER_PROMPT = (
    "以上是按页顺序的简历页图。请严格按系统说明只输出 JSON。"
    "Q1～Q3 的 note 必须分别对应「对齐 / 列表与标题 / 联系方式分区」，禁止改写含义。"
    "defects 五项必须全部给出（含 font_inconsistent）。"
    "请专门对比第 1 页技能区与后续页项目区的字号 / 字重；有跳变就把 font_inconsistent.found 设为 true。"
    "字体不一致只写入细项，不要单独因此把合格线判未过。"
    "不要根据文字内容判断能不能投递。"
)

VisualAssessor = Callable[[list[Path]], dict[str, Any]]

DEFECT_CODES = (
    "leading_punct",
    "bullet_inconsistent",
    "alignment",
    "tight_spacing",
    "font_inconsistent",
)

FONT_HUMAN_REVIEW_TIP = (
    "请人工再看各页正文字号、字体族与同级标题样式是否前后一致"
    "（技能区与项目区、首页与后续页）；视觉模型易漏判，不因此单独判合格线未过。"
)


@dataclass(frozen=True)
class LayoutResult:
    """一次查排版的落盘结果。"""

    run_dir: Path
    layout_pass: bool
    page_count: int
    pass_line: list[dict[str, Any]]
    level_line: list[dict[str, Any]]
    defects: list[dict[str, Any]]
    revision_tips: list[str]
    report_md: Path
    report_json: Path


class CheckLayoutError(Exception):
    """缺少页图、输入无效，或查排版失败。"""


def build_revision_tips(
    *,
    page_count: int,
    pass_line: list[dict[str, Any]],
    defects: list[dict[str, Any]],
) -> list[str]:
    """合格线未过或有细项时，给出可执行的压页 / 扫读改稿要点。"""
    tips: list[str] = []
    if page_count > MAX_PASS_PAGES:
        tips.append(
            f"当前共 {page_count} 页，须压到 {MAX_PASS_PAGES} 页以内再投。"
        )
        tips.append(
            "优先压缩个人优势与非目标方向经历，项目职责改为短要点，删除重复技能堆砌。"
        )
        tips.append(
            "长段落拆成项目符号（•）短句，让岗位方向、主项目与量化结果更容易被扫到。"
        )
    failed_ids = {str(item.get("id")) for item in pass_line if not item.get("pass")}
    if "P2" in failed_ids:
        tips.append("先消除出框、叠字或切字，保证每页正文完整可读。")
    if "P3" in failed_ids:
        tips.append("理清技能 / 项目 / 工作等主块分区，统一列表符号与缩进，保证可扫读。")
    if "P4" in failed_ids:
        tips.append("减轻文字墙：加大模块间距，把长段改为短要点，避免整页挤满。")
    for item in defects:
        if not item.get("found"):
            continue
        code = str(item.get("code") or "")
        note = str(item.get("note") or "").strip()
        if code == "tight_spacing":
            tips.append(note or "段前段后过密，宜留白并拆短句。")
        elif code == "bullet_inconsistent":
            tips.append(note or "列表符号与缩进宜全文统一。")
        elif code == "alignment":
            tips.append(note or "同级块左缘对齐，避免随意居中或错位。")
        elif code == "leading_punct":
            tips.append(note or "避免标点落在行首。")
        elif code == "font_inconsistent":
            tips.append(note or "统一正文字号与同级标题样式，避免前后跳变。")
    font_found = any(
        str(item.get("code") or "") == "font_inconsistent" and item.get("found")
        for item in defects
    )
    if not font_found:
        tips.append(FONT_HUMAN_REVIEW_TIP)
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for tip in tips:
        if tip not in seen:
            seen.add(tip)
            unique.append(tip)
    return unique


def _sha256_pages(paths: list[Path]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _list_page_images(pages_dir: Path) -> list[Path]:
    pages = sorted(pages_dir.glob("*.png"))
    if not pages:
        pages = sorted(pages_dir.glob("*.jpg")) + sorted(pages_dir.glob("*.jpeg"))
    return pages


def resolve_pages_dir(source: Path, *, root: Path | None = None) -> tuple[Path, Path, list[Path]]:
    """由 PDF 定位运行目录与 pages/；返回 (pdf_path, pages_dir, page_files)。"""
    try:
        pdf_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise CheckLayoutError(str(exc)) from exc

    if pdf_path.suffix.lower() != ".pdf":
        raise CheckLayoutError("输入不是 PDF，请先转成 PDF 再检查。")

    run_dir = output_run_dir(pdf_path, root=root)
    pages_dir = run_dir / "pages"
    if not pages_dir.is_dir():
        raise CheckLayoutError(
            f"未找到页图目录 {pages_dir}。请先运行：employ-guard pdf-to-images <简历.pdf>"
        )
    pages = _list_page_images(pages_dir)
    if not pages:
        raise CheckLayoutError(
            f"页图目录为空：{pages_dir}。请先运行：employ-guard pdf-to-images <简历.pdf>"
        )
    return pdf_path, pages_dir, pages


def evaluate_p1(page_count: int) -> dict[str, Any]:
    """P1 页数可控：总页数 ≤ 4。纯规则，不看图模型。"""
    passed = page_count <= MAX_PASS_PAGES
    note = (
        f"共 {page_count} 页，不超过 {MAX_PASS_PAGES} 页。"
        if passed
        else f"共 {page_count} 页，超过 {MAX_PASS_PAGES} 页 → 排版未合格。"
    )
    return {"id": "P1", "pass": passed, "note": note, "method": "rule"}


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise CheckLayoutError(f"看图结果不是合法 JSON：{stripped[:400]}") from exc
    if not isinstance(data, dict):
        raise CheckLayoutError("看图结果须为 JSON 对象。")
    return data


def _normalize_defects(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = data.get("defects", [])
    by_code: dict[str, dict[str, Any]] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict) and item.get("code"):
                by_code[str(item["code"])] = item
    defects: list[dict[str, Any]] = []
    for code in DEFECT_CODES:
        item = by_code.get(code, {})
        pages = item.get("pages") or []
        if not isinstance(pages, list):
            pages = [pages]
        defects.append(
            {
                "code": code,
                "found": bool(item.get("found", False)),
                "pages": pages,
                "note": str(item.get("note") or ""),
            }
        )
    return defects


def _normalize_visual(data: dict[str, Any]) -> dict[str, Any]:
    pass_line: list[dict[str, Any]] = []
    by_id = {
        str(item.get("id")): item
        for item in data.get("pass_line", [])
        if isinstance(item, dict)
    }
    for code, default_note in (
        ("P2", "未返回该项"),
        ("P3", "未返回该项"),
        ("P4", "未返回该项"),
        ("P5", "未返回该项"),
    ):
        item = by_id.get(code, {})
        pass_line.append(
            {
                "id": code,
                "pass": bool(item.get("pass", False)),
                "note": str(item.get("note") or default_note),
                "method": "vision",
            }
        )

    level_line: list[dict[str, Any]] = []
    level_by_id = {
        str(item.get("id")): item
        for item in data.get("level_line", [])
        if isinstance(item, dict)
    }
    for code in ("Q1", "Q2", "Q3"):
        item = level_by_id.get(code, {})
        level_line.append(
            {
                "id": code,
                "signal": bool(item.get("signal", False)),
                "note": str(item.get("note") or "未返回该项"),
                "method": "vision",
            }
        )
    return {
        "pass_line": pass_line,
        "level_line": level_line,
        "defects": _normalize_defects(data),
    }


def default_vision_assessor(pages: list[Path]) -> dict[str, Any]:
    """把页图发给视觉模型，解析 P2～P5 / Q1～Q3 与 defects。"""
    data_urls: list[str] = []
    for path in pages:
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        data_urls.append(f"data:{mime};base64,{b64}")
    try:
        text = chat_with_images(
            system=SYSTEM_PROMPT,
            user_text=USER_PROMPT,
            image_data_urls=data_urls,
            detail="high",
        )
    except VisionError as exc:
        raise CheckLayoutError(str(exc)) from exc
    return _normalize_visual(_parse_json_object(text))


def _markdown_report(
    *,
    layout_pass: bool,
    page_count: int,
    pass_line: list[dict[str, Any]],
    level_line: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    revision_tips: list[str],
    pages_dir: Path,
) -> str:
    verdict = "排版合格" if layout_pass else "排版未合格"
    lines = [
        "# 查排版结果",
        "",
        "> 本文件由 `check-layout` 只根据页图生成。不评价内容能不能投。合格线与水平线分开写。",
        "",
        f"**结论**：{verdict}（共 {page_count} 页）",
        "",
        f"页图目录：`{pages_dir}`",
        "",
        "## 合格线",
        "",
        "| 编号 | 是否过 | 说明 |",
        "| ---- | ------ | ---- |",
    ]
    for item in pass_line:
        mark = "过" if item["pass"] else "未过"
        note = str(item["note"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['id']} | {mark} | {note} |")

    if revision_tips:
        lines.extend(["", "## 改稿要点", ""])
        for tip in revision_tips:
            lines.append(f"- {tip}")

    defect_labels = {
        "leading_punct": "行首标点",
        "bullet_inconsistent": "列表符号不一致",
        "alignment": "对齐异常",
        "tight_spacing": "段前段后过密",
        "font_inconsistent": "字体字号不一致",
    }
    lines.extend(["", "## 细项缺陷（页图）", "", "| 代码 | 是否发现 | 页码 | 说明 |", "| ---- | -------- | ---- | ---- |"])
    for item in defects:
        found = "是" if item.get("found") else "否"
        pages = ", ".join(str(p) for p in item.get("pages") or []) or "—"
        note = str(item.get("note") or "").replace("|", "\\|").replace("\n", " ")
        label = defect_labels.get(str(item.get("code")), str(item.get("code")))
        lines.append(f"| {label} | {found} | {pages} | {note or '—'} |")

    lines.extend(["", "## 水平线", ""])
    if not layout_pass:
        lines.append("未过合格线，不输出「水平更高 / 更低」的排序结论。改稿请先看上方「改稿要点」。")
    else:
        lines.extend(
            [
                "| 编号 | 信号 | 说明 |",
                "| ---- | ---- | ---- |",
            ]
        )
        for item in level_line:
            mark = "有" if item.get("signal") else "弱 / 无"
            note = str(item["note"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {item['id']} | {mark} | {note} |")
    lines.append("")
    return "\n".join(lines)


def check_layout(
    source: Path,
    *,
    root: Path | None = None,
    visual_assessor: VisualAssessor | None = None,
) -> LayoutResult:
    """查排版，写出 `{stem}.layout.md` / `{stem}.layout.json`，返回结果。"""
    pdf_path, pages_dir, pages = resolve_pages_dir(source, root=root)
    run_dir = pages_dir.parent
    page_count = len(pages)

    p1 = evaluate_p1(page_count)
    assessor = visual_assessor or default_vision_assessor
    visual = assessor(pages)
    visual_pass = list(visual.get("pass_line") or [])
    level_line = list(visual.get("level_line") or [])
    defects = list(visual.get("defects") or _normalize_defects({}))

    pass_line = [p1, *visual_pass]
    layout_pass = all(bool(item.get("pass")) for item in pass_line)
    if not layout_pass:
        level_line = []
    revision_tips = build_revision_tips(
        page_count=page_count,
        pass_line=pass_line,
        defects=defects,
    )

    stem = pdf_path.stem
    md_name = f"{stem}.layout.md"
    json_name = f"{stem}.layout.json"
    report_md = run_dir / md_name
    report_json = run_dir / json_name
    record = {
        "tool": "check-layout",
        "judges_content": False,
        "evaluates_layout": True,
        "layout_pass": layout_pass,
        "standard": "docs/04-standard/004_resume-bar_简历合格线.md#3",
        "input": str(pdf_path),
        "pages_dir": str(pages_dir),
        "page_count": page_count,
        "pages": [path.name for path in pages],
        "pages_sha256": _sha256_pages(pages),
        "pass_line": pass_line,
        "level_line": level_line,
        "defects": defects,
        "revision_tips": revision_tips,
        "method": {
            "P1": "rule",
            "P2_P5_Q": "vision" if visual_assessor is None else "injected",
        },
    }
    if (run_dir / PDF_TO_IMAGES_RECORD).is_file():
        record["pdf_to_images_record"] = PDF_TO_IMAGES_RECORD

    report_md.write_text(
        _markdown_report(
            layout_pass=layout_pass,
            page_count=page_count,
            pass_line=pass_line,
            level_line=level_line,
            defects=defects,
            revision_tips=revision_tips,
            pages_dir=pages_dir,
        ),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return LayoutResult(
        run_dir=run_dir,
        layout_pass=layout_pass,
        page_count=page_count,
        pass_line=pass_line,
        level_line=level_line,
        defects=defects,
        revision_tips=revision_tips,
        report_md=report_md,
        report_json=report_json,
    )
