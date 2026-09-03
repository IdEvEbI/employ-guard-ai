"""判内容能不能投。对照 docs/04-standard/004 §2；不评价排版。"""

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

from employ_guard.llm import LLMError, chat_completion
from employ_guard.paths import output_run_dir, resolve_input_file

PASS_CODES = tuple(f"C{i}" for i in range(1, 10))
LEVEL_CODES = tuple(f"H{i}" for i in range(1, 9))
LEVEL_LABELS = {"high": "高", "mid": "中", "low": "低"}
DEFAULT_SCOPE = "通用大模型应用 / 应用算法面初筛，非针对某一企业"
JUDGE_MAX_TOKENS = 8192
JUDGE_ATTEMPT_STRATEGIES: tuple[dict[str, object], ...] = (
    {"json_object": True, "user_suffix": ""},
    {
        "json_object": True,
        "user_suffix": "\n\n请只输出一个合法 json 对象，字段齐全，不要其它文字。",
    },
    {
        "json_object": False,
        "user_suffix": "\n\n请只输出一个合法 json 对象，字段齐全，不要 Markdown 围栏。",
    },
)

# 2024.09-2025.06 / 2025.08—2026.08 / 2024.09---至今 / 2024.11 – 2025.06
DATE_RANGE_RE = re.compile(
    r"(?P<y1>20\d{2})\s*[./年\-]\s*(?P<m1>\d{1,2})"
    r"(?:\s*[./日\-]\s*\d{1,2})?"
    r"\s*(?:[-–—~]|至|到){1,3}\s*"
    r"(?:"
    r"(?P<ongoing>至今|现在|目前)"
    r"|"
    r"(?P<y2>20\d{2})\s*[./年\-]\s*(?P<m2>\d{1,2})"
    r"(?:\s*[./日\-]\s*\d{1,2})?"
    r")"
)

