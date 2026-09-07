"""文本规范化与字段抽取。结构化底座；不判能不能投，不评排版。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from employ_guard.judge_resume import JudgeResumeError, resolve_resume_text
from employ_guard.llm import LLMError, chat_completion
from employ_guard.read_resume import normalize_extracted_text

DISCLAIMER = (
    "本文件由 `parse-resume` 根据简历文本规范化并抽取字段。"
    "不判断能不能投，也不评价排版；性别等字段可空，缺省不构成硬伤。"
    "抽取不完整时仅标明 parse_incomplete，不得写成内容不能投。"
)
PARSE_MAX_TOKENS = 8192
NORMALIZE_MAX_TOKENS = 8192
ATTEMPT_STRATEGIES: tuple[dict[str, object], ...] = (
    {"json_object": True, "user_suffix": ""},
    {
        "json_object": True,
        "user_suffix": "\n\n请只输出一个合法 json 对象，不要 Markdown 围栏。",
    },
    {
        "json_object": False,
        "user_suffix": "\n\n请只输出一个合法 json 对象，不要 Markdown 围栏。",
    },
)

NORMALIZE_SYSTEM_PROMPT = """你是就业辅导场景的简历文本规范化员。把 PDF/工具抽出的简历正文整理成清晰、可阅读的 Markdown。
禁止：判断能不能投；评价排版；代写扩写；编造简历未出现的公司、项目、学校、指标；打百分制。
必须只输出规范化后的 Markdown 正文，不要 Markdown 围栏，不要前言后语。

## 硬约束
- **不改字义**：保留原文用词与数字；可合并视觉折行、补中英文空格、去掉装饰符号，但不得改事实。
- 装饰性前缀（如 ■、●、•、·）不要留在章节标题里；列表项改为 Markdown 的 `-` 或 `1.`。

