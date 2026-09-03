"""出练习题。对照产品说明 §4.6；不判能不能投，不评排版。"""

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

DEFAULT_SCOPE = "通用技术面，不是某家公司的真题"
DISCLAIMER = (
    "下列题目由工具根据简历文本推测，仅供学员练习。"
    "不是某家公司的面试真题，也不能替代「判能不能投」。"
)
QUESTIONS_MAX_TOKENS = 4096
QUESTIONS_ATTEMPT_STRATEGIES: tuple[dict[str, object], ...] = (
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

SYSTEM_PROMPT = """你是就业辅导场景的「练习题」出题员。只根据简历文本推测面试可能追问什么，供学员练习。
禁止：判断能不能投；评价排版；写成某企业真题或「某司一面原题」；代写标准答案长文；打百分制分数。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用技术面，不是某家公司的真题）",
  "questions": [
    {
      "id": "Q1",
      "category": "项目深挖|RAG链路|Agent工具|评测指标|工程化|其它",
      "question": "完整中文问句",
      "why": "依据简历哪一点作出推测（一两句人话）",
      "focus": "练习时建议练什么（可追问口径、因果链、数字推演等）"
    }
  ]
}

## 出题规则
- 产出 **6～8** 道题；优先覆盖简历里写到的主项目、RAG/Agent、评测数字、工程落地。
- 每题必须能在简历中找到依据；写不清依据的不要出。
- 题干用完整问句；对事不对人；可追问、可练习，避免空泛「谈谈你的优势」。
- 若提供了目标岗位说明，题目可略向该方向靠拢，但仍须标明不是该公司真题。
- 无岗位说明时，scope 必须写明通用技术面、不是某家公司的真题。
- category 只能从：项目深挖、RAG链路、Agent工具、评测指标、工程化、其它。
"""

QuestionsAssessor = Callable[[str, str | None], dict[str, Any]]


@dataclass(frozen=True)
class QuestionsResult:
    """一次出练习题的落盘结果。"""

    run_dir: Path
    scope: str
    questions: list[dict[str, Any]]
    report_md: Path
    report_json: Path


class DraftQuestionsError(Exception):
    """缺少简历文本、输入无效，或出练习题失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise DraftQuestionsError("LLM 返回为空，无法解析 json 结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise DraftQuestionsError(f"LLM 返回不是合法 JSON：{stripped[:400]}") from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DraftQuestionsError(f"LLM 返回不是合法 JSON：{stripped[:400]}") from exc
    if not isinstance(data, dict):
        raise DraftQuestionsError("LLM 返回须为 JSON 对象。")
    return data


def _normalize_questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("questions") or []
    if not isinstance(raw, list):
        return []
    allowed = {"项目深挖", "RAG链路", "Agent工具", "评测指标", "工程化", "其它"}
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        category = str(item.get("category") or "其它").strip()
        if category not in allowed:
            category = "其它"
        qid = str(item.get("id") or f"Q{index}").strip() or f"Q{index}"
        result.append(
            {
                "id": qid,
                "category": category,
                "question": question,
                "why": str(item.get("why") or "").strip() or "未说明依据。",
                "focus": str(item.get("focus") or "").strip() or "把因果链与可追问口径讲清楚。",
                "method": "llm",
            }
        )
    return result


def default_questions_assessor(
    resume_text: str,
    job_description: str | None,
) -> dict[str, Any]:
    """把简历文本发给 LLM，解析练习题列表。"""
    user_parts = [
        "以下是简历正文。",
        "",
        resume_text,
        "",
    ]
    if job_description and job_description.strip():
        user_parts.extend(
            [
                "目标岗位说明（可略向该方向出题，但仍须标明不是该公司真题）：",
                job_description.strip(),
                "",
            ]
        )
    else:
        user_parts.append(
            "未提供目标岗位说明；scope 须标明通用技术面、不是某家公司的真题。"
        )
    user_parts.append("请严格按系统说明只输出 json 对象。")
    base_user_text = "\n".join(user_parts)

    last_error: DraftQuestionsError | None = None
    for attempt, strategy in enumerate(QUESTIONS_ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=QUESTIONS_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            questions = _normalize_questions(parsed)
            if not questions:
                raise DraftQuestionsError("模型未返回可用练习题。")
            scope = str(parsed.get("scope") or "").strip() or DEFAULT_SCOPE
            if not job_description or not str(job_description).strip():
                if "真题" not in scope:
                    scope = DEFAULT_SCOPE
            return {"scope": scope, "questions": questions}
        except (LLMError, DraftQuestionsError) as exc:
            last_error = (
                exc
                if isinstance(exc, DraftQuestionsError)
                else DraftQuestionsError(str(exc))
            )
            if attempt < len(QUESTIONS_ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc

    raise DraftQuestionsError("出练习题失败。")


def _markdown_report(*, scope: str, questions: list[dict[str, Any]]) -> str:
    lines = [
        "# 练习题（推测）",
        "",
        "> 本文件由 `draft-questions` 根据简历文本生成。**仅供练习**，不是某家公司的真题；不替代「判能不能投」。",
        "",
        f"**范围**：{scope}",
        "",
        f"**声明**：{DISCLAIMER}",
        "",
        "## 题目",
        "",
    ]
    for item in questions:
        lines.append(f"### {item['id']} · {item['category']}")
        lines.append("")
        lines.append(str(item["question"]))
        lines.append("")
        lines.append(f"- **推测依据**：{item['why']}")
        lines.append(f"- **练习重点**：{item['focus']}")
        lines.append("")
    return "\n".join(lines)


def draft_questions(
    source: Path,
    *,
    job_description: str | None = None,
    root: Path | None = None,
    questions_assessor: QuestionsAssessor | None = None,
) -> QuestionsResult:
    """出练习题，写出 `{stem}.questions.md` / `{stem}.questions.json`。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise DraftQuestionsError(str(exc)) from exc
    if not body.strip():
        raise DraftQuestionsError("简历正文为空，无法出练习题。")

    assessor = questions_assessor or default_questions_assessor
    assessed = assessor(body, job_description)
    questions = list(assessed.get("questions") or [])
    if not questions:
        raise DraftQuestionsError("未得到任何练习题。")
    scope = str(assessed.get("scope") or DEFAULT_SCOPE)

    md_name = f"{stem}.questions.md"
    json_name = f"{stem}.questions.json"
    report_md = run_dir / md_name
    report_json = run_dir / json_name
    record = {
        "tool": "draft-questions",
        "judges_content": False,
        "evaluates_layout": False,
        "is_practice_only": True,
        "not_company_real_questions": True,
        "scope": scope,
        "disclaimer": DISCLAIMER,
        "standard": "docs/01-product/001_prd_就业守护助手产品说明.md#46",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "questions": questions,
        "method": {
            "questions": "llm" if questions_assessor is None else "injected",
        },
    }
    if job_description:
        record["job_description_provided"] = True

    report_md.write_text(
        _markdown_report(scope=scope, questions=questions),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return QuestionsResult(
        run_dir=run_dir,
        scope=scope,
        questions=questions,
        report_md=report_md,
        report_json=report_json,
    )
