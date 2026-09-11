"""从前置分项产物汇总进 judge（R25）。不替代各工具单独再跑。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

DATE_RANGE_RE = re.compile(
    r"(?P<y1>20\d{2})\s*[.年/\-]\s*(?P<m1>1[0-2]|0?[1-9])"
    r"\s*[-–—至到~]\s*"
    r"(?:"
    r"(?P<ongoing>至今|现在|present|current)"
    r"|"
    r"(?P<y2>20\d{2})\s*[.年/\-]\s*(?P<m2>1[0-2]|0?[1-9])"
    r")",
    re.IGNORECASE,
)


def _clean_note(note: str) -> str:
    text = str(note or "").strip()
    text = re.sub(
        r"\b(?:pass|doubtful|level)\s*=\s*(?:true|false|high|mid|low)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", text).strip(" ；;") or "未返回说明。"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class UpstreamBundle:
    """同目录前置产物（缺文件则为 None）。"""

    profile: dict[str, Any] | None = None
    skills: dict[str, Any] | None = None
    projects: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    writing: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def has_profile(self) -> bool:
        return self.profile is not None


def load_upstream(run_dir: Path, stem: str) -> UpstreamBundle:
    """读取同 stem 的前置 JSON。"""
    bundle = UpstreamBundle()
    mapping = {
        "profile": run_dir / f"{stem}.profile.json",
        "skills": run_dir / f"{stem}.skills.json",
        "projects": run_dir / f"{stem}.projects.json",
        "layout": run_dir / f"{stem}.layout.json",
        "writing": run_dir / f"{stem}.writing.json",
    }
    for key, path in mapping.items():
        data = _read_json(path)
        setattr(bundle, key, data)
        if data is not None:
            bundle.sources.append(path.name)
    return bundle


def _status_pass_doubtful(status: str | None) -> tuple[bool, bool]:
    text = str(status or "").strip().lower()
    if text == "fail":
        return False, False
    if text == "doubtful":
        return True, True
    return True, False


def _set_c_item(
    pass_line: list[dict[str, Any]],
    code: str,
    *,
    passed: bool,
    doubtful: bool,
    note: str,
    method: str,
) -> list[dict[str, Any]]:
    note = _clean_note(note)
    updated: list[dict[str, Any]] = []
    found = False
    for item in pass_line:
        if item.get("id") != code:
            updated.append(item)
            continue
        found = True
        updated.append(
            {
                **item,
                "pass": passed,
                "doubtful": bool(doubtful),
                "note": note,
                "method": method,
                "source": "upstream",
            }
        )
    if not found:
        updated.append(
            {
                "id": code,
                "pass": passed,
                "doubtful": bool(doubtful),
                "note": note,
                "method": method,
                "source": "upstream",
            }
        )
    return updated


def apply_profile_c1(
    pass_line: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """C1 以 check-profile 为准。"""
    status = str(profile.get("status") or "fail")
    passed, doubtful = _status_pass_doubtful(status)
    role = str(profile.get("target_role") or "").strip() or "（未抽出岗位表述）"
    evidence = str(profile.get("homepage_evidence") or "").strip()
    notes = profile.get("notes") or []
    extra = "；".join(str(n) for n in notes if str(n).strip())
    if status == "pass":
        note = f"汇总自 check-profile（过本项）。读到的岗位类表述：{role}。"
        if evidence:
            note += f" 依据：{evidence}"
    elif status == "doubtful":
        note = f"汇总自 check-profile（存疑）。岗位类表述：{role}。"
        if evidence:
            note += f" 依据：{evidence}"
    else:
        note = (
            f"汇总自 check-profile（未过）。"
            f"{evidence or '首页基本信息未见清晰岗位类表述。'}"
        )
    if extra:
        note = f"{note.rstrip('。')}。{extra}"
    return _set_c_item(
        pass_line,
        "C1",
        passed=passed,
        doubtful=doubtful,
        note=note,
        method="upstream:profile",
    )


def apply_skills_projects_c3_c8(
    pass_line: list[dict[str, Any]],
    *,
    skills: dict[str, Any] | None,
    projects: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """C3 / C8 主要映射 skills + projects。"""
    updated = pass_line

    if skills is not None:
        status = str(skills.get("status") or "fail")
        passed, doubtful = _status_pass_doubtful(status)
        coverage = (
            skills.get("project_coverage")
            if isinstance(skills.get("project_coverage"), dict)
            else {}
        )
        market = (
            skills.get("market_alignment")
            if isinstance(skills.get("market_alignment"), dict)
            else {}
        )
        note = (
            f"汇总自 check-skills（总评 {status}）。"
            f"与项目覆盖：{coverage.get('note') or '未写'}；"
            f"市场高频：{market.get('note') or '未写'}。"
        )
        if status == "fail" or str(coverage.get("status") or "") == "fail":
            passed, doubtful = False, False
        updated = _set_c_item(
            updated,
            "C8",
            passed=passed,
            doubtful=doubtful
            or str(coverage.get("status") or "") == "doubtful"
            or str(market.get("status") or "") == "doubtful",
            note=note,
            method="upstream:skills",
        )

    if projects is not None or skills is not None:
        parts: list[str] = []
        c3_pass = True
        c3_doubt = False
        if projects is not None:
            if projects.get("llm_degraded"):
                c3_doubt = True
                parts.append(
                    "项目审阅 LLM 降级，未给出含金量 / 难度档（不因此写成不能投）"
                )
            else:
                plist = projects.get("projects") or []
                if isinstance(plist, list) and plist:
                    tiers = []
                    gaps_n = 0
                    typed = [p for p in plist if isinstance(p, dict)]
                    for item in typed:
                        name = str(item.get("name") or "项目")
                        vt = str(item.get("value_tier") or "mid")
                        dt = str(item.get("difficulty_tier") or "mid")
                        tiers.append(f"{name}含金量{vt}/难度{dt}")
                        gaps = item.get("structure_gaps") or []
                        if isinstance(gaps, list):
                            gaps_n += len(gaps)
                    parts.append("；".join(tiers))
                    if gaps_n:
                        c3_doubt = True
                        parts.append(f"结构缺口合计 {gaps_n} 处（辅导）")
                    if typed and all(
                        str(item.get("value_tier")) == "low"
                        and str(item.get("difficulty_tier")) == "low"
                        for item in typed
                    ):
                        c3_pass = False
                        parts.append("主项目含金量与难度均为低，技术证据偏薄")
                else:
                    c3_doubt = True
                    parts.append("项目审阅未识别到主项目列表")
        if skills is not None:
            market = (
                skills.get("market_alignment")
                if isinstance(skills.get("market_alignment"), dict)
                else {}
            )
            if market.get("note"):
                parts.append(f"技能市场向：{market.get('note')}")
            if str(skills.get("status") or "") == "fail":
                c3_doubt = True
        note = "汇总自 review-projects / check-skills。" + "；".join(parts)
        updated = _set_c_item(
            updated,
            "C3",
            passed=c3_pass,
            doubtful=c3_doubt,
            note=note,
            method="upstream:skills+projects",
        )

    if projects is not None:
        plist = projects.get("projects") or []
        gap_notes: list[str] = []
        if isinstance(plist, list):
            for item in plist:
                if not isinstance(item, dict):
                    continue
                gaps = item.get("structure_gaps") or []
                if gaps:
                    gap_notes.append(
                        f"{item.get('name') or '项目'}缺："
                        + "、".join(str(g) for g in gaps)
                    )
        flags = projects.get("credibility_flags") or []
        p4 = [
            f
            for f in flags
            if isinstance(f, dict) and str(f.get("code")) == "P4"
        ]
        if gap_notes:
            updated = _set_c_item(
                updated,
                "C4",
                passed=True,
                doubtful=True,
                note="汇总自 review-projects 结构缺口：" + "；".join(gap_notes),
                method="upstream:projects",
            )
        if p4:
            updated = _set_c_item(
                updated,
                "C5",
                passed=True,
                doubtful=True,
                note="汇总自 review-projects："
                + "；".join(str(f.get("note") or "P4") for f in p4),
                method="upstream:projects",
            )

    return updated


def _find_future_end_dates(resume_text: str, *, today: date) -> list[str]:
    as_of_months = today.year * 12 + today.month
    hits: list[str] = []
    for match in DATE_RANGE_RE.finditer(resume_text):
        raw = match.group(0).strip()
        if match.group("ongoing"):
            continue
        end_year = int(match.group("y2"))
        end_month = int(match.group("m2"))
        if end_month < 1 or end_month > 12:
            continue
        if end_year * 12 + end_month > as_of_months:
            hits.append(raw)
    return hits


def build_time_summary(
    *,
    resume_text: str,
    projects: dict[str, Any] | None,
    today: date | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """时间检测汇总：返回 (说明列表, 结构化条)。"""
    as_of = today or date.today()
    lines: list[str] = []
    rows: list[dict[str, Any]] = []

    futures = _find_future_end_dates(resume_text, today=as_of)
    if futures:
        note = "结束年月晚于检查当日：" + "；".join(futures)
        lines.append(note)
        rows.append({"code": "FUTURE_END", "severity": "doubtful", "note": note})

    if projects is not None:
        for flag in projects.get("credibility_flags") or []:
            if not isinstance(flag, dict):
                continue
            code = str(flag.get("code") or "")
            if code in {"P2", "P5", "P6", "P7", "P8", "G1-T"}:
                note = str(flag.get("note") or code)
                lines.append(f"{code}：{note}")
                rows.append(
                    {
                        "code": code,
                        "severity": str(flag.get("severity") or "doubtful"),
                        "note": note,
                    }
                )

    if not lines:
        lines.append("未发现规则层时间硬伤；若有项目时段存疑见上表。")
    return lines, rows


def apply_time_to_c7(
    pass_line: list[dict[str, Any]],
    time_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把时间汇总写入 C7（存疑为主）。"""
    doubtful_notes = [
        str(r.get("note"))
        for r in time_rows
        if r.get("severity") == "doubtful" and r.get("note")
    ]
    if not doubtful_notes:
        return pass_line
    note = "时间检测汇总：" + "；".join(doubtful_notes)
    return _set_c_item(
        pass_line,
        "C7",
        passed=True,
        doubtful=True,
        note=note,
        method="upstream:time",
    )