## 结构约定
- 一级章节用 `##`：基本信息、专业技能、工作经历/工作经验、项目经历/项目经验、教育背景/教育经历、自我评价、语言能力等。原文有「■ 专业技能」等应写成 `## 专业技能`。
- 若文首有姓名/联系方式/求职意向但无「基本信息」标题，可补 `## 基本信息`。
- 每一段工作的「公司 + 岗位 + 时间」行用 `###`；其下补 `#### 工作职责`（原文有则保留语义，无则补标题），职责用列表。
- 每一个项目的标题行用 `###`（含「项目一：…」或「名称 · 副题 [标签]」等）；其下「项目背景 / 技术栈 / 责任描述 / 项目成果」等用 `####`，内容用列表或段落。
- 合并 PDF 造成的同一句折行；不要把章节标题粘进上一句。
- 分栏被抽成一行时（如「教育经历 语言能力 自我评价」），按语义拆成对应 `##` 小节，勿把三栏正文糊成一句。
- 日期、联系方式保持可读；不要把「1995/07」改成别的年份。
"""


def _light_pre_normalize(text: str) -> str:
    """交给模型前的轻量清理：不去猜结构、不合并折行。"""
    body = normalize_extracted_text(text)
    lines = [_MULTI_SPACE.sub(" ", line).strip() for line in body.splitlines()]
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if cleaned and not prev_blank:
                cleaned.append("")
            prev_blank = True
            continue
        cleaned.append(line)
        prev_blank = False
    return "\n".join(cleaned).strip()


def _strip_md_fence(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)```", stripped)
    if fence:
        return fence.group(1).strip()
    return stripped


NormalizeAssessor = Callable[[str], str]


def default_normalize_assessor(text: str) -> str:
    """用 LLM 把抽出正文整理为 Markdown（不改字义）。"""
    user_text = "\n".join(
        [
            "以下是从简历抽出的正文（可能有折行、装饰符、分栏乱序）。请按系统说明规范化为 Markdown。",
            "",
            text,
            "",
            "请只输出规范化后的 Markdown 正文。",
        ]
    )
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            out = chat_completion(
                system=NORMALIZE_SYSTEM_PROMPT,
                user_text=user_text,
                json_object=False,
                max_tokens=NORMALIZE_MAX_TOKENS,
            )
            cleaned = _strip_md_fence(out)
            if not cleaned.strip():
                raise ParseResumeError("LLM 规范化返回为空。")
            return cleaned.strip()
        except (LLMError, ParseResumeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(float(attempt))
                continue
            raise ParseResumeError(str(exc)) from exc
    raise ParseResumeError(str(last_error) if last_error else "规范化失败。")

# 中英文粘连补空格；不改词义。
_CJK_LATIN = re.compile(
    r"([\u4e00-\u9fff])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff])"
)
_SPACE_BEFORE_CN_PUNCT = re.compile(r" +([，。；：！？、）】」』])")
_SPACE_AFTER_OPEN = re.compile(r"([（【「『]) +")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# 新段 / 新条：不宜与上一行合并
_NEW_BLOCK_START = re.compile(
    r"^(?:"
    r"[●•·▪◦]\s*"
    r"|\d+[\.、．]\s*"
    r"|[一二三四五六七八九十]+[、．.]\s*"
    r"|项目[一二三四五六七八九十\d]+"
    r"|业务背景|项目技术|项目成果|项目背景|工作职责"
    r"|工作经历|工作经验|项目经历|教育背景|教育经历|自我评价"
    r"|专业技能|基本信息|基础信息|个人信息"
    r"|姓名|姓\s*名|求职|应聘"
    r")"
)
# 含英文句号：PDF 常把中文句号抽成「.」
_TERMINAL_END = re.compile(r"[。！？；;…：:.]$")
_FIELD_LINE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9@./\s+-]*[：:]")
_CONTACT_LINE_DONE = re.compile(
    r"(?:@[\w.-]+\.\w+|求职意向|应聘岗位|应聘职位)"
)
_ORDERED_TITLE_LINE = re.compile(r"^\d+[\.、．]\s*.+$")
_PROJECT_HEAD_LINE = re.compile(
    r".{2,40}项目(?:负责人|成员|参与者?).{0,20}\d{4}\s*[./年]"
)
# 公司/项目头常以「2023/09 - 2026/07」收尾，勿与职责首行粘连
_DATE_RANGE_TAIL = re.compile(
    r"\d{4}\s*[./年]\s*\d{1,2}"
    r"(?:\s*[-–—~至]\s*(?:\d{4}\s*[./年]\s*\d{1,2}|至今|今))?$"
)


def _is_complete_block_line(line: str) -> bool:
    """上一行已是完整块（段末 / 联系方式 / 日期头），勿再吞并下一行。"""
    return bool(
        _TERMINAL_END.search(line)
        or _CONTACT_LINE_DONE.search(line)
        or _DATE_RANGE_TAIL.search(line)
    )


def _merge_soft_wrap_lines(lines: list[str]) -> list[str]:
    """合并 PDF 视觉折行造成的断行；不改字义。"""
    if not lines:
        return []
    # 先去掉「未收尾行」后面的空行，否则视觉折行会被空行打断
    compact: list[str] = []
    for line in lines:
        if (
            not line
            and compact
            and compact[-1]
            and not _is_complete_block_line(compact[-1])
            and not _NEW_BLOCK_START.match(compact[-1])
        ):
            continue
        compact.append(line)

    merged: list[str] = [compact[0]]
    for line in compact[1:]:
        prev = merged[-1]
        if not prev or not line:
            merged.append(line)
            continue
        if _is_complete_block_line(prev):
            merged.append(line)
            continue
        if _NEW_BLOCK_START.match(line) or _FIELD_LINE.match(line):
            merged.append(line)
            continue
        # 短标题行（如「项目技术栈与创新点」）不吞并后文
        if (
            len(prev) <= 16
            and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9 ·/+-]+", prev)
            and not re.search(r"[，、,的与和及]$", prev)
        ):
            merged.append(line)
            continue
        # 编号短标题（如「1.多智能体架构设计与模块化拆分开发」）不吞并正文
        if (
            _ORDERED_TITLE_LINE.match(prev)
            and len(prev) <= 40
            and not re.search(r"[，、,的与和及]$", prev)
        ):
            merged.append(line)
            continue
        left = prev[-1]
        right = line[0]
        need_space = bool(
            re.match(r"[A-Za-z0-9]", left) and re.match(r"[A-Za-z0-9]", right)
        )
        merged[-1] = prev + (" " if need_space else "") + line
    return merged


def _inject_inferred_section_headers(lines: list[str]) -> list[str]:
    """装饰条标题常不在 PDF 文字层：按内容启发式补「基本信息 / 专业技能」。"""
    text = "\n".join(lines)
    inserted_basic = (
        "基本信息" in text or "基础信息" in text or "个人信息" in text
    )
    inserted_skills = "专业技能" in text
    out: list[str] = []
    for line in lines:
        if (
            not inserted_basic
            and re.search(r"姓\s*名\s*[：:]", line)
        ):
            out.append("基本信息")
            out.append("")
            inserted_basic = True
        if (
            not inserted_skills
            and re.match(r"[●•·▪◦]", line)
            and any(
                key in line
                for key in (
                    "熟练",
                    "掌握",
                    "熟悉",
                    "Python",
                    "Java",
                    "RAG",
                    "LLM",
                )
            )
        ):
            prior = "\n".join(out[-40:])
            if inserted_basic or re.search(r"(姓名|电话|邮箱|出生)", prior):
                if out and out[-1] != "":
                    out.append("")
                out.append("专业技能")
                out.append("")
                inserted_skills = True
        out.append(line)
    return out


def normalize_resume_body_rules(text: str) -> str:
    """规则规范化（测试注入与 LLM 失败回退）；不改字义，不是页图查排版。"""
    body = normalize_extracted_text(text)
    body = _CJK_LATIN.sub(
        lambda m: f"{m.group(1)} {m.group(2)}"
        if m.group(1)
        else f"{m.group(3)} {m.group(4)}",
        body,
    )
    body = _SPACE_BEFORE_CN_PUNCT.sub(r"\1", body)
    body = _SPACE_AFTER_OPEN.sub(r"\1", body)
    body = _MULTI_SPACE.sub(" ", body)
    lines = [_MULTI_SPACE.sub(" ", line).strip() for line in body.splitlines()]
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if cleaned and not prev_blank:
                cleaned.append("")
            prev_blank = True
            continue
        cleaned.append(line)
        prev_blank = False
    cleaned = _merge_soft_wrap_lines(cleaned)
    cleaned = _inject_inferred_section_headers(cleaned)
    cleaned = _apply_markdown_structure(cleaned)
    final: list[str] = []
    prev_blank = False
    for line in cleaned:
        if not line.strip():
            if final and not prev_blank:
                final.append("")
            prev_blank = True
            continue
        final.append(line.strip())
        prev_blank = False
    return "\n".join(final).strip()


def normalize_resume_body(
    text: str,
    *,
    assessor: NormalizeAssessor | None = None,
    allow_rules_fallback: bool = True,
) -> tuple[str, str]:
    """规范化简历正文。默认 LLM；可注入；失败时可回退规则。

    返回 (正文, 方法标记：llm / rules_fallback / injected / rules)。
    """
    light = _light_pre_normalize(text)
    if not light:
        return "", "empty"
    if assessor is not None:
        out = _strip_md_fence(assessor(light)).strip()
        return out, "injected"
    try:
        return default_normalize_assessor(light), "llm"
    except ParseResumeError:
        if not allow_rules_fallback:
            raise
        return normalize_resume_body_rules(light), "rules_fallback"


_PROJECT_TITLE = re.compile(
    r"^项目([一二三四五六七八九十百\d]+)[：:．.\s].+"
)
_SECTION_H2 = re.compile(
    r"^(基本信息|基础信息|个人信息|专业技能|"
    r"工作经历|工作经验|项目经历|教育背景|教育经历|自我评价)$"
)
_SECTION_H4 = re.compile(
    r"^(业务背景与痛点|项目背景与痛点|项目技术栈与创新点|"
    r"项目职责与技术栈|项目成果|工作职责)[：:]?$"
)
_ORDERED_ITEM = re.compile(r"^(\d+)(?:[\.、．]\s*|\s+)(.+)$")
_UNORDERED_ITEM = re.compile(r"^[●•·▪◦]\s*(.+)$")
_ORDERED_FALSE_START = re.compile(r"^[个名位条项次人岁天月年]")
_SKILL_LINE_START = re.compile(r"^(熟练|精通|掌握|熟悉)")
_DUTY_LINE_START = re.compile(
    r"^(负责|搭建|利用|基于|持续|完成|参与|主导|协助|统筹|承接|"
    r"汇总|依托|使用|开展|跟进|支持|推动|设计|研究|探索|关注|结合)"
)
_JOB_HEAD_DATE_START = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月")


def _is_job_header_line(line: str) -> bool:
    """工作经历下的「公司 + 岗位 + 时间」行（日期在前或在后）。"""
    if line.startswith(("#", "-", "·")):
        return False
    if _UNORDERED_ITEM.match(line):
        return False
    if _SECTION_H4.fullmatch(line.rstrip("：:")):
        return False
    # 须先于有序列表判断：否则「2024 年9 月…公司」会被当成「2024. …」
    if _JOB_HEAD_DATE_START.match(line):
        return True
    return bool(_DATE_RANGE_TAIL.search(line) and 8 <= len(line) <= 100)


def _apply_markdown_structure(lines: list[str]) -> list[str]:
    """把常见章节/项目标题与列表改成 Markdown 标题与列表（不改字义）。"""
    out: list[str] = []
    in_results = False
    in_skills = False
    in_work = False
    need_work_duties_h4 = False
    in_work_duties = False
    work_duties_ordered = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue

        if _SECTION_H2.fullmatch(stripped):
            in_results = False
            in_skills = stripped == "专业技能"
            in_work = stripped in {"工作经历", "工作经验"}
            need_work_duties_h4 = False
            in_work_duties = False
            work_duties_ordered = False
            out.append(f"## {stripped}")
            continue
        if _PROJECT_TITLE.match(stripped) or _PROJECT_HEAD_LINE.match(stripped):
            in_results = False
            in_skills = False
            in_work = False
            need_work_duties_h4 = False
            in_work_duties = False
            work_duties_ordered = False
            out.append(f"### {stripped}")
            continue

        if in_work and _is_job_header_line(stripped):
            need_work_duties_h4 = True
            in_work_duties = False
            work_duties_ordered = False
            out.append(f"### {stripped}")
            continue

        h4 = _SECTION_H4.fullmatch(stripped)
        if h4:
            title = stripped.rstrip("：:")
            in_results = title == "项目成果"
            in_skills = False
            if title == "工作职责":
                need_work_duties_h4 = False
                in_work_duties = in_work
                work_duties_ordered = False
            out.append(f"#### {title}")
            continue

        # 工作条目缺「工作职责」小标题时按涂海鹏结构补上
        if in_work and need_work_duties_h4:
            out.append("#### 工作职责")
            need_work_duties_h4 = False
            in_work_duties = True
            work_duties_ordered = False

        ordered = _ORDERED_ITEM.match(stripped)
        if ordered:
            rest = ordered.group(2).strip()
            num = ordered.group(1)
            # 避免把「2021.09-2025.06 …」误当成有序列表而写成「2021. 09-…」
            if rest[:1].isdigit():
                out.append(stripped)
                continue
            # 避免把「5 个专职 Agent」类续行误当成有序列表
            if not _ORDERED_FALSE_START.match(rest):
                in_results = False
                if in_work_duties:
                    work_duties_ordered = True
                out.append(f"{num}. {rest}")
                continue

        unordered = _UNORDERED_ITEM.match(stripped)
        if unordered:
            out.append(f"- {unordered.group(1).strip()}")
            continue

        # 专业技能下无项目符号、以「熟练/精通…」起笔 → 无序列表
        if (
            in_skills
            and _SKILL_LINE_START.match(stripped)
            and not stripped.startswith(("-", "#"))
        ):
            out.append(f"- {stripped}")
            continue

        # 工作职责下无编号时的职责句（装饰圆点常不在文字层）→ 无序列表；
        # 若本段已是 1.2.3.，则后续短句视为编号续行，不改成 -
        if (
            in_work_duties
            and not work_duties_ordered
            and not stripped.startswith(("-", "#"))
            and (
                _DUTY_LINE_START.match(stripped)
                or stripped.endswith(("；", "。", ";", "."))
            )
        ):
            out.append(f"- {stripped}")
            continue

        # 项目成果下无项目符号、但以「标签：」起笔的连续结果行 → 无序列表
        if in_results and "：" in stripped and not stripped.startswith("#"):
            out.append(f"- {stripped}")
            continue

        out.append(stripped)
    return out


SYSTEM_PROMPT = """你是就业辅导场景的简历字段抽取员。只根据给定的规范化简历正文抽取结构化字段。
禁止：判断能不能投；评价排版；代写或改写原文语义；编造简历未出现的公司、项目、学校；打百分制。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全；没有则用 null 或空数组）
{
  "name": "姓名或 null",
  "gender": "性别或 null（可缺）",
  "hometown": "籍贯或 null",
  "age": "年龄字符串或 null；正文仅有出生年时可填（当年−出生年）",
  "target_role": "首页或基本信息中的岗位类表述（如智能体开发工程师）；没有则 null",
  "phone": "手机或 null",
  "email": "邮箱或 null",
  "skills": ["技能条目，尽量按原文列表拆分"],
  "work_experience": [
    {
      "start": "开始年月如 2024.07 或原文",
      "end": "结束年月或至今",
      "company": "公司名",
      "title": "岗位名",
      "duties": "职责摘要（可多句，勿扩写）"
    }
  ],
  "projects": [
    {
      "start": "开始年月",
      "end": "结束年月",
      "name": "项目名称",
      "role": "担任角色或 null",
      "content": "项目内容摘要（背景/职责/结果压缩，勿扩写）"
    }
  ],
  "education": [
    {
      "start": "开始年月或 null",
      "end": "结束年月或 null",
      "school": "学校",
      "major": "专业或 null",
      "degree": "学历或 null"
    }
  ],
  "self_evaluation": "自我评价原文或压缩摘要，没有则 null",
  "parse_incomplete": false,
  "parse_notes": ["抽取不确定处的简短说明，可空数组"]
}

