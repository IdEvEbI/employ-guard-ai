"""按项目出基础题与追问。辅导增强；不判能不能投，不评排版。"""

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
    "下列题目由工具根据简历文本按项目推测，仅供学员练习。"
    "不是某家公司的面试真题，也不能替代「判能不能投」。"
)
BASICS_TARGET = 5
DEEP_TARGET = 3
MAX_PROJECTS = 3
QUESTIONS_MAX_TOKENS = 8192
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

SYSTEM_PROMPT = """你是就业辅导场景的「按项目练习题」出题员。只根据简历文本识别主项目，并按项目出题供学员练习。
禁止：判断能不能投；评价排版；写成某企业真题或「某司一面原题」；代写标准答案长文；打百分制分数；编造简历未写的项目。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用技术面，不是某家公司的真题）",
  "projects": [
    {
      "name": "项目名称（与简历一致或可对齐的简称）",
      "why_selected": "为何选作主项目（一两句，须能在简历找到依据）",
      "basics": [
        {
          "id": "P1-B1",
          "question": "完整中文问句（基础题）",
          "focus": "考察点（一两句）",
          "follow_ups": ["可能追问 1", "可能追问 2"]
        }
      ],
      "deep_dives": [
        {
          "id": "P1-D1",
          "question": "完整中文问句（深挖题）",
          "focus": "练习重点",
          "why": "依据简历哪一点（一两句）"
        }
      ]
    }
  ]
}

## 出题规则
- 识别 **1～3** 个主项目（简历里篇幅最长、技术最完整的项目优先）；没有可辨认项目时 projects 可为空数组并在 scope 中说明。
- 每个项目：**约 5** 道 basics（允许 4～6）；每道须含 focus（考察点）与 **1～3** 条 follow_ups（可能追问）。
- 每个项目：**约 3** 道 deep_dives（允许 2～4）；须能在简历找到依据。
- 基础题偏「能讲清做了什么 / 关键概念 / 分工」；深挖题偏「权衡、失败、指标、边界与可复现细节」。
- 题干用完整问句；对事不对人；避免空泛「谈谈你的优势」。
- 若提供了目标岗位说明，题目可略向该方向靠拢，但仍须标明不是该公司真题。
- 无岗位说明时，scope 必须写明通用技术面、不是某家公司的真题。
"""

QuestionsAssessor = Callable[[str, str | None], dict[str, Any]]


@dataclass(frozen=True)
class QuestionsResult:
    """一次按项目出题的落盘结果。"""

    run_dir: Path
    scope: str
    projects: list[dict[str, Any]]
    report_md: Path
    report_json: Path

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def question_count(self) -> int:
        total = 0
        for project in self.projects:
            total += len(project.get("basics") or [])
            total += len(project.get("deep_dives") or [])
        return total


class DraftQuestionsError(Exception):
    """缺少简历文本、输入无效，或按项目出题失败。"""


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
            raise DraftQuestionsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DraftQuestionsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from exc
    if not isinstance(data, dict):
        raise DraftQuestionsError("LLM 返回须为 JSON 对象。")
    return data


