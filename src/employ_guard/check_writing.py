"""查文字表达。对照 docs/04-standard/003 §3.5；不判能不能投。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from employ_guard.judge_resume import JudgeResumeError, resolve_resume_text
from employ_guard.llm import LLMError, chat_completion

WRITING_CATEGORIES = ("W1", "W2", "W3", "W4")
CATEGORY_LABELS = {
    "W1": "错别字",
    "W2": "列表标点一致",
    "W3": "中文标点",
    "W4": "用语专业",
}
LIST_ITEM_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,2}[\.\)、]\s*"
    r"|[•·●○▪▫\-*]\s"
    r"|\([0-9]+\)\s*"
    r"|[（(][0-9]+[）)]\s*"
    r")"
)
SKILL_TEMPLATE_RE = re.compile(r"熟练(?:掌握|使用|运用|英语)")
SKILL_CATEGORY_RE = re.compile(
    r"(?:编程语言|模型与算法|AI\s*框架[/／]?模型|框架[/／]模型|"
    r"数据库[/／与]?工具|业务领域|工程与部署|评测与安全)"
    r"|(?:^|\n)\s*(?:语言|框架|工具|模型)[：:]"
)
SKILL_SECTION_START = re.compile(r"专业技能|个人优势|相关技能|技能清单")
SKILL_SECTION_END = re.compile(r"工作经历|工作经验|项目经历|教育背景|教育经历")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
DECIMAL_RE = re.compile(r"\d+\.\d+")
EN_PUNCT_RE = re.compile(r"[,\.;:!\?]")
WRITING_MAX_TOKENS = 4096
WRITING_ATTEMPT_STRATEGIES: tuple[dict[str, object], ...] = (
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

SYSTEM_PROMPT = """你是简历文字表达校对员。只检查错别字与用语是否专业，不评价项目够不够投、不评排版。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "typos": [
    {"line": 12, "excerpt": "原文短句", "suggestion": "建议写法", "note": "说明"}
  ],
  "colloquial": [
    {"line": 20, "excerpt": "原文短句", "issue": "问题类型", "suggestion": "改写方向", "note": "说明"}
  ]
}

## W1 错别字
- 列出疑似错字、别字、输入法联想错误；专有名词（LangGraph、Milvus、DeepSeek 等）与常见技术栈不要误报。
- 没有问题时 typos 为空数组。

## W4 用语专业（对齐 003 §3.3 S9、§3.5 W4）
- 口语、网络梗（如「全家桶」「大礼包」）、过度主观或空泛词（熟悉、了解、参与、负责推进、精通一切）。
- 技能区连续多条以「熟练掌握 / 熟练使用 / 熟练运用」起笔、且未按类别分组 → 写入 colloquial：建议按「编程语言 / 模型与算法 / AI 框架 / 数据库与工具 / 业务领域」等分组，并改成可核实表述，避免满篇同一句式。
- 职责写成技能名词堆砌、或单条职责过长像说明书 → 写入 colloquial，建议改成「痛点 → 做法 → 结果」短要点。
- 个人优势里非目标方向栈（如大段 Java / 前端）压过 RAG/Agent/大模型 → 建议弱化并前置大模型相关。
- 建议改为动词开头的可核实表述；对事不对人。
- 没有问题时 colloquial 为空数组。

line 为简历正文行号（从 1 起）；excerpt 须摘自原文且尽量短。
"""

WritingAssessor = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class WritingResult:
    """一次查文字表达的落盘结果。"""

    run_dir: Path
    writing_pass: bool
    findings: list[dict[str, Any]]
    report_md: Path
    report_json: Path


class CheckWritingError(Exception):
    """缺少简历文本、输入无效，或查文字表达失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chinese_char_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _mask_spans(text: str, pattern: re.Pattern[str]) -> str:
    chars = list(text)
    for match in pattern.finditer(text):
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def _is_ascii_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in {"_", "-", "/", "+", "#"})


def _is_between_ascii_terms(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text) - 1:
        return False
    left = text[index - 1]
    right = text[index + 1]
    if not (_is_ascii_word_char(left) or left.isspace()):
        return False
    if not (_is_ascii_word_char(right) or right.isspace()):
        return False
    start = index - 1
    while start >= 0 and (_is_ascii_word_char(text[start]) or text[start].isspace()):
        start -= 1
    end = index + 1
    while end < len(text) and (_is_ascii_word_char(text[end]) or text[end].isspace()):
        end += 1
    segment = text[start + 1 : end]
    return _chinese_char_count(segment) == 0


