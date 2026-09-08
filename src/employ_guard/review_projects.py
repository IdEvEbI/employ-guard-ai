"""按主项目审阅含金量与难度档。辅导增强；不判能不能投；不含薪资。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from employ_guard.judge_resume import JudgeResumeError, resolve_resume_text
from employ_guard.llm import LLMError, chat_completion

DEFAULT_SCOPE = "通用技术面，不是针对某一企业"
DISCLAIMER = (
    "下列档次与存疑由工具根据简历文本按项目审阅，仅供辅导改项目写法与练习优先级。"
    "不替代「判能不能投」，也不含薪资匹配或报价。"
    "G1-T 与 P1～P8 存疑默认不自动等同内容未合格。"
)
MAX_PROJECTS = 3
MAX_FIXES = 2
TIER_LABELS = {"high": "高", "mid": "中", "low": "低"}
ROLE_LABELS = {"primary": "主项目", "secondary": "辅项目"}
_TIER_RANK = {"high": 3, "mid": 2, "low": 1}
PROJECTS_MAX_TOKENS = 4096
# 与会话「今天」对齐，供「至今」解析；非运行时时钟
_REFERENCE_TODAY = date(2026, 9, 8)
_YM_RE = re.compile(
    r"(?P<y>20\d{2}|19\d{2})\s*[.年/\-]\s*(?P<m>1[0-2]|0?[1-9])"
)
_PRESENT_RE = re.compile(r"至今|现在|present|current", re.I)
_LEAD_RE = re.compile(r"项目负责人|负责人|project\s*lead|tech\s*lead", re.I)
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

SYSTEM_PROMPT = """你是就业辅导场景的「项目审阅」员。只根据简历文本识别主项目，并给出含金量档与难度档，以及叙事可信度字段。
禁止：判断能不能投；评价排版；打百分制；做薪资匹配或报价；代写项目经历；编造简历未写的技术；另发明一套「好项目」标准。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

档次只能是 high / mid / low（对应中文高 / 中 / 低）。禁止「沾边即高」。

## 书面口径（005 v0.4，必须遵守）
- **时间倒序**：projects 数组按项目**结束时间**从近到远排列（无结束则用开始时间）；近段在前。
- **含金量（value_tier）**：面试能扛多少追问。高=场景清楚 + 职责落在链上 + 输入→关键环节→输出/评测可追问；中=有名词但闭环弱；低=几乎只有框架名/单点或空泛。
- **难度（difficulty_tier）**：相对应用向常见交付的技术与工程复杂度。难度高≠含金量高。
- **结构（对齐 003 §3.3）**：每项目标明能否看出背景、方案/技术、本人职责、结果；缺哪块写入 structure_gaps（可空数组）。
- **G1-T**：期望近段含金量不低于更早项目；若近浅远深，在 summary 中提示辅导（规则层会再算一遍）。
- **P1～P8（存疑/辅导，非合格线）**：名称是否过泛；工作/实习项目是否过短；近段是否对齐求职意向；是否全员写负责人；同公司时间重叠；是否跨任职段；是否落在在校期；毕业/入职后项目间是否空窗≥2个月。规则层会按日期再检；你须如实填字段。
- 每项目 ≤2 条可执行改法；对事不对人。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用技术面，不是针对某一企业）",
  "summary": "一两句：哪个项目宜作为主打；若近浅远深或叙事存疑可点一句",
  "profile_hint": {
    "work_years": 1.5,
    "target_role": "求职意向原文或空字符串",
    "work_spans": [{"company": "公司名", "start_ym": "2024-01", "end_ym": "2025-06"}],
    "education_spans": [{"label": "本科", "start_ym": "2018-09", "end_ym": "2022-06"}]
  },
  "projects": [
    {
      "name": "项目名称",
      "time_range": "起止时间原文或归纳，如 2024.03-2025.01；不明则空字符串",
      "start_ym": "2024-03",
      "end_ym": "2025-01",
      "employer": "归属公司或空",
      "category": "work|internship|course|personal|unknown",
      "claims_lead": false,
      "name_too_generic": false,
      "aligns_target_role": true,
      "role": "primary 或 secondary",
      "why_selected": "为何选入审阅（一两句）",
      "value_tier": "high|mid|low",
      "value_evidence": "含金量依据（一两句）",
      "difficulty_tier": "high|mid|low",
      "difficulty_evidence": "难度依据（一两句）",
      "structure_gaps": ["缺背景|缺方案或技术|缺本人职责|缺结果 等，可空"],
      "fixes": ["可执行改法1", "可执行改法2"]
    }
  ]
}