def _normalize_follow_ups(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for entry in raw:
        text = str(entry or "").strip()
        if text:
            items.append(text)
    return items[:3]


def _normalize_basics(
    raw: Any,
    *,
    project_index: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        follow_ups = _normalize_follow_ups(item.get("follow_ups"))
        if not follow_ups:
            follow_ups = ["若面试官追问细节，你会先补哪一层？"]
        qid = str(item.get("id") or f"P{project_index}-B{index}").strip()
        result.append(
            {
                "id": qid or f"P{project_index}-B{index}",
                "question": question,
                "focus": str(item.get("focus") or "").strip() or "讲清角色、输入输出与结果。",
                "follow_ups": follow_ups,
                "method": "llm",
            }
        )
    return result


def _normalize_deep_dives(
    raw: Any,
    *,
    project_index: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        qid = str(item.get("id") or f"P{project_index}-D{index}").strip()
        result.append(
            {
                "id": qid or f"P{project_index}-D{index}",
                "question": question,
                "focus": str(item.get("focus") or "").strip() or "讲清权衡、失败与可复现细节。",
                "why": str(item.get("why") or "").strip() or "未说明依据。",
                "method": "llm",
            }
        )
    return result


def _normalize_projects(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("projects") or []
    if not isinstance(raw, list):
        return []
    projects: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:MAX_PROJECTS], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        basics = _normalize_basics(item.get("basics"), project_index=index)
        deep_dives = _normalize_deep_dives(item.get("deep_dives"), project_index=index)
        if not basics and not deep_dives:
            continue
        projects.append(
            {
                "name": name,
                "why_selected": str(item.get("why_selected") or "").strip()
                or "简历中的主项目。",
                "basics": basics,
                "deep_dives": deep_dives,
            }
        )
    return projects


def default_questions_assessor(
    resume_text: str,
    job_description: str | None,
) -> dict[str, Any]:
    """把简历文本发给 LLM，解析按项目分组的练习题。"""
    user_parts = [
        "以下是简历正文。",
        "",
        resume_text,
        "",
        f"请识别最多 {MAX_PROJECTS} 个主项目；"
        f"每项目约 {BASICS_TARGET} 道基础题（含考察点与可能追问）、"
        f"约 {DEEP_TARGET} 道深挖题。",
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
    for attempt, strategy in enumerate(ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=QUESTIONS_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            projects = _normalize_projects(parsed)
            if not projects:
                raise DraftQuestionsError(
                    "模型未返回可用的按项目练习题（未识别到主项目或题目为空）。"
                )
            scope = str(parsed.get("scope") or "").strip() or DEFAULT_SCOPE
            if not job_description or not str(job_description).strip():
                if "真题" not in scope:
                    scope = DEFAULT_SCOPE
            return {"scope": scope, "projects": projects}
        except (LLMError, DraftQuestionsError) as exc:
            last_error = (
                exc
                if isinstance(exc, DraftQuestionsError)
                else DraftQuestionsError(str(exc))
            )
            if attempt < len(ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc

    raise DraftQuestionsError("按项目出练习题失败。")


def _markdown_report(*, scope: str, projects: list[dict[str, Any]]) -> str:
    lines = [
        "# 按项目练习题（推测）",
        "",
        "> 本文件由 `draft-questions` 根据简历文本生成。"
        "**仅供练习**，不是某家公司的真题；不替代「判能不能投」。"
        "`resume` 完整模式会跑本步；`--triage` / `--no-questions` 可关闭。",
        "",
        f"**范围**：{scope}",
        "",
        f"**声明**：{DISCLAIMER}",
        "",
    ]
    for project in projects:
        lines.append(f"## 项目：{project['name']}")
        lines.append("")
        lines.append(f"- **为何选作主项目**：{project['why_selected']}")
        lines.append("")
        lines.append("### 基础题（考察点 + 可能追问）")
        lines.append("")
        for item in project.get("basics") or []:
            lines.append(f"#### {item['id']}")
            lines.append("")
            lines.append(str(item["question"]))
            lines.append("")
            lines.append(f"- **考察点**：{item['focus']}")
            lines.append("- **可能追问**：")
            for follow in item.get("follow_ups") or []:
                lines.append(f"  - {follow}")
            lines.append("")
        lines.append("### 深挖题")
        lines.append("")
        for item in project.get("deep_dives") or []:
            lines.append(f"#### {item['id']}")
            lines.append("")
            lines.append(str(item["question"]))
            lines.append("")
            lines.append(f"- **练习重点**：{item['focus']}")
            lines.append(f"- **推测依据**：{item['why']}")
            lines.append("")
    return "\n".join(lines)


def draft_questions(
    source: Path,
    *,
    job_description: str | None = None,
    root: Path | None = None,
    questions_assessor: QuestionsAssessor | None = None,
) -> QuestionsResult:
    """按项目出练习题，写出 `{stem}.questions.md` / `.json`。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise DraftQuestionsError(str(exc)) from exc
    if not body.strip():
        raise DraftQuestionsError("简历正文为空，无法按项目出练习题。")

    assessor = questions_assessor or default_questions_assessor
    assessed = assessor(body, job_description)
    projects = list(assessed.get("projects") or [])
    if not projects:
        raise DraftQuestionsError("未得到任何按项目练习题。")
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
        "not_in_triage_by_default": True,
        "scope": scope,
        "disclaimer": DISCLAIMER,
        "standard": "docs/01-product/001_prd_就业守护助手产品说明.md#46",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "projects": projects,
        "targets": {
            "basics_per_project": BASICS_TARGET,
            "deep_dives_per_project": DEEP_TARGET,
            "max_projects": MAX_PROJECTS,
        },
        "method": {
            "projects": "llm" if questions_assessor is None else "injected",
        },
    }
    if job_description:
        record["job_description_provided"] = True

    report_md.write_text(
        _markdown_report(scope=scope, projects=projects),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return QuestionsResult(
        run_dir=run_dir,
        scope=scope,
        projects=projects,
        report_md=report_md,
        report_json=report_json,
    )