SYSTEM_PROMPT = """你是简历「内容」检查员。只根据简历文本判断内容上能不能投递过初筛。
禁止：评价排版或版式；出练习题；打百分制分数；发明 004 以外的「好简历」标准；用 Java 全栈 / Spring 等非大模型学科要求。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用大模型应用 / 应用算法面初筛，非针对某一企业）",
  "pass_line": [
    {"id": "C1", "pass": true, "doubtful": false, "note": "求职方向可辨为大模型应用。"},
    {"id": "C2", "pass": true, "doubtful": false, "note": "提供手机与邮箱，可联系。"},
    ... C3 到 C9 同样结构；存疑时 doubtful 为 true，note 只写人话 ...
  ],
  "level_line": [
    {"id": "H1", "level": "high", "note": "近段岗位与目标方向一致，主业为 AI 交付。"},
    ... H2 到 H8；level 只能是 high / mid / low ...
  ],
  "main_blockers": []
}

## 内容合格线（任一 pass=false → 内容未合格；doubtful 不自动判未合格）
- C1 岗位方向可辨：全文能判断为大模型应用或应用算法；不强制「求职意向」字段。
- C2 可联系：有可用手机或邮箱至少一项；不因 QQ 邮箱判不合格。
- C3 算法+RAG+Agent 证据齐全：须同时具备三类可追问证据——① 算法/模型（微调、Prompt、eval/Harness 等）；② RAG（链路至少两段，非仅向量库名词）；③ Agent（规划、Tool/MCP、记忆、降级等且写明本人职责）。缺一类 → pass=false。
- C4 至少 1 个可追问项目：含背景/问题、职责、技术。
- C5 职责可核实：有具体动作，非仅「熟悉/了解/负责推进」。**多个项目标题后均写「项目负责人 / 独立负责」且年限较短（约 1～3 年）** → doubtful=true，pass 仍为 true；note 建议按项目写清可核实贡献（如核心开发 / 模块负责人），避免角色注水印象。
- C6 结果可追问：有业务结果即可过线。下列情形 **必须** doubtful=true，pass 仍为 true：① 多 Agent / 大模型主路径下，整份合同（或同类长文档）审查耗时写「小于 5 秒」且未说明「仅规则前置 / 仅短合同 / 不含 LLM」等口径；② **技术选型或规模与场景明显难辩护**（如 ≤4B 小模型扛金融研报主生成、把 LangGraph+LangChain「共同实现状态管理」写糊、向量库仅千级却写生产级金融 RAG 等）。note 用完整中文写清「哪里不合理 + 建议怎么改」。有可辩护数字（如 40～60 秒）且口径清楚 → 可不因耗时存疑。
- C7 时间线基本自洽：项目须落在对应在职段内。年龄/学历/工龄互算明显不通 → doubtful。**按年月比较**：结束年月 ≤ 检查当日所在年月 → 不算晚于检查日，不得因此存疑；已结束经历写到结束年月即可，不要求改成「至今」。空窗、频繁极短任不单独 pass=false。不核验与档案是否一致。结束年月晚于检查日的判定由实现侧规则处理，你不要自行臆测，也不要写自相矛盾的日期说明。
- C8 技能与项目不严重脱节：大段技能无项目支撑 → pass=false，或存疑时 pass=true 且 doubtful=true。
- C9 工作履历须有 AI 相关主业支撑：个人项目为主且工作履历非 AI → pass=false。

规则层也会对「多项目负责人注水」「小模型扛重生成」「框架并用说不清」「向量规模过小」等做补充存疑；你仍须主动识别，不要依赖规则层才写。

## note 写法（合格线与水平线都必须遵守）
- 用老师能直接念给学生听的完整中文句子，一两句说清「发现了什么 + 建议怎么改」。
- **禁止**在 note 里写程序字段或赋值，例如：doubtful=true、pass=false、level=high、故 doubtful=true。
- **禁止**自问自答或自相矛盾（如先写「晚于检查日？」再写「实际早于」）。
- 存疑项的 note 不要重复「故存疑」套话；表格「存疑」列已标明。

## 内容水平线（仅当全部 C 项 pass=true 时认真填写；level 必须是 high/mid/low）
**禁止「沾边即 high」**。拿不准时优先给 mid，不要给 high。对照：
- H1 履历相关度：
  - 高：求职意向 / 近段岗位名与目标方向一致（如都写「大模型应用开发」），且主业即 AI 交付。
  - 中：工作是 AI，但意向与项目职称拧着或偏泛（例：意向「算法」、项目写「后端研发」；或只写泛「后端」）。
  - 低：非 AI 经历占首页主视觉，或岗位与项目方向明显拧着。
- H2 链路完整度：高=主项目闭环且职责在链上；中=有环节串不成闭环；低=仅框架名。
- H3 工程化：高=服务化/流式/测试/部署/降级等至少两类写在职责里；中=工具名一带；低=几乎无。
- H4 评测与量化：
  - 高：有评测方法或前后对比，数字可辩护（含合理耗时区间），且无 C6 技术可信度存疑。
  - 中：有数字但缺口径，或存在 C6 已存疑的夸张指标 / 难辩护选型。
  - 低：无结果，或数字明显离谱。
- H5 场景深度（务必从严）：
  - 高：同时写清服务对象、业务痛点、使用入口或业务步骤（如「上传→报告→复核」），技术为痛点服务；个人职责按项目可区分。
  - 中：有场景名（如「企业法务」），但职责主要是 1/2/3 技术编号清单或纯实现细节；或各项目角色一律「项目负责人」难区分贡献。
  - 低：纯技术名词堆砌，看不出为谁解决什么问题。
  - 示例：只有「企业法务 + Orchestrator/并发/MCP」→ 中；有「律所律师 + 三类入口 + 发现问题→依据→方案」→ 高。
- H6 信息密度：
  - 高：1～3 个深项目，主深次短，无空泛自评/课程堆砌感。
  - 中：项目够深，但说明书感重，或用长段自我评价、主修课程列表明显凑篇幅。
  - 低：多浅项或过长堆砌。写得多 ≠ 高。
- H7 市场加分项：高=热点能力写入职责与结果；中=技能区有项目一带；低=仅罗列。
- H8 履历稳定性：
  - 高：在职衔接清楚，且能看出当前仍在职（近段写「至今」或等价表述）。
  - 中：衔接尚可，但近段已结束且无「至今」（投递中简历常见，不升为高）；或有小空窗/互算略紧。
  - 低：频繁极短任，或结束年月晚于检查日。
- H 项 note 禁止改写为排版或「能不能投」的替代结论；同样禁止写 level=high 等字段名。

## 未过合格线
- main_blockers 列出导致 pass=false 的 C 编号与一句话原因（人话，不要写 pass=false）。
- level_line 仍输出八项，level 可全为 low，note 可写「未过合格线，不比较水平」——实现侧会在未过合格线时清空水平线。

对事不对人；意见须可执行，避免「再优化项目经历」空泛句。
"""