def _trailing_punct(line: str) -> str | None:
    stripped = line.rstrip()
    if not stripped:
        return None
    last = stripped[-1]
    if last in "。；，、":
        return last
    if last in ",.;:":
        return last
    return None


def _list_punct_consistent(endings: list[str | None]) -> tuple[bool, str]:
    normalized = [item for item in endings if item is not None]
    if not normalized:
        return True, ""
    if all(item is None for item in endings):
        return True, ""
    if any(item is None for item in endings):
        return False, "同一列表内有的条目有句末标点、有的没有，须统一。"
    unique = set(normalized)
    if unique == {"。"}:
        return True, ""
    if unique == {"；"}:
        return True, ""
    if unique == {"，"}:
        return False, "列表条目句末宜使用分号或句号，不宜混用逗号。"
    if normalized[:-1] == ["；"] * (len(normalized) - 1) and normalized[-1] == "。":
        return True, ""
    if "；" in normalized and "。" in normalized:
        return False, "分号与句号混用：前若干条用分号时，仅最后一条可用句号。"
    if len(unique) > 1:
        return False, f"句末标点不一致（{ '、'.join(sorted(unique)) }），同一列表须统一。"
    return True, ""


def _group_list_blocks(lines: list[str]) -> list[tuple[int, list[tuple[int, str]]]]:
    blocks: list[tuple[int, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] = []
    current_start = 0

    def flush() -> None:
        nonlocal current, current_start
        if len(current) >= 2:
            blocks.append((current_start, list(current)))
        current = []

    for line_no, line in enumerate(lines, start=1):
        if LIST_ITEM_RE.match(line):
            if not current:
                current_start = line_no
            current.append((line_no, line))
            continue
        flush()
    flush()
    return blocks


def rule_check_list_punctuation(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for start_line, block in _group_list_blocks(lines):
        endings = [_trailing_punct(line) for _, line in block]
        ok, note = _list_punct_consistent(endings)
        if ok:
            continue
        first_line, first_text = block[0]
        findings.append(
            {
                "id": "W2",
                "category": "列表标点一致",
                "line": first_line,
                "excerpt": first_text.strip()[:80],
                "note": note,
                "method": "rule",
                "block_start": start_line,
            }
        )
    return findings


def rule_check_long_blocks(lines: list[str]) -> list[dict[str, Any]]:
    """超长段落或超长编号职责 → 建议拆成项目符号短句。"""
    findings: list[dict[str, Any]] = []
    buffer: list[str] = []
    start_line = 1

    def flush() -> None:
        nonlocal buffer, start_line
        if not buffer:
            return
        text = "".join(part.strip() for part in buffer)
        chars = _chinese_char_count(text)
        first = buffer[0]
        is_list = bool(LIST_ITEM_RE.match(first))
        threshold = 100 if is_list else 120
        if chars >= threshold:
            findings.append(
                {
                    "id": "W4",
                    "category": "用语专业",
                    "line": start_line,
                    "excerpt": first.strip()[:80],
                    "note": (
                        "该段过长，扫读成本高；宜拆成项目符号（•）短句，"
                        "每条只写一个可核实动作或结果。"
                        if is_list
                        else "长段落信息密度过高；宜拆成项目符号短句，突出痛点、做法与结果。"
                    ),
                    "suggestion": "改为 • 短要点，删掉重复技术堆砌。",
                    "method": "rule",
                }
            )
        buffer = []

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            flush()
            continue
        if not buffer:
            start_line = line_no
            buffer = [line]
            continue
        # 新的列表项或明显新段标题，先结算
        if LIST_ITEM_RE.match(line) or (
            len(line.strip()) <= 20
            and _chinese_char_count(line) >= 2
            and not line.strip().endswith(("。", "；", "，", ",", ";", "."))
        ):
            flush()
            start_line = line_no
            buffer = [line]
            continue
        buffer.append(line)
    flush()
    return findings


def _skill_section_span(lines: list[str]) -> tuple[int, int] | None:
    """返回技能相关段落的行号区间（含起不含止）；找不到则 None。"""
    start: int | None = None
    for index, line in enumerate(lines):
        if SKILL_SECTION_START.search(line):
            start = index
            break
    if start is None:
        # 首页直接以「熟练」技能条开场时，取文首到工作/项目前
        for index, line in enumerate(lines):
            if SKILL_TEMPLATE_RE.search(line):
                start = index
                break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if SKILL_SECTION_END.search(lines[index]):
            end = index
            break
    return start, end


def rule_check_skill_templates(lines: list[str]) -> list[dict[str, Any]]:
    """技能区满篇「熟练××」且未分组 → W4 改稿建议。"""
    span = _skill_section_span(lines)
    if span is None:
        return []
    start, end = span
    section = lines[start:end]
    template_hits: list[tuple[int, str]] = []
    for offset, line in enumerate(section):
        if SKILL_TEMPLATE_RE.search(line):
            template_hits.append((start + offset + 1, line.strip()[:80]))
    if len(template_hits) < 5:
        return []

    has_category = any(SKILL_CATEGORY_RE.search(line) for line in section)
    first_line, first_excerpt = template_hits[0]
    findings: list[dict[str, Any]] = [
        {
            "id": "W4",
            "category": "用语专业",
            "line": first_line,
            "excerpt": first_excerpt,
            "note": (
                f"技能区连续 {len(template_hits)} 条以「熟练掌握 / 使用 / 运用」起笔，句式单调；"
                "宜改成可核实表述，并适当变换话术。"
            ),
            "suggestion": "少用「熟练××」模板，按实际用法写短句。",
            "method": "rule",
        }
    ]
    if not has_category:
        findings.append(
            {
                "id": "W4",
                "category": "用语专业",
                "line": first_line,
                "excerpt": first_excerpt,
                "note": (
                    "技能点偏散、未见分类小标题；宜按编程语言、模型与算法、"
                    "AI 框架、数据库与工具、业务领域等分组总结。"
                ),
                "suggestion": "增加分组小标题后再列要点。",
                "method": "rule",
            }
        )
    return findings


def rule_check_english_punctuation(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    punct_names = {
        ",": "半角逗号",
        ".": "半角句号",
        ";": "半角分号",
        ":": "半角冒号",
        "!": "半角叹号",
        "?": "半角问号",
    }
    for line_no, line in enumerate(lines, start=1):
        if _chinese_char_count(line) < 2:
            continue
        masked = _mask_spans(line, EMAIL_RE)
        masked = _mask_spans(masked, URL_RE)
        masked = _mask_spans(masked, DECIMAL_RE)
        for match in EN_PUNCT_RE.finditer(masked):
            if _is_between_ascii_terms(masked, match.start()):
                continue
            punct = match.group()
            findings.append(
                {
                    "id": "W3",
                    "category": "中文标点",
                    "line": line_no,
                    "excerpt": line.strip()[:80],
                    "note": f"中文叙述中不宜使用{punct_names.get(punct, '半角标点')}「{punct}」，宜改用中文全角标点。",
                    "method": "rule",
                }
            )
    return findings


def _parse_llm_writing_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise CheckWritingError("LLM 返回为空，无法解析文字表达结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(stripped[start : end + 1])
        else:
            raise CheckWritingError(f"LLM 结果不是合法 JSON：{stripped[:400]}") from None
    if not isinstance(data, dict):
        raise CheckWritingError("LLM 结果须为 JSON 对象。")
    return data


def _should_skip_llm_finding(note: str) -> bool:
    skip_markers = ("无需修改", "不是错别字", "无需改动", "正确，无需", "不应误报")
    return any(marker in note for marker in skip_markers)


def _normalize_llm_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in data.get("typos") or []:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        if not excerpt:
            continue
        note = str(item.get("note") or item.get("suggestion") or "疑似错别字").strip()
        if _should_skip_llm_finding(note):
            continue
        findings.append(
            {
                "id": "W1",
                "category": "错别字",
                "line": int(item["line"]) if str(item.get("line", "")).isdigit() else None,
                "excerpt": excerpt[:120],
                "note": note,
                "suggestion": str(item.get("suggestion") or "").strip(),
                "method": "llm",
            }
        )
    for item in data.get("colloquial") or []:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        if not excerpt:
            continue
        issue = str(item.get("issue") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        note_parts = [part for part in (issue, suggestion, str(item.get("note") or "")) if part]
        note = "；".join(note_parts) if note_parts else "用语宜更专业、可核实"
        if _should_skip_llm_finding(note):
            continue
        findings.append(
            {
                "id": "W4",
                "category": "用语专业",
                "line": int(item["line"]) if str(item.get("line", "")).isdigit() else None,
                "excerpt": excerpt[:120],
                "note": note,
                "suggestion": suggestion,
                "method": "llm",
            }
        )
    return findings


def default_writing_assessor(resume_text: str) -> dict[str, Any]:
    """把简历正文发给 LLM，解析 W1 / W4。"""
    user_text = "\n".join(
        [
            "以下是简历正文（行号已标注，供你填写 line 字段）。",
            "",
            *_numbered_lines(resume_text),
            "",
            "请严格按系统说明只输出 json 对象。",
        ]
    )
    last_error: CheckWritingError | None = None
    for attempt, strategy in enumerate(WRITING_ATTEMPT_STRATEGIES, start=1):
        suffix = str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text + suffix,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=WRITING_MAX_TOKENS,
            )
            parsed = _parse_llm_writing_payload(text)
            return {"llm_findings": _normalize_llm_findings(parsed)}
        except (LLMError, CheckWritingError, json.JSONDecodeError) as exc:
            last_error = (
                exc
                if isinstance(exc, CheckWritingError)
                else CheckWritingError(str(exc))
            )
            if attempt < len(WRITING_ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc
    raise CheckWritingError("查文字表达失败。")


def _numbered_lines(text: str) -> list[str]:
    return [f"{index:04d} | {line}" for index, line in enumerate(text.splitlines(), start=1)]


def _markdown_report(*, writing_pass: bool, findings: list[dict[str, Any]]) -> str:
    verdict = "未发现明显文字问题" if writing_pass else "有待改进的文字表达项"
    lines = [
        "# 查文字表达",
        "",
        "> 本文件由 `check-writing` 根据简历文本生成。对照 003 §3.5；**不替代「判能不能投」**。",
        "",
        f"**结论**：{verdict}",
        "",
    ]
    if not findings:
        lines.append("未发现 W1～W4 范围内的明显问题。")
        lines.append("")
        return "\n".join(lines)

    by_category: dict[str, list[dict[str, Any]]] = {code: [] for code in WRITING_CATEGORIES}
    for item in findings:
        by_category.setdefault(str(item.get("id")), []).append(item)

    for code in WRITING_CATEGORIES:
        items = by_category.get(code) or []
        if not items:
            continue
        lines.extend([f"## {code} {CATEGORY_LABELS[code]}", ""])
        for item in items:
            line_no = item.get("line")
            location = f"第 {line_no} 行" if line_no else "位置未标注"
            excerpt = str(item.get("excerpt") or "").replace("|", "\\|")
            note = str(item.get("note") or "").replace("|", "\\|")
            method = str(item.get("method") or "")
            lines.append(f"- **{location}**（{method}）：{excerpt}")
            lines.append(f"  - {note}")
            suggestion = str(item.get("suggestion") or "").strip()
            if suggestion and suggestion not in note:
                lines.append(f"  - 建议：{suggestion}")
        lines.append("")
    return "\n".join(lines)


def check_writing(
    source: Path,
    *,
    root: Path | None = None,
    writing_assessor: WritingAssessor | None = None,
) -> WritingResult:
    """查文字表达，写出 `{stem}.writing.md` / `{stem}.writing.json`。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise CheckWritingError(str(exc)) from exc
    if not body.strip():
        raise CheckWritingError("简历正文为空，无法查文字表达。")

    lines = body.splitlines()
    findings = (
        rule_check_list_punctuation(lines)
        + rule_check_english_punctuation(lines)
        + rule_check_long_blocks(lines)
        + rule_check_skill_templates(lines)
    )

    assessor = writing_assessor or default_writing_assessor
    assessed = assessor(body)
    findings.extend(list(assessed.get("llm_findings") or []))

    writing_pass = len(findings) == 0
    md_name = f"{stem}.writing.md"
    json_name = f"{stem}.writing.json"
    report_md = run_dir / md_name
    report_json = run_dir / json_name
    record = {
        "tool": "check-writing",
        "evaluates_content_bar": False,
        "evaluates_layout": False,
        "writing_pass": writing_pass,
        "standard": "docs/04-standard/003_resume-standard_简历书写标准.md#35",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "findings": findings,
        "summary": {
            code: sum(1 for item in findings if item.get("id") == code)
            for code in WRITING_CATEGORIES
        },
        "method": {
            "W2_W3_long": "rule",
            "W1_W4": "llm" if writing_assessor is None else "injected",
        },
    }
    report_md.write_text(
        _markdown_report(writing_pass=writing_pass, findings=findings),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return WritingResult(
        run_dir=run_dir,
        writing_pass=writing_pass,
        findings=findings,
        report_md=report_md,
        report_json=report_json,
    )