## 规则
- target_role：只从基本信息 / 页眉 / 求职意向类位置取岗位类表述；不要用项目名冒充。
- 性别可缺；缺省不要编造。
- 年龄：正文写了年龄用原文；若只有出生年 / 出生年月（如 1995/07、出生年月：2001.06）而无年龄，按「检查当年公历年 − 出生年」填写（例：2026−1995→31）；不要用入学年冒充出生年。
- 时间尽量保留原文格式；无法识别则该字段 null，并将 parse_incomplete 设为 true。
- 一个都抽不出姓名且项目与工作皆空时，parse_incomplete 必须为 true。
"""

ParseAssessor = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class ParseResult:
    """一次规范化与字段抽取的落盘结果。"""

    run_dir: Path
    stem: str
    normalized_text: str
    fields: dict[str, Any]
    parse_incomplete: bool
    norm_md: Path
    parsed_md: Path
    parsed_json: Path
    normalize_method: str = "llm"


class ParseResumeError(Exception):
    """缺少简历文本、输入无效，或字段抽取失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ParseResumeError("LLM 返回为空，无法解析 json 结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ParseResumeError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ParseResumeError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from exc
    if not isinstance(data, dict):
        raise ParseResumeError("LLM 返回须为 JSON 对象。")
    return data


def _as_optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none", "无", "未知"}:
        return None
    return text


def _as_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for entry in raw:
        text = str(entry or "").strip()
        if text:
            items.append(text)
    return items


def _normalize_work(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        company = _as_optional_str(item.get("company"))
        if not company and not _as_optional_str(item.get("title")):
            continue
        result.append(
            {
                "start": _as_optional_str(item.get("start")),
                "end": _as_optional_str(item.get("end")),
                "company": company,
                "title": _as_optional_str(item.get("title")),
                "duties": _as_optional_str(item.get("duties")),
            }
        )
    return result


def _normalize_projects(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _as_optional_str(item.get("name"))
        if not name:
            continue
        result.append(
            {
                "start": _as_optional_str(item.get("start")),
                "end": _as_optional_str(item.get("end")),
                "name": name,
                "role": _as_optional_str(item.get("role")),
                "content": _as_optional_str(item.get("content")),
            }
        )
    return result


def _normalize_education(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        school = _as_optional_str(item.get("school"))
        if not school:
            continue
        result.append(
            {
                "start": _as_optional_str(item.get("start")),
                "end": _as_optional_str(item.get("end")),
                "school": school,
                "major": _as_optional_str(item.get("major")),
                "degree": _as_optional_str(item.get("degree")),
            }
        )
    return result


_BIRTH_LABELED = re.compile(
    r"(?:出生年月|出生日期|出生|生日)\s*[：:]\s*(19|20)(\d{2})"
    r"(?:\s*[./年]\s*\d{1,2})?"
)
_BIRTH_BARE = re.compile(r"(?<!\d)(19|20)(\d{2})\s*[./年]\s*\d{1,2}(?!\d)")


def infer_age_from_birth(
    text: str, *, today_year: int | None = None
) -> str | None:
    """正文无年龄、但有出生年时，用「当年 − 出生年」推算（按整年，不细算生日）。"""
    year_now = today_year if today_year is not None else date.today().year
    birth_year: int | None = None
    labeled = _BIRTH_LABELED.search(text)
    if labeled:
        birth_year = int(labeled.group(1) + labeled.group(2))
    else:
        # 页眉常见「1995/07 + 邮箱」；只看文首，避免把入学年当出生年
        head = "\n".join(text.splitlines()[:12])
        bare = _BIRTH_BARE.search(head)
        if bare:
            birth_year = int(bare.group(1) + bare.group(2))
    if birth_year is None:
        return None
    if birth_year < 1960 or birth_year > year_now - 16:
        return None
    return str(year_now - birth_year)


def normalize_parsed_fields(
    data: dict[str, Any],
    *,
    normalized_text: str | None = None,
    today_year: int | None = None,
) -> dict[str, Any]:
    """规整 LLM / 注入结果，保证下游字段稳定。"""
    fields = {
        "name": _as_optional_str(data.get("name")),
        "gender": _as_optional_str(data.get("gender")),
        "hometown": _as_optional_str(data.get("hometown")),
        "age": _as_optional_str(data.get("age")),
        "target_role": _as_optional_str(data.get("target_role")),
        "phone": _as_optional_str(data.get("phone")),
        "email": _as_optional_str(data.get("email")),
        "skills": _as_str_list(data.get("skills")),
        "work_experience": _normalize_work(data.get("work_experience")),
        "projects": _normalize_projects(data.get("projects")),
        "education": _normalize_education(data.get("education")),
        "self_evaluation": _as_optional_str(data.get("self_evaluation")),
        "parse_notes": _as_str_list(data.get("parse_notes")),
    }
    if not fields["age"] and normalized_text:
        inferred = infer_age_from_birth(
            normalized_text, today_year=today_year
        )
        if inferred:
            fields["age"] = inferred
            notes = list(fields["parse_notes"])
            notes.append(f"年龄由出生年推算为 {inferred}（当年−出生年）")
            fields["parse_notes"] = notes
    incomplete = bool(data.get("parse_incomplete"))
    if (
        not fields["name"]
        and not fields["work_experience"]
        and not fields["projects"]
    ):
        incomplete = True
    fields["parse_incomplete"] = incomplete
    return fields


def default_parse_assessor(normalized_text: str) -> dict[str, Any]:
    """把规范化正文发给 LLM，解析结构化字段。"""
    user_parts = [
        "以下是规范化后的简历正文。请抽取字段，不要扩写。",
        "",
        normalized_text,
        "",
        "请严格按系统说明只输出 json 对象。",
    ]
    base_user_text = "\n".join(user_parts)
    last_error: ParseResumeError | None = None
    for attempt, strategy in enumerate(ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=PARSE_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            return normalize_parsed_fields(
                parsed, normalized_text=normalized_text
            )
        except (LLMError, ParseResumeError) as exc:
            last_error = (
                exc
                if isinstance(exc, ParseResumeError)
                else ParseResumeError(str(exc))
            )
            if attempt < len(ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc
    raise ParseResumeError("字段抽取失败。")


def _markdown_report(*, fields: dict[str, Any], normalized_preview: str) -> str:
    lines = [
        "# 简历字段抽取",
        "",
        f"> {DISCLAIMER}",
        "",
        f"**抽取是否不完整**：{'是' if fields.get('parse_incomplete') else '否'}",
        "",
        "## 基本信息",
        "",
        f"- **姓名**：{fields.get('name') or '（未抽到）'}",
        f"- **性别**：{fields.get('gender') or '（未写 / 未抽到，不硬伤）'}",
        f"- **籍贯**：{fields.get('hometown') or '（未抽到）'}",
        f"- **年龄**：{fields.get('age') or '（未抽到）'}",
        f"- **岗位类表述**：{fields.get('target_role') or '（未抽到）'}",
        f"- **手机**：{fields.get('phone') or '（未抽到）'}",
        f"- **邮箱**：{fields.get('email') or '（未抽到）'}",
        "",
        "## 专业技能",
        "",
    ]
    skills = fields.get("skills") or []
    if skills:
        for skill in skills:
            lines.append(f"- {skill}")
    else:
        lines.append("- （未抽到）")
    lines.extend(["", "## 工作经历", ""])
    work = fields.get("work_experience") or []
    if work:
        for item in work:
            company = item.get("company") or "（公司未抽到）"
            title = item.get("title") or "（岗位未抽到）"
            lines.append(f"### {company} | {title}")
            lines.append("")
            start = item.get("start") or "?"
            end = item.get("end") or "?"
            lines.append(f"- **时间**：{start} – {end}")
            if item.get("duties"):
                lines.append(f"- **职责**：{item['duties']}")
            lines.append("")
    else:
        lines.append("（未抽到）")
        lines.append("")
    lines.extend(["## 项目经历", ""])
    projects = fields.get("projects") or []
    if projects:
        for item in projects:
            lines.append(f"### {item.get('name')}")
            lines.append("")
            start = item.get("start") or "?"
            end = item.get("end") or "?"
            role = item.get("role") or "（未抽到）"
            lines.append(f"- **时间**：{start} – {end}")
            lines.append(f"- **角色**：{role}")
            if item.get("content"):
                lines.append(f"- **内容**：{item['content']}")
            lines.append("")
    else:
        lines.append("（未抽到）")
        lines.append("")
    lines.extend(["## 教育背景", ""])
    education = fields.get("education") or []
    if education:
        for item in education:
            school = item.get("school")
            start = item.get("start") or "?"
            end = item.get("end") or "?"
            major = item.get("major") or "专业未抽到"
            degree = item.get("degree") or "学历未抽到"
            lines.append(f"- {school}（{start} – {end}；{major}；{degree}）")
    else:
        lines.append("- （未抽到）")
    lines.extend(
        [
            "",
            "## 自我评价",
            "",
            fields.get("self_evaluation") or "（未抽到）",
            "",
        ]
    )
    notes = fields.get("parse_notes") or []
    if notes:
        lines.extend(["## 抽取备注", ""])
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    preview = normalized_preview.strip()
    if len(preview) > 800:
        preview = preview[:800] + "…"
    lines.extend(
        [
            "## 规范化正文摘录",
            "",
            "```text",
            preview,
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def parse_resume(
    source: Path,
    *,
    root: Path | None = None,
    normalize_assessor: NormalizeAssessor | None = None,
    parse_assessor: ParseAssessor | None = None,
    allow_rules_fallback: bool = True,
) -> ParseResult:
    """规范化简历文本并抽取字段，写出 norm / parsed 产物。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise ParseResumeError(str(exc)) from exc
    if not body.strip():
        raise ParseResumeError("简历正文为空，无法规范化或抽取字段。")

    normalized, normalize_method = normalize_resume_body(
        body,
        assessor=normalize_assessor,
        allow_rules_fallback=allow_rules_fallback,
    )
    if not normalized.strip():
        raise ParseResumeError("规范化后正文为空，无法抽取字段。")

    assessor = parse_assessor or default_parse_assessor
    raw_fields = assessor(normalized)
    fields = normalize_parsed_fields(
        raw_fields, normalized_text=normalized
    )

    norm_md = run_dir / f"{stem}.resume.norm.md"
    parsed_md = run_dir / f"{stem}.parsed.md"
    parsed_json = run_dir / f"{stem}.parsed.json"

    if normalize_method == "llm":
        how = "经大模型整理结构与折行"
    elif normalize_method == "rules_fallback":
        how = "在大模型失败后回退规则整理"
    elif normalize_method == "injected":
        how = "经注入的规范化器整理"
    else:
        how = "经规则整理"
    norm_body = "\n".join(
        [
            "# 简历文本（规范化）",
            "",
            f"> 由 `parse-resume` {how}（空格 / 章节 / 列表等）。"
            "**不改字义**；不是页图查排版，也不判断能不能投。",
            "",
            normalized,
            "",
        ]
    )
    norm_md.write_text(norm_body, encoding="utf-8")
    parsed_md.write_text(
        _markdown_report(fields=fields, normalized_preview=normalized),
        encoding="utf-8",
    )

    record = {
        "tool": "parse-resume",
        "judges_content": False,
        "evaluates_layout": False,
        "not_in_resume_by_default": True,
        "disclaimer": DISCLAIMER,
        "standard": "docs/01-product/001_prd_就业守护助手产品说明.md#47a",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "normalized_sha256": _sha256_text(normalized),
        "parse_incomplete": fields["parse_incomplete"],
        "fields": fields,
        "artifacts": {
            "norm_md": norm_md.name,
            "parsed_md": parsed_md.name,
            "parsed_json": parsed_json.name,
        },
        "method": {
            "normalize": normalize_method,
            "fields": "llm" if parse_assessor is None else "injected",
        },
    }
    parsed_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ParseResult(
        run_dir=run_dir,
        stem=stem,
        normalized_text=normalized,
        fields=fields,
        parse_incomplete=bool(fields["parse_incomplete"]),
        norm_md=norm_md,
        parsed_md=parsed_md,
        parsed_json=parsed_json,
        normalize_method=normalize_method,
    )