ContentAssessor = Callable[[str, str | None], dict[str, Any]]


@dataclass(frozen=True)
class JudgeResult:
    """一次判内容的落盘结果。"""

    run_dir: Path
    content_pass: bool
    scope: str
    pass_line: list[dict[str, Any]]
    level_line: list[dict[str, Any]]
    main_blockers: list[str]
    doubtful_items: list[str]
    report_md: Path
    report_json: Path


class JudgeResumeError(Exception):
    """缺少简历文本、输入无效，或判内容失败。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_resume_header(text: str) -> str:
    """去掉 read-resume 生成的固定头，保留正文供评价。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "# 简历文本":
        return text.strip()
    body_start = 0
    for index, line in enumerate(lines):
        if line.startswith("> 本文件由 `read-resume`"):
            body_start = index + 1
            while body_start < len(lines) and not lines[body_start].strip():
                body_start += 1
            break
    return "\n".join(lines[body_start:]).strip()


def resolve_resume_text(
    source: Path,
    *,
    root: Path | None = None,
) -> tuple[Path, Path, str, str]:
    """解析输入，返回 (run_dir, stem, 正文, source_label)。"""
    resolved = source.resolve()
    suffix = resolved.suffix.lower()

    if suffix == ".pdf":
        try:
            pdf_path = resolve_input_file(source)
        except FileNotFoundError as exc:
            raise JudgeResumeError(str(exc)) from exc
        run_dir = output_run_dir(pdf_path, root=root)
        md_candidates = sorted(run_dir.glob("*.resume.md"))
        if not md_candidates:
            raise JudgeResumeError(
                f"未找到简历文本：{run_dir}。请先运行：employ-guard read-resume {pdf_path}"
            )
        md_path = md_candidates[0]
        stem = pdf_path.stem
        raw = md_path.read_text(encoding="utf-8")
        return run_dir, stem, _strip_resume_header(raw), str(pdf_path)

    if suffix in {".md", ".txt"}:
        if not resolved.is_file():
            raise JudgeResumeError(f"找不到文件：{resolved}")
        raw = resolved.read_text(encoding="utf-8")
        body = _strip_resume_header(raw)
        if resolved.name.endswith(".resume.md"):
            stem = resolved.name[: -len(".resume.md")]
            run_dir = resolved.parent
        else:
            stem = resolved.stem
            run_dir = resolved.parent
        return run_dir, stem, body, str(resolved)

    raise JudgeResumeError(
        "输入须为 PDF（须已 read-resume）、*.resume.md 或文本文件。"
    )


