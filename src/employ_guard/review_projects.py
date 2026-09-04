"""按主项目审阅含金量与难度档。辅导增强；不判能不能投；不含薪资。"""

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

DEFAULT_SCOPE = "通用技术面，不是针对某一企业"
DISCLAIMER = (
    "下列档次由工具根据简历文本按项目审阅，仅供辅导改项目写法与练习优先级。"
    "不替代「判能不能投」，也不含薪资匹配或报价。"
)
MAX_PROJECTS = 3
MAX_FIXES = 2
TIER_LABELS = {"high": "高", "mid": "中", "low": "低"}
ROLE_LABELS = {"primary": "主项目", "secondary": "辅项目"}
PROJECTS_MAX_TOKENS = 4096
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

SYSTEM_PROMPT = """你是就业辅导场景的「项目审阅」员。只根据简历文本识别主项目，并给出含金量档与难度档。
禁止：判断能不能投；评价排版；打百分制；做薪资匹配或报价；代写项目经历；编造简历未写的技术；另发明一套「好项目」标准。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

档次只能是 high / mid / low（对应中文高 / 中 / 低）。禁止「沾边即高」。

## 书面口径（必须遵守）
- 含金量（value_tier）：面试能扛多少追问、证据是否够厚。高=场景清楚 + 职责落在链上 + 输入→关键环节→输出/评测可追问；中=有名词但闭环弱或说明书感；低=几乎只有框架名/单点调用或空泛自评。
- 难度（difficulty_tier）：相对应用向常见交付的技术与工程复杂度。高=多环节系统且本人参与关键设计/权衡；中=链路中一段或工程/评测一带而过；低=主要是聊天 API、低代码拼装或单一脚本。
- 难度高不等于含金量高；可分档不一致。
- 每项目 ≤2 条可执行改法；对事不对人。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用技术面，不是针对某一企业）",
  "summary": "一两句：哪个项目宜作为主打练习或投递叙事重点",
  "projects": [
    {
      "name": "项目名称（与简历一致或可对齐的简称）",
      "role": "primary 或 secondary",
      "why_selected": "为何选入审阅（一两句）",
      "value_tier": "high|mid|low",
      "value_evidence": "含金量依据（一两句，须能在简历找到）",
      "difficulty_tier": "high|mid|low",
      "difficulty_evidence": "难度依据（一两句，须能在简历找到）",
      "fixes": ["可执行改法1", "可执行改法2"]
    }
  ]
}

## 规则
- 识别 1～3 个主项目；primary 宜少而深；没有可辨认项目时 projects 可为空数组并在 summary 说明。
- 无岗位说明时，scope 必须写明通用技术面。
"""

ProjectsAssessor = Callable[[str, str | None], dict[str, Any]]


@dataclass(frozen=True)
class ProjectsResult:
    """一次项目审阅的落盘结果。"""

    run_dir: Path
    scope: str
    summary: str
    projects: list[dict[str, Any]]
    report_md: Path
    report_json: Path

    @property
    def project_count(self) -> int:
        return len(self.projects)


