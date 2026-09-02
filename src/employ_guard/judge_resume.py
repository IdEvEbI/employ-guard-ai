"""判内容能不能投。对照 docs/04-standard/004 §2；不评价排版。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from employ_guard.llm import LLMError, chat_completion
from employ_guard.paths import output_run_dir, resolve_input_file

PASS_CODES = tuple(f"C{i}" for i in range(1, 10))
LEVEL_CODES = tuple(f"H{i}" for i in range(1, 9))
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

SYSTEM_PROMPT = """你是简历「内容」检查员。只根据简历文本判断内容上能不能投递过初筛。
禁止：评价排版或版式；出练习题；打百分制分数；发明 004 以外的「好简历」标准；用 Java 全栈 / Spring 等非大模型学科要求。
必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它说明。

## 输出字段（必须齐全）
{
  "scope": "评价范围说明（无岗位说明时用：通用大模型应用 / 应用算法面初筛，非针对某一企业）",
  "pass_line": [
    {"id": "C1", "pass": true, "doubtful": false, "note": "对照下方 C1 定义，可执行说明"},
    {"id": "C2", "pass": true, "doubtful": false, "note": "..."},
    ... C3 到 C9 同样结构 ...
  ],
  "level_line": [
    {"id": "H1", "signal": true, "note": "只谈履历相关度"},
    ... H2 到 H8 ...
  ],
  "main_blockers": []
}

## 内容合格线（任一 pass=false → 内容未合格；doubtful 不自动判未合格）
- C1 岗位方向可辨：全文能判断为大模型应用或应用算法；不强制「求职意向」字段。
- C2 可联系：有可用手机或邮箱至少一项；不因 QQ 邮箱判不合格。
- C3 算法+RAG+Agent 证据齐全：须同时具备三类可追问证据——① 算法/模型（微调、Prompt、eval/Harness 等）；② RAG（链路至少两段，非仅向量库名词）；③ Agent（规划、Tool/MCP、记忆、降级等且写明本人职责）。缺一类 → pass=false。
- C4 至少 1 个可追问项目：含背景/问题、职责、技术。
- C5 职责可核实：有具体动作，非仅「熟悉/了解/负责推进」。
- C6 结果可追问：有业务结果、效率、指标或规模等可追问表述；不强制量化数字。
- C7 时间线基本自洽：项目时间在在职段内。年龄/学历/工龄互算明显不通 → doubtful=true、pass 仍为 true（存疑，老师复核）。空窗、频繁极短任不单独 pass=false。不核验与档案是否一致。
- C8 技能与项目不严重脱节：大段技能无项目支撑 → pass=false 或 doubtful=true（存疑时 pass=true）。
- C9 工作履历须有 AI 相关主业支撑：个人项目为主且工作履历非 AI → pass=false。

## 内容水平线（仅当全部 C 项 pass=true 时填写 H1～H8；signal 可为 false）
- H1 履历相关度；H2 链路完整度；H3 工程化；H4 评测与量化；H5 场景深度；H6 信息密度；H7 市场加分项；H8 履历稳定性。
- H 项 note 禁止改写为排版或「能不能投」的替代结论。

## 未过合格线
- main_blockers 列出导致 pass=false 的 C 编号与一句话原因（如「C3：缺 Agent 可追问证据」）。
- level_line 仍输出八项但 signal 可全 false，或 note 写「未过合格线，不比较水平」——实现侧会在未过合格线时清空水平线。

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
                "note": str(item.get("note") or "未返回该项"),
                "method": "llm",
            }
        )
    return result


def _normalize_level_line(data: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("id")): item
        for item in data.get("level_line", [])
        if isinstance(item, dict)
    }
    return [
        {
            "id": code,
            "signal": bool(by_id.get(code, {}).get("signal", False)),
            "note": str(by_id.get(code, {}).get("note") or "未返回该项"),
            "method": "llm",
        }
        for code in LEVEL_CODES
    ]


def default_content_assessor(resume_text: str, job_description: str | None) -> dict[str, Any]:
    """把简历文本发给 LLM，解析 C / H 项。"""
    user_parts = ["以下是简历正文。", "", resume_text, ""]
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
            return {
                "scope": str(parsed.get("scope") or DEFAULT_SCOPE),
                "pass_line": _normalize_pass_line(parsed),
                "level_line": _normalize_level_line(parsed),
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
        for note in doubtful_items:
            lines.append(f"- {note}")

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
                "| 编号 | 信号 | 说明 |",
                "| ---- | ---- | ---- |",
            ]
        )
        for item in level_line:
            mark = "有" if item.get("signal") else "弱 / 无"
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
) -> JudgeResult:
    """判内容，写出 `{stem}.judge.md` / `{stem}.judge.json`，返回结果。"""
    run_dir, stem, body, source_label = resolve_resume_text(source, root=root)
    if not body.strip():
        raise JudgeResumeError("简历正文为空，无法判断能不能投。")

    assessor = content_assessor or default_content_assessor
    assessed = assessor(body, job_description)
    pass_line = list(assessed.get("pass_line") or [])
    level_line = list(assessed.get("level_line") or [])
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
