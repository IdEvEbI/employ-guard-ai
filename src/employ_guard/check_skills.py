"""技能结构检测。对照 003 §3.1a / S3 / 004 C8；不替代整份判能不能投。"""

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
    "本文件由 `check-skills` 对照技能编写规则（003 §3.1a / S3）与合格线 C8 做分项检查。"
    "市场高频技能摘要对齐 docs/04-standard/001 大模型岗位 JD 扫描（应用 / 应用算法岗）。"
    "不替代整份「判能不能投」，也不评价排版。"
    "本项未过不等于已写入不能投结论，须由老师或后续 judge 汇总。"
)
SKILLS_MAX_TOKENS = 3072
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

# 001 §3～§4 摘要：写入提示词，不整篇粘贴 JD
_MARKET_SKILLS_DIGEST = """
应用 / 应用算法岗（001 A+B）市场高频（定性，非精确百分比）：
- 几乎都会写：RAG 全链路（解析/切分/Embedding/向量库/混合检索/Rerank/Query 改写）；Agent 编排（规划/工具调用/记忆/多 Agent/降级）；业务落地场景；效果与评测（offline/online eval、Bad Case、Harness）；工程交付（Python 服务化、API、Docker/K8s）；安全与可控。
- 常见：Prompt/上下文工程；微调 LoRA/SFT；推理部署 vLLM/量化；向量组件；LangGraph/LangChain 等框架；多模型接入；Function Calling / MCP。
- 加分：Multi-Agent 平台化、可观测、GraphRAG、多模态等。
- 不以预训练研究 / 训推 Infra（001 C/D）为人人硬门槛。
- Python 几乎必备，但是「能跑 AI 链路」的基础，不宜独占技能区主视觉。
""".strip()

SYSTEM_PROMPT = f"""你是就业辅导场景的「专业技能」检查员。
只根据给定正文（及可选的已抽取字段）判断技能区：分组、与意向同向、与项目覆盖、对齐市场高频、岗位相关是否靠前。
禁止：评价排版；代写简历；打百分制；把本项写成整份「能不能投」终裁；把 C/D 类训推 Infra 当应用岗硬门槛。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 市场技能摘要（对齐 001，供 SK5）
{_MARKET_SKILLS_DIGEST}

## 书面口径（003 §3.1a / S3 / 004 C8）
- **SK1 结构分组**：技能宜按「模型与算法 / RAG / Agent / 工程与部署 / 评测与安全」等类别分组（不必五类齐全）。一整段堆砌、看不出类别 → grouping.status=doubtful（不因未分组单独判 fail）。
- **SK2 与意向同向**：技能主项应能支撑首页岗位类表述（应用岗偏 RAG/Agent/工程；算法岗偏模型/评测等）。明显拧着 → role_alignment.status=doubtful。
- **SK3 与项目覆盖**：写入技能区的关键框架 / 模型，至少在一个项目或工作职责中能找到对应用法。大段堆砌且项目完全无法支撑 → project_coverage.status=fail。轻微遗漏 → doubtful。
- **SK4**：满篇「熟练掌握 / 使用」→ 写入 verbosity_note，**不因此单独 fail**。
- **SK5 对齐市场高频**：技能区应能扫到上述摘要中至少两类应用向高频能力（如 RAG 链路、Agent/工具、评测、工程交付）。岗位相关高频几乎看不见 → market_alignment.status=doubtful。不因缺某一热点名词单独 fail。
- **SK6 岗位相关靠前**：大模型 / RAG / Agent / 评测等宜靠前或进主组；纯 Python、Docker、Linux、Coze/Dify 等低代码或基础项宜靠后、缩短或并入「工程与工具」。基础项大段置顶、岗位相关靠后淹没 → skill_order.status=doubtful（不单独 fail）。

## 输出字段（必须齐全）
{{
  "status": "pass|fail|doubtful",
  "target_role": "从首页读到的岗位类表述；没有则 null",
  "grouping": {{"status": "pass|fail|doubtful", "note": "一两句依据"}},
  "role_alignment": {{"status": "pass|fail|doubtful", "note": "一两句依据"}},
  "project_coverage": {{"status": "pass|fail|doubtful", "note": "一两句依据"}},
  "market_alignment": {{"status": "pass|fail|doubtful", "note": "对照 001 摘要：看到了哪些高频 / 缺什么"}},
  "skill_order": {{"status": "pass|fail|doubtful", "note": "是否岗位相关靠前、基础项是否置顶"}},
  "verbosity_note": "若有熟练堆砌等写作问题则写一句；否则 null",
  "fixes": ["可执行改法，最多 3 条；对事不对人"],
  "notes": ["其它简短说明，可空数组"]
}}

## 总评 status 怎么定
- project_coverage 为 fail → 总评 fail
- 任一分项 fail → 总评 fail
- 否则任一分项 doubtful → 总评 doubtful
- 否则 pass
"""