def find_future_end_dates(
    resume_text: str,
    *,
    today: date | None = None,
) -> list[str]:
    """找出结束年月晚于检查当日的时间段（「至今」除外）。"""
    as_of = today or date.today()
    as_of_months = as_of.year * 12 + as_of.month
    hits: list[str] = []
    for match in DATE_RANGE_RE.finditer(resume_text):
        raw = match.group(0).strip()
        if match.group("ongoing"):
            continue
        end_year = int(match.group("y2"))
        end_month = int(match.group("m2"))
        if end_month < 1 or end_month > 12:
            continue
        end_months = end_year * 12 + end_month
        if end_months > as_of_months:
            hits.append(raw)
    seen: set[str] = set()
    unique: list[str] = []
    for item in hits:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise JudgeResumeError("LLM 返回为空，无法解析 json 结果。")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError as exc:
                raise JudgeResumeError(
                    f"LLM 结果不是合法 JSON（已尝试截取 {{…}}）：{stripped[:400]}"
                ) from exc
        else:
            raise JudgeResumeError(f"LLM 结果不是合法 JSON：{stripped[:400]}") from None

    if not isinstance(data, dict):
        raise JudgeResumeError("LLM 结果须为 JSON 对象。")
    return data


def _clean_note(note: str) -> str:
    """去掉模型偶发写入的字段名，保留人话说明。"""
    cleaned = str(note or "").strip()
    cleaned = re.sub(
        r"[，,；;。]?\s*故?\s*doubtful\s*=\s*(true|false)\s*。?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"[，,；;。]?\s*故?\s*pass\s*=\s*(true|false)\s*。?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"[，,；;。]?\s*level\s*=\s*(high|mid|low|高|中|低)\s*。?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[，,；;]\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and cleaned[-1] not in "。！？":
        cleaned += "。"
    return cleaned or "未返回说明。"


def _normalize_pass_line(data: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("id")): item
        for item in data.get("pass_line", [])
        if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    for code in PASS_CODES:
        item = by_id.get(code, {})
        result.append(
            {
                "id": code,
                "pass": bool(item.get("pass", False)),
                "doubtful": bool(item.get("doubtful", False)),
                "note": _clean_note(str(item.get("note") or "未返回该项")),
                "method": "llm",
            }
        )
    return result


def _coerce_level(raw: object) -> str:
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in LEVEL_LABELS:
            return value
        if value in {"高", "strong", "true", "yes", "1"}:
            return "high"
        if value in {"中", "medium", "mid"}:
            return "mid"
        if value in {"低", "weak", "false", "no", "0"}:
            return "low"
    if raw is True:
        return "high"
    if raw is False:
        return "low"
    return "mid"


def _normalize_level_line(data: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("id")): item
        for item in data.get("level_line", [])
        if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    for code in LEVEL_CODES:
        item = by_id.get(code, {})
        level = _coerce_level(item.get("level", item.get("signal")))
        result.append(
            {
                "id": code,
                "level": level,
                "note": _clean_note(str(item.get("note") or "未返回该项")),
                "method": "llm",
            }
        )
    return result


def _merge_pass_doubt(
    pass_line: list[dict[str, Any]],
    code: str,
    note: str,
    *,
    marker: str,
    extra_markers: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """把规则层存疑并入指定 C 项（不改 pass）。"""
    markers = (marker, *extra_markers)
    updated: list[dict[str, Any]] = []
    for item in pass_line:
        if item.get("id") != code:
            updated.append(item)
            continue
        merged_note = _clean_note(str(item.get("note") or ""))
        if not any(token in merged_note for token in markers):
            if merged_note in {"", "未返回说明。", "未返回该项。"}:
                merged_note = note
            else:
                merged_note = f"{merged_note.rstrip('。')}。{note}"
        updated.append(
            {
                **item,
                "doubtful": True,
                "note": _clean_note(merged_note),
                "method": "llm+rule" if item.get("method") == "llm" else "rule",
            }
        )
    return updated


def apply_future_date_doubts(
    pass_line: list[dict[str, Any]],
    resume_text: str,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """规则层：结束时间晚于检查当日 → C7 存疑（不改 pass）。"""
    as_of = today or date.today()
    futures = find_future_end_dates(resume_text, today=as_of)
    if not futures:
        return pass_line

    note = (
        f"检查当日为 {as_of.isoformat()}。"
        f"下列工作或项目的结束年月晚于当日，投递中的简历不合理，进行中应改为「至今」："
        + "、".join(futures[:8])
        + "。"
    )
    return _merge_pass_doubt(
        pass_line,
        "C7",
        note,
        marker="晚于当日",
        extra_markers=("晚于检查",),
    )


def find_credibility_issues(resume_text: str) -> list[tuple[str, str, str]]:
    """规则层技术 / 角色可信度线索 → (C 编号, note, marker)。"""
    issues: list[tuple[str, str, str]] = []
    leader_count = len(re.findall(r"项目负责人", resume_text))
    if leader_count >= 3:
        issues.append(
            (
                "C5",
                "多个项目均写「项目负责人」，年限不长时显得角色注水；"
                "宜按项目写清可核实贡献（如核心开发 / 模块负责人），避免一律负责人。",
                "项目负责人",
            )
        )

    small_model = bool(
        re.search(
            r"(?:Qwen|ChatGLM|Llama|LLaMA|DeepSeek)[\w.\-]*?(?:[1-4])\s*B",
            resume_text,
            flags=re.IGNORECASE,
        )
    )
    heavy_gen = bool(re.search(r"金融研报|研报生成|长文档生成|合同审查报告", resume_text))
    if small_model and heavy_gen:
        issues.append(
            (
                "C6",
                "小参数模型（约 4B 及以下）用于金融研报等重生成主路径，面试难辩护；"
                "宜改为更大基座或写清「仅辅助 / 非主生成」口径。",
                "小参数模型",
            )
        )

    if re.search(
        r"LangGraph\s*\+\s*LangChain.{0,48}状态|以\s*LangGraph\s*\+\s*LangChain\s*实现状态",
        resume_text,
        flags=re.IGNORECASE,
    ):
        issues.append(
            (
                "C6",
                "把 LangGraph 与 LangChain「共同实现状态管理」写在一起，职责边界不清；"
                "宜写清各自用途（如 LangGraph 编排状态、LangChain 仅工具链），或删掉堆砌写法。",
                "LangGraph",
            )
        )

    for match in re.finditer(
        r"(?:Milvus|向量库)[^\n。；]{0,48}?(\d{1,4})\s*条?向量",
        resume_text,
        flags=re.IGNORECASE,
    ):
        count = int(match.group(1))
        if count < 5000:
            issues.append(
                (
                    "C6",
                    f"向量规模仅约 {count} 条，与「生产级 / 多源金融 RAG」叙事落差大；"
                    "宜补真实规模口径，或弱化生产级表述。",
                    "向量规模",
                )
            )
            break

    return issues


def apply_credibility_doubts(
    pass_line: list[dict[str, Any]],
    resume_text: str,
) -> list[dict[str, Any]]:
    """规则层：角色注水 / 技术可信度 → C5 或 C6 存疑（不改 pass）。"""
    updated = pass_line
    for code, note, marker in find_credibility_issues(resume_text):
        updated = _merge_pass_doubt(updated, code, note, marker=marker)
    return updated


def _set_level(
    level_line: list[dict[str, Any]],
    code: str,
    *,
    max_level: str,
    note: str,
) -> None:
    order = {"low": 0, "mid": 1, "high": 2}
    for index, item in enumerate(level_line):
        if item.get("id") != code:
            continue
        current = _coerce_level(item.get("level"))
        if order[current] <= order[max_level]:
            return
        merged = _clean_note(str(item.get("note") or ""))
        if note not in merged:
            merged = f"{merged.rstrip('。')}。{note}"
        level_line[index] = {
            **item,
            "level": max_level,
            "note": _clean_note(merged),
            "method": "llm+rule" if item.get("method") == "llm" else "rule",
        }
        return


def _extract_section(resume_text: str, start_markers: tuple[str, ...], end_markers: tuple[str, ...]) -> str:
    lower_map = [(marker, resume_text.find(marker)) for marker in start_markers]
    starts = [(m, i) for m, i in lower_map if i >= 0]
    if not starts:
        return ""
    _, start = min(starts, key=lambda item: item[1])
    rest = resume_text[start:]
    end_at = len(rest)
    for marker in end_markers:
        pos = rest.find(marker, 1)
        if pos >= 0:
            end_at = min(end_at, pos)
    return rest[:end_at]


def _count_numbered_duties(resume_text: str) -> int:
    return len(re.findall(r"(?:^|\n)\s*\d{1,2}[\.、．]\s*\S", resume_text))


def refine_level_line(
    level_line: list[dict[str, Any]],
    resume_text: str,
    pass_line: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """规则层封顶：避免模型对关键水平项沾边即高。"""
    refined = [dict(item) for item in level_line]
    c6_doubt = any(
        item.get("id") == "C6" and item.get("doubtful") for item in pass_line
    )
    if c6_doubt:
        _set_level(
            refined,
            "H4",
            max_level="mid",
            note="存在难以辩护的量化指标或技术可信度问题，评测与量化不超过中。",
        )

    c5_doubt = any(
        item.get("id") == "C5" and item.get("doubtful") for item in pass_line
    )
    if c5_doubt or len(re.findall(r"项目负责人", resume_text)) >= 3:
        _set_level(
            refined,
            "H5",
            max_level="mid",
            note="多项目角色一律「项目负责人」时贡献难区分，场景深度不超过中。",
        )

    has_ongoing = bool(re.search(r"至今|现在|目前", resume_text))
    if not has_ongoing:
        _set_level(
            refined,
            "H8",
            max_level="mid",
            note="近段未写「至今」，投递中简历的在职状态不够清楚，稳定性不超过中。",
        )

    intent_algo = bool(re.search(r"求职意向[^\n]{0,40}算法", resume_text))
    backend_title = bool(
        re.search(r"(后端研发|后端工程师|Java\s*后端)", resume_text, flags=re.IGNORECASE)
    )
    app_title = bool(re.search(r"(应用开发|大模型应用)", resume_text))
    if intent_algo and backend_title and not app_title:
        _set_level(
            refined,
            "H1",
            max_level="mid",
            note="求职意向偏算法，项目或岗位却写后端研发，相关度不超过中。",
        )

    advantage = _extract_section(
        resume_text,
        ("个人优势", "自我评价", "专业技能"),
        ("相关技能", "专业技能", "项目经历", "工作经验", "工作经历"),
    )
    if advantage:
        java_chapter = bool(
            re.search(
                r"Java\s*微服务|前端开发方面|Spring\s*Boot|Spring\s*Cloud",
                advantage,
                flags=re.IGNORECASE,
            )
        )
        java_hits = len(
            re.findall(
                r"Java|Spring|微服务|Vue\.?js|HTML5|前端开发|全栈",
                advantage,
                flags=re.IGNORECASE,
            )
        )
        ai_hits = len(
            re.findall(
                r"RAG|Agent|大模型|LangChain|LangGraph|Milvus|Prompt|智能体",
                advantage,
                flags=re.IGNORECASE,
            )
        )
        if java_chapter or (java_hits >= 3 and java_hits >= ai_hits):
            _set_level(
                refined,
                "H1",
                max_level="mid",
                note="个人优势中 Java / 前端等非大模型篇幅偏重，宜突出大模型相关、弱化无关栈，相关度不超过中。",
            )

    tech_list_heavy = bool(
        re.search(r"(?:^|\n)\s*[1-5][\.、．]\s*(?:多\s*Agent|四维度|混合检索|Human)", resume_text)
    )
    product_entry = bool(
        re.search(
            r"(三类使用入口|使用入口|上传合同|发现问题\s*[→\-]|律所律师|业务价值)",
            resume_text,
        )
    )
    if tech_list_heavy and not product_entry:
        _set_level(
            refined,
            "H5",
            max_level="mid",
            note="主项目偏技术编号清单，缺少使用入口或业务步骤，场景深度不超过中。",
        )

    numbered = _count_numbered_duties(resume_text)
    if numbered >= 12:
        _set_level(
            refined,
            "H5",
            max_level="mid",
            note="项目职责条目过多且偏实现细节，业务痛点与价值宜单独写短，场景深度不超过中。",
        )
        _set_level(
            refined,
            "H6",
            max_level="mid",
            note="职责编号过多，说明书感强，信息密度不超过中；宜压到少数短要点。",
        )

    if len(resume_text) >= 6000:
        _set_level(
            refined,
            "H6",
            max_level="mid",
            note="正文篇幅过长，易淹没关键信息，信息密度不超过中；宜压页并删冗余。",
        )

    padded = bool(re.search(r"自我评价", resume_text)) and bool(
        re.search(r"主修课程", resume_text)
    )
    if padded:
        _set_level(
            refined,
            "H6",
            max_level="mid",
            note="含主修课程与自我评价，易有凑篇幅感，信息密度不超过中。",
        )

    return refined


def default_content_assessor(resume_text: str, job_description: str | None) -> dict[str, Any]:
    """把简历文本发给 LLM，解析 C / H 项。"""
    today = date.today()
    user_parts = [
        f"检查当日：{today.isoformat()}（投递中简历的工作/项目结束年月不得晚于该日；进行中写「至今」。）",
        "",
        "以下是简历正文。",
        "",
        resume_text,
        "",
    ]
    if job_description and job_description.strip():
        user_parts.extend(["目标岗位说明（须纳入 scope 与 C1 等判断）：", job_description.strip(), ""])
    else:
        user_parts.append("未提供目标岗位说明；scope 须标明通用大模型应用/应用算法面初筛、非针对某一企业。")
    user_parts.append("请严格按系统说明只输出 json 对象，不要 Markdown 围栏。")
    base_user_text = "\n".join(user_parts)

    last_error: JudgeResumeError | None = None
    for attempt, strategy in enumerate(JUDGE_ATTEMPT_STRATEGIES, start=1):
        user_text = base_user_text + str(strategy.get("user_suffix") or "")
        try:
            text = chat_completion(
                system=SYSTEM_PROMPT,
                user_text=user_text,
                json_object=bool(strategy.get("json_object", True)),
                max_tokens=JUDGE_MAX_TOKENS,
            )
            parsed = _parse_json_object(text)
            pass_line = apply_credibility_doubts(
                apply_future_date_doubts(
                    _normalize_pass_line(parsed),
                    resume_text,
                    today=today,
                ),
                resume_text,
            )
            level_line = refine_level_line(
                _normalize_level_line(parsed),
                resume_text,
                pass_line,
            )
            return {
                "scope": str(parsed.get("scope") or DEFAULT_SCOPE),
                "pass_line": pass_line,
                "level_line": level_line,
                "main_blockers": [
                    str(item)
                    for item in (parsed.get("main_blockers") or [])
                    if str(item).strip()
                ],
            }
        except (LLMError, JudgeResumeError) as exc:
            last_error = (
                exc if isinstance(exc, JudgeResumeError) else JudgeResumeError(str(exc))
            )
            if attempt < len(JUDGE_ATTEMPT_STRATEGIES):
                time.sleep(float(attempt))
                continue
            raise last_error from exc

    raise JudgeResumeError("判内容失败。")


def _markdown_report(
    *,
    content_pass: bool,
    scope: str,
    pass_line: list[dict[str, Any]],
    level_line: list[dict[str, Any]],
    main_blockers: list[str],
    doubtful_items: list[str],
) -> str:
    verdict = "内容合格" if content_pass else "内容未合格"
    lines = [
        "# 判能不能投（内容）",
        "",
        "> 本文件由 `judge-resume` 只根据简历文本生成。不评价排版。合格线与水平线分开写。",
        "",
        f"**结论**：{verdict}",
        "",
        f"**评价范围**：{scope}",
        "",
        "## 合格线",
        "",
        "| 编号 | 是否过 | 存疑 | 说明 |",
        "| ---- | ------ | ---- | ---- |",
    ]
    for item in pass_line:
        mark = "过" if item["pass"] else "未过"
        doubt = "是" if item.get("doubtful") else "—"
        note = str(item["note"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['id']} | {mark} | {doubt} | {note} |")

    if doubtful_items:
        lines.extend(["", "## 存疑（老师复核，不自动等同未合格）", ""])
        for item in pass_line:
            if not item.get("doubtful"):
                continue
            note = str(item.get("note") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"- **{item['id']}**：{note}")
            lines.append("")

    if main_blockers:
        lines.extend(["", "## 主要卡点", ""])
        for note in main_blockers:
            lines.append(f"- {note}")

    lines.extend(["", "## 水平线", ""])
    if not content_pass:
        lines.append("未过合格线，不输出「水平更高 / 更低」的排序结论。")
    else:
        lines.extend(
            [
                "| 编号 | 水平 | 说明 |",
                "| ---- | ---- | ---- |",
            ]
        )
        for item in level_line:
            level = _coerce_level(item.get("level"))
            mark = LEVEL_LABELS[level]
            note = str(item["note"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {item['id']} | {mark} | {note} |")
    lines.append("")
    return "\n".join(lines)


def judge_resume(
    source: Path,
    *,
    job_description: str | None = None,
    root: Path | None = None,
    content_assessor: ContentAssessor | None = None,
    today: date | None = None,
) -> JudgeResult:
    """判内容，写出 `{stem}.judge.md` / `{stem}.judge.json`，返回结果。"""
    run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    if not body.strip():
        raise JudgeResumeError("简历正文为空，无法判断能不能投。")

    assessor = content_assessor or default_content_assessor
    assessed = assessor(body, job_description)
    pass_line = apply_credibility_doubts(
        apply_future_date_doubts(
            list(assessed.get("pass_line") or []),
            body,
            today=today,
        ),
        body,
    )
    level_line = refine_level_line(
        list(assessed.get("level_line") or []),
        body,
        pass_line,
    )
    scope = str(assessed.get("scope") or DEFAULT_SCOPE)
    main_blockers = list(assessed.get("main_blockers") or [])

    content_pass = all(bool(item.get("pass")) for item in pass_line)
    doubtful_items = [
        f"{item['id']}：{item.get('note', '')}"
        for item in pass_line
        if item.get("doubtful")
    ]
    if not content_pass:
        level_line = []
        if not main_blockers:
            main_blockers = [
                f"{item['id']}：{item.get('note', '')}"
                for item in pass_line
                if not item.get("pass")
            ]

    md_name = f"{stem}.judge.md"
    json_name = f"{stem}.judge.json"
    report_md = run_dir / md_name
    report_json = run_dir / json_name
    record = {
        "tool": "judge-resume",
        "judges_content": True,
        "evaluates_layout": False,
        "content_pass": content_pass,
        "scope": scope,
        "standard": "docs/04-standard/004_resume-bar_简历合格线.md#2",
        "input": source_label,
        "text_sha256": _sha256_text(body),
        "checked_on": (today or date.today()).isoformat(),
        "pass_line": pass_line,
        "level_line": level_line,
        "main_blockers": main_blockers,
        "doubtful_items": doubtful_items,
        "method": {"content": "llm" if content_assessor is None else "injected"},
    }
    if job_description:
        record["job_description_provided"] = True

    report_md.write_text(
        _markdown_report(
            content_pass=content_pass,
            scope=scope,
            pass_line=pass_line,
            level_line=level_line,
            main_blockers=main_blockers,
            doubtful_items=doubtful_items,
        ),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return JudgeResult(
        run_dir=run_dir,
        content_pass=content_pass,
        scope=scope,
        pass_line=pass_line,
        level_line=level_line,
        main_blockers=main_blockers,
        doubtful_items=doubtful_items,
        report_md=report_md,
        report_json=report_json,
    )
