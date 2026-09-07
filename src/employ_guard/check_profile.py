"""基础信息检测。对照 003 S1 / 004 C1；不替代整份判能不能投。"""

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

DISCLAIMER = (
    "本文件由 `check-profile` 对照首页岗位类表述（003 S1 / 004 C1）做分项检查。"
    "不替代整份「判能不能投」，也不评价排版；缺性别等不构成硬伤。"
    "本项未过不等于已写入不能投结论，须由老师或后续 judge 汇总。"
)
PROFILE_MAX_TOKENS = 2048
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

SYSTEM_PROMPT = """你是就业辅导场景的「基础信息 / 首页岗位类表述」检查员。
只根据给定正文（及可选的已抽取字段）判断：首页基本信息里是否能扫到岗位类表述。
禁止：评价排版；代写简历；打百分制；把本项写成整份「能不能投」终裁；因缺性别/籍贯判硬伤。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 书面口径（003 S1 / 004 C1）
- **必须**：首页基本信息（页眉 / 基本信息区 / 求职意向行附近）出现岗位类表述，例如：智能体开发工程师、算法工程师、大模型应用工程师、大模型工程师、NLP 算法工程师。
- **不强制**出现「应聘岗位」「求职意向」这几个字。
- 仅靠项目经历或技能堆砌反推、首页看不出投什么岗 → **未过本项**（status=fail）。
- 有岗位类表述，但与全文主业明显拧着（例如首页写前端、正文全是 RAG/Agent）→ **存疑**（status=doubtful）。
- 有清晰岗位类表述且与主业大致同向 → **过本项**（status=pass）。

## 输出字段（必须齐全）
{
  "status": "pass|fail|doubtful",
  "target_role": "从首页读到的岗位类表述；没有则 null",
  "homepage_evidence": "一两句：依据哪一行/哪一段判断（摘原文短句）",
  "conflict_note": "若 doubtful，说明与主业如何拧着；否则 null",
  "fixes": ["可执行改法，最多 2 条；对事不对人"],
  "notes": ["其它简短说明，可空数组"]
}
"""

ProfileAssessor = Callable[[str, dict[str, Any] | None], dict[str, Any]]

_STATUS_ZH = {
    "pass": "过本项",
    "fail": "未过本项",
    "doubtful": "存疑",
}


@dataclass(frozen=True)
class ProfileResult:
    """一次基础信息检测的落盘结果。"""

    run_dir: Path
    stem: str
    status: str
    target_role: str | None
    report_md: Path
    report_json: Path