def layout_writing_summary(
    upstream: UpstreamBundle,
) -> tuple[bool | None, bool | None, list[str]]:
    """返回 (layout_pass, writing_pass, 说明行)。None 表示本趟无产物。"""
    notes: list[str] = []
    layout_pass: bool | None = None
    writing_pass: bool | None = None
    if upstream.layout is not None:
        layout_pass = bool(upstream.layout.get("layout_pass"))
        notes.append(
            "排版："
            + ("达标" if layout_pass else "未达标")
            + "（汇总自 check-layout）"
        )
    else:
        notes.append("排版：本趟无 layout 产物（单独跑 judge 或未跑查排版）")
    if upstream.writing is not None:
        writing_pass = bool(upstream.writing.get("writing_pass"))
        findings = upstream.writing.get("findings") or []
        n = len(findings) if isinstance(findings, list) else 0
        notes.append(
            "文字表达："
            + ("无明显问题" if writing_pass else f"有待改进 {n} 条")
            + "（汇总自 check-writing）"
        )
    else:
        notes.append("文字表达：本趟无 writing 产物（排查关闭或不存在）")
    return layout_pass, writing_pass, notes


def overlay_pass_line(
    pass_line: list[dict[str, Any]],
    upstream: UpstreamBundle,
    resume_text: str,
    *,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """按 R25 口径覆盖 pass_line。"""
    updated = list(pass_line)
    if upstream.profile is not None:
        updated = apply_profile_c1(updated, upstream.profile)
    updated = apply_skills_projects_c3_c8(
        updated, skills=upstream.skills, projects=upstream.projects
    )
    time_lines, time_rows = build_time_summary(
        resume_text=resume_text,
        projects=upstream.projects,
        today=today,
    )
    updated = apply_time_to_c7(updated, time_rows)
    return updated, time_lines, time_rows