SkillsAssessor = Callable[[str, dict[str, Any] | None], dict[str, Any]]

_STATUS_ZH = {
    "pass": "过本项",
    "fail": "未过本项",
    "doubtful": "存疑",
}
_CHECK_LABELS = {
    "grouping": "SK1 结构分组",
    "role_alignment": "SK2 与意向同向",
    "project_coverage": "SK3 与项目覆盖",
    "market_alignment": "SK5 对齐市场高频",
    "skill_order": "SK6 岗位相关靠前",
}


@dataclass(frozen=True)
class SkillsResult:
    """一次技能检测的落盘结果。"""

    run_dir: Path
    stem: str
    status: str
    target_role: str | None
    report_md: Path
    report_json: Path


class CheckSkillsError(Exception):
    """缺少简历文本、输入无效，或技能检测失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise CheckSkillsError("LLM 返回为空，无法解析 json 结果。")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise CheckSkillsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise CheckSkillsError(
                f"LLM 返回不是合法 JSON：{stripped[:400]}"
            ) from exc
    if not isinstance(data, dict):
        raise CheckSkillsError("LLM 返回须为 JSON 对象。")
    return data


def _as_optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none", "无", "未知"}:
        return None
    return text


def _as_str_list(raw: Any, *, limit: int = 3) -> list[str]:
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


def _normalize_subcheck(raw: Any, *, default_fail_note: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {"status": "fail", "note": default_fail_note}
    status = str(raw.get("status") or "").strip().lower()
    if status not in {"pass", "fail", "doubtful"}:
        status = "fail"
    note = _as_optional_str(raw.get("note")) or default_fail_note
    return {"status": status, "note": note}


def _derive_overall(*subs: dict[str, str]) -> str:
    if any(item["status"] == "fail" for item in subs):
        return "fail"
    if any(item["status"] == "doubtful" for item in subs):
        return "doubtful"
    return "pass"


def normalize_skills_fields(data: dict[str, Any]) -> dict[str, Any]:
    """规整 LLM / 注入结果；总评由分项推导。"""
    grouping = _normalize_subcheck(
        data.get("grouping"),
        default_fail_note="未返回技能分组判断。",
    )
    role_alignment = _normalize_subcheck(
        data.get("role_alignment"),
        default_fail_note="未返回与意向同向判断。",
    )
    project_coverage = _normalize_subcheck(
        data.get("project_coverage"),
        default_fail_note="未返回与项目覆盖判断。",
    )
    market_alignment = _normalize_subcheck(
        data.get("market_alignment"),
        default_fail_note="未返回市场高频技能对齐判断。",
    )
    skill_order = _normalize_subcheck(
        data.get("skill_order"),
        default_fail_note="未返回技能排序判断。",
    )
    status = _derive_overall(
        grouping,
        role_alignment,
        project_coverage,
        market_alignment,
        skill_order,
    )
    fields = {
        "status": status,
        "target_role": _as_optional_str(data.get("target_role")),
        "grouping": grouping,
        "role_alignment": role_alignment,
        "project_coverage": project_coverage,
        "market_alignment": market_alignment,
        "skill_order": skill_order,
        "verbosity_note": _as_optional_str(data.get("verbosity_note")),
        "fixes": _as_str_list(data.get("fixes"), limit=3),
        "notes": _as_str_list(data.get("notes"), limit=5),
    }
    if fields["status"] == "fail" and not fields["fixes"]:
        fields["fixes"] = [
            "按类别分组列出技能，并保证关键框架 / 模型在至少一个项目职责中有对应用法。"
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
    skills = fields.get("skills")
    skill_list = (
        [str(s).strip() for s in skills if str(s).strip()]
        if isinstance(skills, list)
        else []
    )
    return {
        "name": fields.get("name"),
        "target_role": fields.get("target_role"),
        "skills": skill_list[:40],
        "parse_incomplete": fields.get("parse_incomplete"),
    }


def _prefer_normalized_body(run_dir: Path, stem: str, fallback: str) -> str:
    norm = run_dir / f"{stem}.resume.norm.md"
    if not norm.is_file():
        return fallback
    raw = norm.read_text(encoding="utf-8")
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


def default_skills_assessor(
    resume_text: str, parsed_hint: dict[str, Any] | None
) -> dict[str, Any]:
    """把正文发给 LLM，解析技能分项结果。"""
    user_parts = [
        "以下是简历正文（优先已规范化）。请检查专业技能：分组、与意向、与项目覆盖、对齐 001 市场高频、岗位相关是否靠前。",
        "",
        resume_text,
        "",
    ]
    if parsed_hint:
        user_parts.extend(
            [
                "已抽取字段（仅供参考；仍以正文为准）：",
                json.dumps(parsed_hint, ensure_ascii=False),
                "",
            ]
        )
    user_parts.append("请严格按系统说明只输出 json 对象。")
    base_user_text = "\n".join(user_parts)
    last_error: CheckSkillsError | None = None
    for attempt, strategy in enumerate(ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=SKILLS_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            return normalize_skills_fields(parsed)
        except (LLMError, CheckSkillsError) as exc:
            last_error = (
                exc
                if isinstance(exc, CheckSkillsError)
                else CheckSkillsError(str(exc))
            )
            if attempt < len(ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc
    raise CheckSkillsError("技能检测失败。")


def _markdown_report(fields: dict[str, Any]) -> str:
    status = str(fields.get("status") or "fail")
    lines = [
        "# 技能结构检测",
        "",
        f"> {DISCLAIMER}",
        "",
        f"**本项结论**：{_STATUS_ZH.get(status, status)}（`{status}`）",
        "",
        "## 岗位意向（参考）",
        "",
        f"- **读到的表述**：{fields.get('target_role') or '（未抽到 / 首页未见）'}",
        "",
        "## 分项",
        "",
        "| 编号 | 结论 | 说明 |",
        "| ---- | ---- | ---- |",
    ]
    for key, label in _CHECK_LABELS.items():
        item = fields.get(key) or {}
        sub = str(item.get("status") or "fail")
        note = str(item.get("note") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {label} | {_STATUS_ZH.get(sub, sub)}（`{sub}`） | {note} |"
        )
    lines.append("")
    if fields.get("verbosity_note"):
        lines.extend(
            [
                "## 用语提醒（不单独判未过）",
                "",
                str(fields["verbosity_note"]),
                "",
            ]
        )
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
            "- 本报告对应书写标准 **§3.1a（SK1～SK6）** 与合格线 **C8 / S3**。",
            "- SK5 对照 001 大模型岗位 JD 扫描摘要（应用 / 应用算法岗），非整篇 JD。",
            "- 整份能不能投仍以 `judge-resume`（及后续汇总）为准。",
            "",
        ]
    )
    return "\n".join(lines)


def check_skills(
    source: Path,
    *,
    root: Path | None = None,
    skills_assessor: SkillsAssessor | None = None,
) -> SkillsResult:
    """检查技能结构 / 意向 / 项目覆盖，写出 skills 产物。"""
    try:
        run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    except JudgeResumeError as exc:
        raise CheckSkillsError(str(exc)) from exc
    if not body.strip():
        raise CheckSkillsError("简历正文为空，无法做技能检测。")

    text_for_check = _prefer_normalized_body(run_dir, stem, body)
    parsed_hint = _load_parsed_hint(run_dir, stem)

    assessor = skills_assessor or default_skills_assessor
    raw = assessor(text_for_check, parsed_hint)
    fields = normalize_skills_fields(raw)

    report_md = run_dir / f"{stem}.skills.md"
    report_json = run_dir / f"{stem}.skills.json"
    report_md.write_text(_markdown_report(fields), encoding="utf-8")

    record = {
        "tool": "check-skills",
        "judges_content": False,
        "evaluates_layout": False,
        "not_in_resume_by_default": True,
        "disclaimer": DISCLAIMER,
        "standard": "docs/04-standard/003_resume-standard_简历书写标准.md#31a",
        "input": source_label,
        "text_sha256": _sha256_text(text_for_check),
        "used_normalized": (run_dir / f"{stem}.resume.norm.md").is_file(),
        "used_parsed_hint": parsed_hint is not None,
        "status": fields["status"],
        "target_role": fields["target_role"],
        "grouping": fields["grouping"],
        "role_alignment": fields["role_alignment"],
        "project_coverage": fields["project_coverage"],
        "market_alignment": fields["market_alignment"],
        "skill_order": fields["skill_order"],
        "verbosity_note": fields["verbosity_note"],
        "fixes": fields["fixes"],
        "notes": fields["notes"],
        "artifacts": {
            "skills_md": report_md.name,
            "skills_json": report_json.name,
        },
        "method": {
            "skills": "llm" if skills_assessor is None else "injected",
        },
    }
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SkillsResult(
        run_dir=run_dir,
        stem=stem,
        status=fields["status"],
        target_role=fields["target_role"],
        report_md=report_md,
        report_json=report_json,
    )