## 字段口径
- work_years：可解析则填数字（年）；推不出填 null。
- start_ym / end_ym：尽量 YYYY-MM；「至今」用接近当前的月份；解析不出可空，规则层会再试 time_range。
- claims_lead：正文写了项目负责人 / 负责人等抬头则为 true。
- name_too_generic：名称过短过泛、看不出业务域则为 true（如仅「健康管家」）。
- aligns_target_role：仅对**近段**主项目有意义；与求职意向明显不符为 false；早期项目可 true；无意向则 null。
- category：工作交付 work；实习 internship；课程 course；个人 personal；不明 unknown。

## 规则
- 识别 1～3 个主项目；primary 宜少而深；数组须近段在前。
- 无岗位说明时，scope 必须写明通用技术面。
"""

ProjectsAssessor = Callable[[str, str | None], dict[str, Any]]
Month = tuple[int, int]  # (year, month)


@dataclass(frozen=True)
class CredibilityFlag:
    """一条存疑 / 辅导标记（P* 或 G1-T）。"""

    code: str
    note: str
    severity: str = "doubtful"


@dataclass(frozen=True)
class ProjectsResult:
    """一次项目审阅的落盘结果。"""

    run_dir: Path
    scope: str
    summary: str
    projects: list[dict[str, Any]]
    report_md: Path
    report_json: Path
    g1t_doubtful: bool = False
    g1t_note: str = ""
    credibility_flags: list[CredibilityFlag] = field(default_factory=list)

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


def _normalize_gaps(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    gaps: list[str] = []
    for entry in raw:
        text = str(entry or "").strip()
        if text:
            gaps.append(text)
        if len(gaps) >= 4:
            break
    return gaps


def _normalize_bool(raw: Any, *, default: bool | None = None) -> bool | None:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"true", "1", "yes", "是"}:
        return True
    if text in {"false", "0", "no", "否"}:
        return False
    return default


def _month_ord(month: Month) -> int:
    return month[0] * 12 + month[1]


def _parse_ym_token(raw: Any) -> Month | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if _PRESENT_RE.search(text):
        return (_REFERENCE_TODAY.year, _REFERENCE_TODAY.month)
    match = _YM_RE.search(text)
    if not match:
        return None
    year = int(match.group("y"))
    month = int(match.group("m"))
    if month < 1 or month > 12:
        return None
    return (year, month)


def parse_project_interval(
    *,
    start_ym: Any = None,
    end_ym: Any = None,
    time_range: str = "",
) -> tuple[Month, Month] | None:
    """解析项目起止月份；失败返回 None。"""
    start = _parse_ym_token(start_ym)
    end = _parse_ym_token(end_ym)
    if start and end:
        if _month_ord(end) < _month_ord(start):
            start, end = end, start
        return start, end
    text = str(time_range or "").strip()
    if not text:
        return None
    if _PRESENT_RE.search(text) and not end:
        end = (_REFERENCE_TODAY.year, _REFERENCE_TODAY.month)
    matches = list(_YM_RE.finditer(text))
    if not matches and end and start:
        return start, end
    if len(matches) >= 2:
        start = (
            int(matches[0].group("y")),
            int(matches[0].group("m")),
        )
        end = (
            int(matches[1].group("y")),
            int(matches[1].group("m")),
        )
    elif len(matches) == 1:
        start = (
            int(matches[0].group("y")),
            int(matches[0].group("m")),
        )
        end = end or start
    elif end and start:
        pass
    else:
        return None
    if start and end:
        if _month_ord(end) < _month_ord(start):
            start, end = end, start
        return start, end
    return None


def duration_months(interval: tuple[Month, Month]) -> int:
    """起止相隔月数：同月为 0，相邻月为 1；P2 以少于 2 为存疑。"""
    return _month_ord(interval[1]) - _month_ord(interval[0])


def intervals_overlap(a: tuple[Month, Month], b: tuple[Month, Month]) -> bool:
    return _month_ord(a[0]) <= _month_ord(b[1]) and _month_ord(b[0]) <= _month_ord(
        a[1]
    )


def _normalize_category(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    aliases = {
        "work": "work",
        "工作": "work",
        "internship": "internship",
        "实习": "internship",
        "course": "course",
        "课程": "course",
        "personal": "personal",
        "个人": "personal",
        "unknown": "unknown",
        "不明": "unknown",
    }
    return aliases.get(text, "unknown")


def _normalize_spans(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    spans: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        company = str(
            item.get("company") or item.get("label") or item.get("school") or ""
        ).strip()
        start = _parse_ym_token(item.get("start_ym") or item.get("start"))
        end = _parse_ym_token(item.get("end_ym") or item.get("end"))
        if not start or not end:
            continue
        if _month_ord(end) < _month_ord(start):
            start, end = end, start
        spans.append(
            {
                "label": company,
                "start_ym": f"{start[0]:04d}-{start[1]:02d}",
                "end_ym": f"{end[0]:04d}-{end[1]:02d}",
                "_interval": (start, end),
            }
        )
    return spans


def _normalize_profile_hint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    work_years_raw = raw.get("work_years")
    work_years: float | None
    try:
        work_years = (
            float(work_years_raw) if work_years_raw is not None else None
        )
    except (TypeError, ValueError):
        work_years = None
    return {
        "work_years": work_years,
        "target_role": str(raw.get("target_role") or "").strip(),
        "work_spans": _normalize_spans(raw.get("work_spans")),
        "education_spans": _normalize_spans(raw.get("education_spans")),
    }


def _name_looks_generic(name: str, flagged: bool | None) -> bool:
    if flagged is True:
        return True
    if flagged is False:
        return False
    compact = re.sub(r"\s+", "", name)
    # 过短且无常见系统/业务后缀 → 倾向过泛
    if len(compact) <= 4 and not re.search(
        r"系统|平台|引擎|助手|中台|网关|Agent|RAG|LLM", compact, re.I
    ):
        return True
    return False


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
        time_range = str(item.get("time_range") or "").strip()
        interval = parse_project_interval(
            start_ym=item.get("start_ym"),
            end_ym=item.get("end_ym"),
            time_range=time_range,
        )
        claims_lead = _normalize_bool(item.get("claims_lead"))
        if claims_lead is None:
            blob = " ".join(
                str(item.get(k) or "")
                for k in ("why_selected", "value_evidence", "name", "role_title")
            )
            claims_lead = bool(_LEAD_RE.search(blob))
        name_flag = _normalize_bool(item.get("name_too_generic"))
        align = _normalize_bool(item.get("aligns_target_role"), default=None)
        start_ym = (
            f"{interval[0][0]:04d}-{interval[0][1]:02d}" if interval else ""
        )
        end_ym = (
            f"{interval[1][0]:04d}-{interval[1][1]:02d}" if interval else ""
        )
        projects.append(
            {
                "name": name,
                "time_range": time_range,
                "start_ym": start_ym or str(item.get("start_ym") or "").strip(),
                "end_ym": end_ym or str(item.get("end_ym") or "").strip(),
                "employer": str(item.get("employer") or "").strip(),
                "category": _normalize_category(item.get("category")),
                "claims_lead": bool(claims_lead),
                "name_too_generic": _name_looks_generic(name, name_flag),
                "aligns_target_role": align,
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
                "structure_gaps": _normalize_gaps(item.get("structure_gaps")),
                "fixes": _normalize_fixes(item.get("fixes")),
                "_interval": interval,
                "method": "llm",
            }
        )
    return projects


def evaluate_g1t(projects: list[dict[str, Any]]) -> tuple[bool, str]:
    """近段（列表首项）含金量是否明显低于更早项目。"""
    if len(projects) < 2:
        return False, "仅一个主项目，未触发近浅远深比较。"
    near = projects[0]
    near_rank = _TIER_RANK.get(str(near.get("value_tier")), 2)
    for older in projects[1:]:
        older_rank = _TIER_RANK.get(str(older.get("value_tier")), 2)
        if near_rank < older_rank:
            return (
                True,
                (
                    f"近段「{near.get('name')}」含金量（{TIER_LABELS.get(str(near.get('value_tier')), near.get('value_tier'))}）"
                    f"低于更早的「{older.get('name')}」（{TIER_LABELS.get(str(older.get('value_tier')), older.get('value_tier'))}），"
                    "近浅远深存疑；宜调整主副、压缩旧项或理顺叙事。"
                ),
            )
    return False, "近段含金量不低于更早项目，符合 G1-T 期望。"


def career_window_start(
    work_spans: list[dict[str, Any]],
    edu_spans: list[dict[str, Any]],
) -> Month | None:
    """毕业 / 入职窗口起点：最早入职与最晚毕业取较早者；皆无则 None。"""
    candidates: list[Month] = []
    work_starts = [
        s["_interval"][0] for s in work_spans if s.get("_interval")
    ]
    if work_starts:
        candidates.append(min(work_starts, key=_month_ord))
    edu_ends = [s["_interval"][1] for s in edu_spans if s.get("_interval")]
    if edu_ends:
        candidates.append(max(edu_ends, key=_month_ord))
    if not candidates:
        return None
    return min(candidates, key=_month_ord)


def gap_months(earlier_end: Month, later_start: Month) -> int:
    """前一段结束月与后一段开始月之间的间隙月数（同月或重叠为 ≤0）。"""
    return _month_ord(later_start) - _month_ord(earlier_end)


def evaluate_credibility(
    projects: list[dict[str, Any]],
    profile_hint: dict[str, Any],
    *,
    job_description: str | None = None,
) -> list[CredibilityFlag]:
    """按 005 P1～P8 产出存疑清单（不含 G1-T）。"""
    flags: list[CredibilityFlag] = []
    work_years = profile_hint.get("work_years")
    target_role = str(profile_hint.get("target_role") or "").strip()
    has_target = bool(target_role or (job_description and str(job_description).strip()))
    work_spans = list(profile_hint.get("work_spans") or [])
    edu_spans = list(profile_hint.get("education_spans") or [])

    for project in projects:
        name = str(project.get("name") or "")
        if project.get("name_too_generic"):
            flags.append(
                CredibilityFlag(
                    code="P1",
                    note=f"「{name}」名称偏泛，宜补全业务域 / 系统形态，便于开场与检索。",
                )
            )
        interval = project.get("_interval")
        category = str(project.get("category") or "unknown")
        if (
            interval
            and category in {"work", "internship"}
            and duration_months(interval) < 2
        ):
            flags.append(
                CredibilityFlag(
                    code="P2",
                    note=(
                        f"「{name}」属工作 / 实习项目且可解析时长少于 2 个月，"
                        "宜核日期或说明短期轮岗 / 试点。"
                    ),
                )
            )

    if has_target and projects:
        near = projects[0]
        align = near.get("aligns_target_role")
        if align is False:
            flags.append(
                CredibilityFlag(
                    code="P3",
                    note=(
                        f"近段「{near.get('name')}」与求职意向 / 目标岗位明显不符，"
                        "早期项目可保留，但近段宜对齐主打叙事。"
                    ),
                )
            )

    if (
        isinstance(work_years, (int, float))
        and work_years <= 2
        and projects
        and all(bool(p.get("claims_lead")) for p in projects)
    ):
        flags.append(
            CredibilityFlag(
                code="P4",
                note=(
                    f"工作年限约 {work_years:g} 年且审阅范围内项目均写负责人类抬头，"
                    "存在负责人通胀存疑；宜核对手头职责用词。"
                ),
            )
        )

    # P5：同公司重叠对 ≥ 2
    by_employer: dict[str, list[tuple[str, tuple[Month, Month]]]] = {}
    for project in projects:
        employer = str(project.get("employer") or "").strip()
        interval = project.get("_interval")
        if not employer or not interval:
            continue
        by_employer.setdefault(employer, []).append(
            (str(project.get("name") or ""), interval)
        )
    for employer, items in by_employer.items():
        overlap_pairs = 0
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if intervals_overlap(items[i][1], items[j][1]):
                    overlap_pairs += 1
        if overlap_pairs >= 2:
            flags.append(
                CredibilityFlag(
                    code="P5",
                    note=(
                        f"「{employer}」下可解析项目重叠对数为 {overlap_pairs}（≥2），"
                        "轻度重叠可接受，过多宜理顺起止或并行写法。"
                    ),
                )
            )

    # P6：工作/实习项目落在任职段之外
    if work_spans:
        work_intervals = [s["_interval"] for s in work_spans if "_interval" in s]

        def _covered(interval: tuple[Month, Month]) -> bool:
            for span in work_intervals:
                if (
                    _month_ord(span[0]) <= _month_ord(interval[0])
                    and _month_ord(interval[1]) <= _month_ord(span[1])
                ):
                    return True
            return False

        for project in projects:
            category = str(project.get("category") or "unknown")
            if category not in {"work", "internship", "unknown"}:
                continue
            interval = project.get("_interval")
            if not interval:
                continue
            if not _covered(interval):
                flags.append(
                    CredibilityFlag(
                        code="P6",
                        note=(
                            f"「{project.get('name')}」时间未落入已写明的任职 / 实习段，"
                            "存在跨职或归属不清存疑；课程 / 个人项目宜标明来源。"
                        ),
                    )
                )

    # P7：落在在校期
    if edu_spans:
        for project in projects:
            interval = project.get("_interval")
            if not interval:
                continue
            for edu in edu_spans:
                edu_iv = edu.get("_interval")
                if edu_iv and intervals_overlap(interval, edu_iv):
                    flags.append(
                        CredibilityFlag(
                            code="P7",
                            note=(
                                f"「{project.get('name')}」时间与在读区间重叠，"
                                "宜标注课程设计 / 实习 / 个人项目，勿装成正式在职交付。"
                            ),
                        )
                    )
                    break

    # P8：毕业 / 入职后，工作·实习项目相邻间隙 ≥ 2 个月
    window = career_window_start(work_spans, edu_spans)
    if window is not None:
        timeline: list[tuple[str, tuple[Month, Month]]] = []
        for project in projects:
            category = str(project.get("category") or "unknown")
            if category not in {"work", "internship"}:
                continue
            interval = project.get("_interval")
            if not interval:
                continue
            # 整段都在窗口起点之前 → 视为在校期轴，不入 P8
            if _month_ord(interval[1]) < _month_ord(window):
                continue
            timeline.append((str(project.get("name") or ""), interval))
        timeline.sort(key=lambda item: (_month_ord(item[1][0]), _month_ord(item[1][1])))
        for i in range(len(timeline) - 1):
            name_a, iv_a = timeline[i]
            name_b, iv_b = timeline[i + 1]
            gap = gap_months(iv_a[1], iv_b[0])
            # 间隙须落在窗口内：后段起点不早于窗口，或前段结束已过窗口
            gap_after_window = _month_ord(iv_b[0]) >= _month_ord(window)
            if gap >= 2 and gap_after_window:
                flags.append(
                    CredibilityFlag(
                        code="P8",
                        note=(
                            f"毕业 / 入职后，「{name_a}」与「{name_b}」之间可解析空窗约 {gap} 个月（≥2），"
                            "宜核漏写项目、日期笔误，或说明空窗原因。"
                        ),
                    )
                )

    return flags


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
        f"请识别最多 {MAX_PROJECTS} 个主项目，按结束时间倒序给出含金量与难度档（high/mid/low），"
        "检查结构缺口，并填写 profile_hint 与 P1～P8 相关字段。",
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
            profile_hint = _normalize_profile_hint(parsed.get("profile_hint"))
            return {
                "scope": scope,
                "summary": summary,
                "projects": projects,
                "profile_hint": profile_hint,
            }
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


def _strip_private(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in projects:
        row = {k: v for k, v in item.items() if not str(k).startswith("_")}
        cleaned.append(row)
    return cleaned


def _markdown_report(
    *,
    scope: str,
    summary: str,
    projects: list[dict[str, Any]],
    g1t_doubtful: bool,
    g1t_note: str,
    credibility_flags: list[CredibilityFlag],
) -> str:
    lines = [
        "# 项目审阅（含金量 / 难度档）",
        "",
        "> 本文件由 `review-projects` 根据简历文本生成。"
        "**不替代「判能不能投」**；**不含薪资匹配**。"
        "`resume` 完整模式会跑本步；`--triage` 可关。",
        "",
        f"**范围**：{scope}",
        "",
        f"**摘要**：{summary}",
        "",
        f"**G1-T（近段宜更高）**：{'存疑' if g1t_doubtful else '符合期望'} — {g1t_note}",
        "",
    ]
    if credibility_flags:
        lines.append("**存疑清单（辅导，非合格线）**：")
        lines.append("")
        for flag in credibility_flags:
            lines.append(f"- **{flag.code}**（存疑 / 辅导）：{flag.note}")
        lines.append("")
    else:
        lines.append("**存疑清单**：未触发 P1～P8。")
        lines.append("")
    lines.extend(
        [
            f"**声明**：{DISCLAIMER}",
            "",
        ]
    )
    for project in projects:
        lines.append(f"## 项目：{project['name']}")
        lines.append("")
        if project.get("time_range"):
            lines.append(f"- **时间**：{project['time_range']}")
        if project.get("employer"):
            lines.append(f"- **归属**：{project['employer']}")
        lines.append(f"- **角色**：{_role_zh(str(project['role']))}")
        if project.get("claims_lead"):
            lines.append("- **抬头**：写有负责人类表述")
        if project.get("name_too_generic"):
            lines.append("- **名称**：偏泛（P1）")
        lines.append(f"- **为何选入**：{project['why_selected']}")
        lines.append(
            f"- **含金量档**：{_tier_zh(str(project['value_tier']))}"
            f"（{project['value_evidence']}）"
        )
        lines.append(
            f"- **难度档**：{_tier_zh(str(project['difficulty_tier']))}"
            f"（{project['difficulty_evidence']}）"
        )
        gaps = project.get("structure_gaps") or []
        if gaps:
            lines.append("- **结构缺口**：" + "；".join(str(g) for g in gaps))
        else:
            lines.append("- **结构**：背景 / 方案 / 职责 / 结果大致可辨")
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

    text_for_check = _prefer_normalized_body(run_dir, stem, body)
    assessor = projects_assessor or default_projects_assessor
    assessed = assessor(text_for_check, job_description)
    projects = list(assessed.get("projects") or [])
    if not projects:
        raise ReviewProjectsError("未得到任何项目审阅结果。")
    scope = str(assessed.get("scope") or DEFAULT_SCOPE)
    summary = str(assessed.get("summary") or "见各项目档次与依据。")
    profile_hint = _normalize_profile_hint(assessed.get("profile_hint"))

    # injected assessor 可能尚未规范化
    if projects_assessor is not None:
        projects = _normalize_projects({"projects": projects})
        for item in projects:
            item["method"] = "injected"

    g1t_doubtful, g1t_note = evaluate_g1t(projects)
    credibility_flags = evaluate_credibility(
        projects, profile_hint, job_description=job_description
    )
    if g1t_doubtful:
        credibility_flags = [
            CredibilityFlag(code="G1-T", note=g1t_note),
            *credibility_flags,
        ]

    public_projects = _strip_private(projects)
    report_md = run_dir / f"{stem}.projects.md"
    report_json = run_dir / f"{stem}.projects.json"
    record = {
        "tool": "review-projects",
        "judges_content": False,
        "evaluates_layout": False,
        "includes_salary": False,
        "in_resume_full_mode": True,
        "not_in_triage_by_default": True,
        "scope": scope,
        "summary": summary,
        "g1t_doubtful": g1t_doubtful,
        "g1t_note": g1t_note,
        "credibility_flags": [
            {"code": f.code, "severity": f.severity, "note": f.note}
            for f in credibility_flags
        ],
        "profile_hint": {
            "work_years": profile_hint.get("work_years"),
            "target_role": profile_hint.get("target_role"),
            "work_spans": [
                {
                    "label": s.get("label"),
                    "start_ym": s.get("start_ym"),
                    "end_ym": s.get("end_ym"),
                }
                for s in profile_hint.get("work_spans") or []
            ],
            "education_spans": [
                {
                    "label": s.get("label"),
                    "start_ym": s.get("start_ym"),
                    "end_ym": s.get("end_ym"),
                }
                for s in profile_hint.get("education_spans") or []
            ],
        },
        "disclaimer": DISCLAIMER,
        "standard": "docs/04-standard/005_project-review_项目审阅口径.md",
        "input": source_label,
        "text_sha256": _sha256_text(text_for_check),
        "used_normalized": (run_dir / f"{stem}.resume.norm.md").is_file(),
        "projects": public_projects,
        "targets": {"max_projects": MAX_PROJECTS, "max_fixes_per_project": MAX_FIXES},
        "method": {
            "projects": "llm" if projects_assessor is None else "injected",
            "g1t": "rule",
            "credibility": "rule+llm_fields",
        },
    }
    if job_description:
        record["job_description_provided"] = True

    report_md.write_text(
        _markdown_report(
            scope=scope,
            summary=summary,
            projects=public_projects,
            g1t_doubtful=g1t_doubtful,
            g1t_note=g1t_note,
            credibility_flags=credibility_flags,
        ),
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
        projects=public_projects,
        report_md=report_md,
        report_json=report_json,
        g1t_doubtful=g1t_doubtful,
        g1t_note=g1t_note,
        credibility_flags=credibility_flags,
    )