class ReviewProjectsError(Exception):
    """缺少简历文本、输入无效，或项目审阅失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ReviewProjectsError("LLM 返回为空，无法解析 json 结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ReviewProjectsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ReviewProjectsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from exc
    if not isinstance(data, dict):
        raise ReviewProjectsError("LLM 返回须为 JSON 对象。")
    return data


def _normalize_tier(raw: Any, *, default: str = "mid") -> str:
    text = str(raw or "").strip().lower()
    aliases = {
        "高": "high",
        "中": "mid",
        "低": "low",
        "high": "high",
        "mid": "mid",
        "middle": "mid",
        "medium": "mid",
        "low": "low",
    }
    return aliases.get(text, default if default in TIER_LABELS else "mid")


def _normalize_role(raw: Any, *, index: int) -> str:
    text = str(raw or "").strip().lower()
    if text in {"primary", "main", "主", "主项目"}:
        return "primary"
    if text in {"secondary", "辅", "辅项目"}:
        return "secondary"
    return "primary" if index == 1 else "secondary"


def _normalize_fixes(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    fixes: list[str] = []
    for entry in raw:
        text = str(entry or "").strip()
        if text:
            fixes.append(text)
        if len(fixes) >= MAX_FIXES:
            break
    return fixes


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
        projects.append(
            {
                "name": name,
                "role": _normalize_role(item.get("role"), index=index),
                "why_selected": str(item.get("why_selected") or "").strip()
                or "简历中的主项目。",
                "value_tier": _normalize_tier(item.get("value_tier")),
                "value_evidence": str(item.get("value_evidence") or "").strip()
                or "未写明依据。",
                "difficulty_tier": _normalize_tier(item.get("difficulty_tier")),
                "difficulty_evidence": str(
                    item.get("difficulty_evidence") or ""
                ).strip()
                or "未写明依据。",
                "fixes": _normalize_fixes(item.get("fixes")),
                "method": "llm",
            }
        )
    return projects


def default_projects_assessor(
    resume_text: str,
    job_description: str | None,
) -> dict[str, Any]:
    """把简历文本发给 LLM，解析按项目的含金量 / 难度档。"""
    user_parts = [
        "以下是简历正文。",
        "",
        resume_text,
        "",
        f"请识别最多 {MAX_PROJECTS} 个主项目，给出含金量档与难度档（high/mid/low）。",
        "不要做薪资匹配；不要判断能不能投。",
        "",
    ]
    if job_description and job_description.strip():
        user_parts.extend(
            [
                "目标岗位说明（可略向该方向审阅，但仍须标明不是针对某一企业的录用结论）：",
                job_description.strip(),
                "",
            ]
        )
    else:
        user_parts.append(
            "未提供目标岗位说明；scope 须标明通用技术面、不是针对某一企业。"
        )
    user_parts.append("请严格按系统说明只输出 json 对象。")
    base_user_text = "\n".join(user_parts)

    last_error: ReviewProjectsError | None = None
    for attempt, strategy in enumerate(ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=PROJECTS_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            projects = _normalize_projects(parsed)
            if not projects:
                raise ReviewProjectsError(
                    "模型未返回可用的项目审阅（未识别到主项目）。"
                )
            scope = str(parsed.get("scope") or "").strip() or DEFAULT_SCOPE
            if not job_description or not str(job_description).strip():
                if "企业" not in scope and "通用" not in scope:
                    scope = DEFAULT_SCOPE
            summary = (
                str(parsed.get("summary") or "").strip()
                or "见各项目档次与依据。"
            )
            return {"scope": scope, "summary": summary, "projects": projects}
        except (LLMError, ReviewProjectsError) as exc:
            last_error = (
                exc
                if isinstance(exc, ReviewProjectsError)
                else ReviewProjectsError(str(exc))
            )
            if attempt < len(ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc

    raise ReviewProjectsError("项目审阅失败。")


def _tier_zh(tier: str) -> str:
    return TIER_LABELS.get(tier, tier)


def _role_zh(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _markdown_report(
    *,
    scope: str,
    summary: str,
    projects: list[dict[str, Any]],
) -> str:
    lines = [
        "# 项目审阅（含金量 / 难度档）",
        "",
        "> 本文件由 `review-projects` 根据简历文本生成。"
        "**不替代「判能不能投」**；**不含薪资匹配**。"
        "独立工具，默认不挂入 `resume`，也不进 `--triage`。",
        "",
        f"**范围**：{scope}",
        "",
        f"**摘要**：{summary}",
        "",
        f"**声明**：{DISCLAIMER}",
        "",
    ]
    for project in projects:
        lines.append(f"## 项目：{project['name']}")
        lines.append("")
        lines.append(f"- **角色**：{_role_zh(str(project['role']))}")
        lines.append(f"- **为何选入**：{project['why_selected']}")
        lines.append(
            f"- **含金量档**：{_tier_zh(str(project['value_tier']))}"
            f"（{project['value_evidence']}）"
        )
        lines.append(
            f"- **难度档**：{_tier_zh(str(project['difficulty_tier']))}"
            f"（{project['difficulty_evidence']}）"
        )
        fixes = project.get("fixes") or []
        if fixes:
            lines.append("- **可改**：")
            for fix in fixes:
                lines.append(f"  - {fix}")
        lines.append("")
    return "\n".join(lines)


def review_projects(
    source: Path,
    *,
    job_description: str | None = None,
    root: Path | None = None,
    projects_assessor: ProjectsAssessor | None = None,
) -> ProjectsResult:
    """按项目审阅含金量与难度，写出 `{stem}.projects.md` / `.json`。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise ReviewProjectsError(str(exc)) from exc
    if not body.strip():
        raise ReviewProjectsError("简历正文为空，无法做项目审阅。")

    assessor = projects_assessor or default_projects_assessor
    assessed = assessor(body, job_description)
    projects = list(assessed.get("projects") or [])
    if not projects:
        raise ReviewProjectsError("未得到任何项目审阅结果。")
    scope = str(assessed.get("scope") or DEFAULT_SCOPE)
    summary = str(assessed.get("summary") or "见各项目档次与依据。")

    # injected assessor 可能尚未规范化
    if projects_assessor is not None:
        projects = _normalize_projects({"projects": projects})
        for item in projects:
            item["method"] = "injected"

    report_md = run_dir / f"{stem}.projects.md"
    report_json = run_dir / f"{stem}.projects.json"
    record = {
        "tool": "review-projects",
        "judges_content": False,
        "evaluates_layout": False,
        "includes_salary": False,
        "not_in_resume_by_default": True,
        "not_in_triage_by_default": True,
        "scope": scope,
        "summary": summary,
        "disclaimer": DISCLAIMER,
        "standard": "docs/04-standard/005_project-review_项目审阅口径.md",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "projects": projects,
        "targets": {"max_projects": MAX_PROJECTS, "max_fixes_per_project": MAX_FIXES},
        "method": {
            "projects": "llm" if projects_assessor is None else "injected",
        },
    }
    if job_description:
        record["job_description_provided"] = True

    report_md.write_text(
        _markdown_report(scope=scope, summary=summary, projects=projects),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ProjectsResult(
        run_dir=run_dir,
        scope=scope,
        summary=summary,
        projects=projects,
        report_md=report_md,
        report_json=report_json,
    )