class CheckProfileError(Exception):
    """缺少简历文本、输入无效，或基础信息检测失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise CheckProfileError("LLM 返回为空，无法解析 json 结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise CheckProfileError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise CheckProfileError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from exc
    if not isinstance(data, dict):
        raise CheckProfileError("LLM 返回须为 JSON 对象。")
    return data


def _as_optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none", "无", "未知"}:
        return None
    return text


def _as_str_list(raw: Any, *, limit: int = 2) -> list[str]:
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for entry in raw:
        text = str(entry or "").strip()
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def normalize_profile_fields(data: dict[str, Any]) -> dict[str, Any]:
    """规整 LLM / 注入结果。"""
    status = str(data.get("status") or "").strip().lower()
    if status not in {"pass", "fail", "doubtful"}:
        status = "fail"
    fields = {
        "status": status,
        "target_role": _as_optional_str(data.get("target_role")),
        "homepage_evidence": _as_optional_str(data.get("homepage_evidence"))
        or "（未写明依据）",
        "conflict_note": _as_optional_str(data.get("conflict_note")),
        "fixes": _as_str_list(data.get("fixes"), limit=2),
        "notes": _as_str_list(data.get("notes"), limit=5),
    }
    if fields["status"] == "pass":
        fields["conflict_note"] = None
    if fields["status"] == "fail" and not fields["fixes"]:
        fields["fixes"] = [
            "在首页基本信息写明岗位类表述（如大模型应用工程师），勿只靠项目反推。"
        ]
    return fields


def _load_parsed_hint(run_dir: Path, stem: str) -> dict[str, Any] | None:
    path = run_dir / f"{stem}.parsed.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fields = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        return None
    return {
        "name": fields.get("name"),
        "target_role": fields.get("target_role"),
        "parse_incomplete": fields.get("parse_incomplete"),
    }


def _prefer_normalized_body(run_dir: Path, stem: str, fallback: str) -> str:
    norm = run_dir / f"{stem}.resume.norm.md"
    if not norm.is_file():
        return fallback
    raw = norm.read_text(encoding="utf-8")
    # 去掉本仓产物说明头
    lines = raw.splitlines()
    if lines and lines[0].startswith("#"):
        body_lines: list[str] = []
        started = False
        for line in lines[1:]:
            if not started and line.strip().startswith(">"):
                continue
            if not started and not line.strip():
                continue
            started = True
            body_lines.append(line)
        text = "\n".join(body_lines).strip()
        return text or fallback
    return raw.strip() or fallback


def default_profile_assessor(
    resume_text: str, parsed_hint: dict[str, Any] | None
) -> dict[str, Any]:
    """把首页相关正文发给 LLM，解析 C1 分项结果。"""
    user_parts = [
        "以下是简历正文（优先已规范化）。请只检查首页基本信息是否有岗位类表述。",
        "",
        resume_text,
        "",
    ]
    if parsed_hint:
        user_parts.extend(
            [
                "已抽取字段（仅供参考；仍以首页正文为准，勿用项目名冒充岗位）：",
                json.dumps(parsed_hint, ensure_ascii=False),
                "",
            ]
        )
    user_parts.append("请严格按系统说明只输出 json 对象。")
    base_user_text = "\n".join(user_parts)
    last_error: CheckProfileError | None = None
    for attempt, strategy in enumerate(ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=PROFILE_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            return normalize_profile_fields(parsed)
        except (LLMError, CheckProfileError) as exc:
            last_error = (
                exc
                if isinstance(exc, CheckProfileError)
                else CheckProfileError(str(exc))
            )
            if attempt < len(ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc
    raise CheckProfileError("基础信息检测失败。")


def _markdown_report(fields: dict[str, Any]) -> str:
    status = str(fields.get("status") or "fail")
    lines = [
        "# 基础信息检测",
        "",
        f"> {DISCLAIMER}",
        "",
        f"**本项结论**：{_STATUS_ZH.get(status, status)}（`{status}`）",
        "",
        "## 岗位类表述",
        "",
        f"- **读到的表述**：{fields.get('target_role') or '（未抽到 / 首页未见）'}",
        f"- **依据**：{fields.get('homepage_evidence')}",
        "",
    ]
    if fields.get("conflict_note"):
        lines.extend(["## 与主业是否拧着", "", fields["conflict_note"], ""])
    fixes = fields.get("fixes") or []
    if fixes:
        lines.extend(["## 可改", ""])
        for fix in fixes:
            lines.append(f"- {fix}")
        lines.append("")
    notes = fields.get("notes") or []
    if notes:
        lines.extend(["## 备注", ""])
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(
        [
            "## 说明",
            "",
            "- 本报告只对应合格线 **C1 / 书写标准 S1**。",
            "- 整份能不能投仍以 `judge-resume`（及后续汇总）为准。",
            "",
        ]
    )
    return "\n".join(lines)


def check_profile(
    source: Path,
    *,
    root: Path | None = None,
    profile_assessor: ProfileAssessor | None = None,
) -> ProfileResult:
    """检查首页岗位类表述，写出 profile 产物。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise CheckProfileError(str(exc)) from exc
    if not body.strip():
        raise CheckProfileError("简历正文为空，无法做基础信息检测。")

    text_for_check = _prefer_normalized_body(run_dir, stem, body)
    parsed_hint = _load_parsed_hint(run_dir, stem)

    assessor = profile_assessor or default_profile_assessor
    raw = assessor(text_for_check, parsed_hint)
    fields = normalize_profile_fields(raw)

    report_md = run_dir / f"{stem}.profile.md"
    report_json = run_dir / f"{stem}.profile.json"
    report_md.write_text(_markdown_report(fields), encoding="utf-8")

    record = {
        "tool": "check-profile",
        "judges_content": False,
        "evaluates_layout": False,
        "not_in_resume_by_default": True,
        "disclaimer": DISCLAIMER,
        "standard": "docs/04-standard/004_resume-bar_简历合格线.md#C1",
        "input": source_label,
        "text_sha256": _sha256_text(text_for_check),
        "used_normalized": (run_dir / f"{stem}.resume.norm.md").is_file(),
        "used_parsed_hint": parsed_hint is not None,
        "status": fields["status"],
        "target_role": fields["target_role"],
        "homepage_evidence": fields["homepage_evidence"],
        "conflict_note": fields["conflict_note"],
        "fixes": fields["fixes"],
        "notes": fields["notes"],
        "artifacts": {
            "profile_md": report_md.name,
            "profile_json": report_json.name,
        },
        "method": {
            "profile": "llm" if profile_assessor is None else "injected",
        },
    }
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ProfileResult(
        run_dir=run_dir,
        stem=stem,
        status=fields["status"],
        target_role=fields["target_role"],
        report_md=report_md,
        report_json=report_json,
    )
