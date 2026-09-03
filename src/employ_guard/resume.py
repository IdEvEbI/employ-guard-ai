"""老师命令：投前看简历（布局路径与文本路径并行，已有结果则跳过）。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from employ_guard.check_layout import (
    CheckLayoutError,
    LayoutResult,
    VisualAssessor,
    check_layout,
)
from employ_guard.check_writing import (
    CheckWritingError,
    WritingAssessor,
    WritingResult,
    check_writing,
)
from employ_guard.draft_questions import (
    DraftQuestionsError,
    QuestionsAssessor,
    QuestionsResult,
    draft_questions,
)
from employ_guard.judge_resume import (
    ContentAssessor,
    JudgeResumeError,
    JudgeResult,
    judge_resume,
)
from employ_guard.paths import output_run_dir, resolve_input_file
from employ_guard.pdf_to_images import (
    RECORD_NAME as PDF_TO_IMAGES_RECORD,
    PdfToImagesError,
    render_pdf_to_images,
)
from employ_guard.read_resume import ReadResumeError, extract_resume_text

StepStatus = Literal["ran", "skipped", "failed", "disabled"]
ProgressHook = Callable[[str], None]

STEP_TOTAL = 6
STEP_ORDER: tuple[str, ...] = (
    "pdf-to-images",
    "check-layout",
    "read-resume",
    "check-writing",
    "judge-resume",
    "draft-questions",
)
STEP_SLOTS: dict[str, int] = {name: i for i, name in enumerate(STEP_ORDER, start=1)}
STEP_LABELS: dict[str, str] = {
    "pdf-to-images": "PDF 出图",
    "check-layout": "查排版",
    "read-resume": "读简历",
    "check-writing": "查文字表达",
    "judge-resume": "判能不能投",
    "draft-questions": "出练习题",
}
STATUS_LABELS: dict[str, str] = {
    "ran": "已跑",
    "skipped": "跳过",
    "failed": "失败",
    "disabled": "关闭",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256_field(path: Path) -> str | None:
    """读取记录里的 sha256；文件缺失或字段缺失则返回 None。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("sha256") or data.get("source_pdf_sha256")
    return str(value) if value else None


def _images_match_pdf(run_dir: Path, current_sha: str) -> bool:
    return _json_sha256_field(run_dir / PDF_TO_IMAGES_RECORD) == current_sha


def _resume_text_match_pdf(run_dir: Path, stem: str, current_sha: str) -> bool:
    return _json_sha256_field(run_dir / f"{stem}.resume.json") == current_sha


@dataclass
class StepOutcome:
    """某一步的执行记录。"""

    name: str
    status: StepStatus
    detail: str = ""
    path: Path | None = None
    elapsed_ms: int | None = None


class _StepClock:
    """步骤开始 / 结束提示与耗时；并行时用固定槽位编号，线程安全。"""

    def __init__(self, progress: ProgressHook | None, *, total: int = STEP_TOTAL) -> None:
        self._progress = progress
        self.total = total
        self._lock = threading.Lock()

    def start(self, name: str) -> float:
        index = STEP_SLOTS[name]
        label = STEP_LABELS.get(name, name)
        self._emit(f"[{index}/{self.total}] 正在 {label} …")
        return time.perf_counter()

    def finish(self, outcome: StepOutcome, started: float) -> StepOutcome:
        elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
        outcome.elapsed_ms = elapsed_ms
        index = STEP_SLOTS[outcome.name]
        label = STEP_LABELS.get(outcome.name, outcome.name)
        status = STATUS_LABELS.get(outcome.status, outcome.status)
        suffix = f"：{outcome.detail}" if outcome.detail else ""
        self._emit(
            f"[{index}/{self.total}] {label} · {status} · {elapsed_ms} ms{suffix}"
        )
        return outcome

    def _emit(self, message: str) -> None:
        if self._progress is None:
            return
        with self._lock:
            self._progress(message)


@dataclass
class ResumeRunResult:
    """一次 `resume` 编排的汇总。"""

    pdf_path: Path
    run_dir: Path
    steps: list[StepOutcome] = field(default_factory=list)
    layout_pass: bool | None = None
    writing_pass: bool | None = None
    content_pass: bool | None = None
    questions_count: int | None = None
    exit_code: int = 0
    hard_error: str | None = None
    triage: bool = False
    actions: list[str] = field(default_factory=list)
    brief_path: Path | None = None


class ResumeError(Exception):
    """输入无效，或出图 / 读文本等硬失败（不得写成内容不能投）。"""


@dataclass
class _PathBundle:
    """单条并行路径的步骤与结论。"""

    steps: list[StepOutcome] = field(default_factory=list)
    layout_pass: bool | None = None
    writing_pass: bool | None = None
    content_pass: bool | None = None
    questions_count: int | None = None
    hard_error: str | None = None


def _list_page_images(pages_dir: Path) -> list[Path]:
    pages = sorted(pages_dir.glob("*.png"))
    if not pages:
        pages = sorted(pages_dir.glob("*.jpg")) + sorted(pages_dir.glob("*.jpeg"))
    return pages


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeError(f"无法读取已有结果 {path.name}：{exc}") from exc
    if not isinstance(data, dict):
        raise ResumeError(f"已有结果格式无效：{path.name}")
    return data


def _pass_label(value: bool | None, *, skipped: str = "未查") -> str:
    if value is True:
        return "达标"
    if value is False:
        return "未达标"
    return skipped


def _collect_actions(run_dir: Path, stem: str, *, limit: int = 3) -> list[str]:
    """从排版 / 内容报告收集可执行改法，最多 limit 条。"""
    tips: list[str] = []
    seen: set[str] = set()

    def _add(note: str) -> None:
        text = note.strip()
        if not text or text in seen:
            return
        seen.add(text)
        tips.append(text)

    layout_json = run_dir / f"{stem}.layout.json"
    if layout_json.is_file():
        data = _read_json(layout_json)
        if not data.get("layout_pass"):
            for tip in data.get("revision_tips") or []:
                _add(str(tip))
            for item in data.get("pass_line") or []:
                if isinstance(item, dict) and not item.get("pass"):
                    code = item.get("id") or "?"
                    note = item.get("note") or "见排版报告"
                    _add(f"排版 {code}：{note}")

    judge_json = run_dir / f"{stem}.judge.json"
    if judge_json.is_file():
        data = _read_json(judge_json)
        if not data.get("content_pass"):
            for note in data.get("main_blockers") or []:
                _add(str(note))
            for item in data.get("pass_line") or []:
                if isinstance(item, dict) and not item.get("pass"):
                    code = item.get("id") or "?"
                    note = item.get("note") or "见内容报告"
                    _add(f"内容 {code}：{note}")

    return tips[:limit]


def write_brief(result: ResumeRunResult) -> Path:
    """写出短教练摘要；不合并合格线口径。"""
    stem = result.pdf_path.stem
    path = result.run_dir / f"{stem}.brief.md"
    if result.triage and result.writing_pass is None:
        writing_line = "排查模式未查"
    elif result.writing_pass is True:
        writing_line = "无明显问题"
    elif result.writing_pass is False:
        writing_line = "有待改进（不自动等同不能投）"
    else:
        writing_line = "未得到结论"

    lines = [
        "# 投前看简历 · 教练摘要",
        "",
        f"- 输入：`{result.pdf_path.name}`",
        f"- 模式：{'排查（triage）' if result.triage else '完整'}",
        f"- 排版：{_pass_label(result.layout_pass)}",
        f"- 内容：{_pass_label(result.content_pass)}",
        f"- 文字表达：{writing_line}",
        "",
        "## 建议先改（最多 3 条）",
        "",
    ]
    if result.actions:
        for tip in result.actions:
            lines.append(f"- {tip}")
    else:
        if result.layout_pass and result.content_pass:
            lines.append("- 排版与内容均达标；可按详细报告微调水平线项。")
        else:
            lines.append("- 见下方详细报告中的未过项。")

    lines.extend(
        [
            "",
            "## 详细报告",
            "",
            f"- 排版：`{stem}.layout.md`",
            f"- 内容：`{stem}.judge.md`",
        ]
    )
    if result.writing_pass is not None:
        lines.append(f"- 文字表达：`{stem}.writing.md`")
    if result.questions_count is not None:
        lines.append(f"- 练习题：`{stem}.questions.md`（{result.questions_count} 道，推测）")
    elif result.triage:
        lines.append("- 练习题：排查模式未出")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _load_layout_from_disk(run_dir: Path, stem: str) -> tuple[bool, Path]:
    report_json = run_dir / f"{stem}.layout.json"
    report_md = run_dir / f"{stem}.layout.md"
    if not report_json.is_file():
        raise ResumeError(f"缺少排版结果：{report_json}")
    data = _read_json(report_json)
    return bool(data.get("layout_pass")), report_md if report_md.is_file() else report_json


def _load_writing_from_disk(run_dir: Path, stem: str) -> tuple[bool, Path]:
    report_json = run_dir / f"{stem}.writing.json"
    report_md = run_dir / f"{stem}.writing.md"
    if not report_json.is_file():
        raise ResumeError(f"缺少文字表达结果：{report_json}")
    data = _read_json(report_json)
    return bool(data.get("writing_pass")), report_md if report_md.is_file() else report_json


def _load_judge_from_disk(run_dir: Path, stem: str) -> tuple[bool, Path]:
    report_json = run_dir / f"{stem}.judge.json"
    report_md = run_dir / f"{stem}.judge.md"
    if not report_json.is_file():
        raise ResumeError(f"缺少内容判断结果：{report_json}")
    data = _read_json(report_json)
    return bool(data.get("content_pass")), report_md if report_md.is_file() else report_json


def _load_questions_from_disk(run_dir: Path, stem: str) -> tuple[int, Path]:
    report_json = run_dir / f"{stem}.questions.json"
    report_md = run_dir / f"{stem}.questions.md"
    if not report_json.is_file():
        raise ResumeError(f"缺少练习题结果：{report_json}")
    data = _read_json(report_json)
    questions = data.get("questions") or []
    count = len(questions) if isinstance(questions, list) else 0
    return count, report_md if report_md.is_file() else report_json


def _run_layout_path(
    *,
    pdf_path: Path,
    run_dir: Path,
    stem: str,
    current_sha: str,
    force: bool,
    dpi: int,
    root: Path | None,
    clock: _StepClock,
    visual_assessor: VisualAssessor | None,
) -> _PathBundle:
    """出图 → 查排版。"""
    bundle = _PathBundle()

    def _add(outcome: StepOutcome, started: float) -> None:
        bundle.steps.append(clock.finish(outcome, started))

    t0 = clock.start("pdf-to-images")
    pages_dir = run_dir / "pages"
    existing_pages = _list_page_images(pages_dir) if pages_dir.is_dir() else []
    can_skip_images = (
        not force and bool(existing_pages) and _images_match_pdf(run_dir, current_sha)
    )
    images_reran = False
    if can_skip_images:
        _add(
            StepOutcome(
                name="pdf-to-images",
                status="skipped",
                detail=f"已有 {len(existing_pages)} 张页图（PDF 哈希一致）",
                path=pages_dir,
            ),
            t0,
        )
    else:
        images_reran = True
        try:
            pages_dir = render_pdf_to_images(pdf_path, dpi=dpi, root=root)
        except PdfToImagesError as exc:
            _add(StepOutcome(name="pdf-to-images", status="failed", detail=str(exc)), t0)
            bundle.hard_error = str(exc)
            return bundle
        count = len(_list_page_images(pages_dir))
        if force and existing_pages:
            reason = f"强制重跑，写出 {count} 张页图"
        elif existing_pages:
            reason = f"PDF 已变更，重新写出 {count} 张页图"
        else:
            reason = f"写出 {count} 张页图"
        _add(
            StepOutcome(
                name="pdf-to-images",
                status="ran",
                detail=reason,
                path=pages_dir,
            ),
            t0,
        )

    t0 = clock.start("check-layout")
    layout_json = run_dir / f"{stem}.layout.json"
    layout_sha = _json_sha256_field(layout_json)
    layout_hash_ok = (
        layout_sha == current_sha
        if layout_sha is not None
        else _images_match_pdf(run_dir, current_sha)
    )
    can_skip_layout = (
        not force and not images_reran and layout_json.is_file() and layout_hash_ok
    )
    if can_skip_layout:
        try:
            layout_pass, layout_path = _load_layout_from_disk(run_dir, stem)
        except ResumeError as exc:
            _add(StepOutcome(name="check-layout", status="failed", detail=str(exc)), t0)
            bundle.hard_error = str(exc)
            return bundle
        bundle.layout_pass = layout_pass
        _add(
            StepOutcome(
                name="check-layout",
                status="skipped",
                detail="排版达标" if layout_pass else "排版未达标（沿用已有报告）",
                path=layout_path,
            ),
            t0,
        )
        return bundle

    had_layout = layout_json.is_file()
    try:
        layout: LayoutResult = check_layout(
            pdf_path,
            root=root,
            visual_assessor=visual_assessor,
        )
    except CheckLayoutError as exc:
        _add(StepOutcome(name="check-layout", status="failed", detail=str(exc)), t0)
        bundle.hard_error = str(exc)
        return bundle
    bundle.layout_pass = layout.layout_pass
    detail = "排版达标" if layout.layout_pass else "排版未达标"
    if force and had_layout:
        detail = f"强制重跑，{detail}"
    elif had_layout:
        detail = f"PDF 已变更，重新查排版（{detail}）"
    _add(
        StepOutcome(
            name="check-layout",
            status="ran",
            detail=detail,
            path=layout.report_md,
        ),
        t0,
    )
    return bundle


def _run_text_path(
    *,
    pdf_path: Path,
    run_dir: Path,
    stem: str,
    current_sha: str,
    force: bool,
    skip_writing: bool,
    skip_questions: bool,
    triage: bool,
    job_description: str | None,
    root: Path | None,
    clock: _StepClock,
    writing_assessor: WritingAssessor | None,
    content_assessor: ContentAssessor | None,
    questions_assessor: QuestionsAssessor | None,
) -> _PathBundle:
    """抽文本 → 查文字表达 → 判能不能投 → 出练习题。"""
    bundle = _PathBundle()

    def _add(outcome: StepOutcome, started: float) -> None:
        bundle.steps.append(clock.finish(outcome, started))

    t0 = clock.start("read-resume")
    resume_md = run_dir / f"{stem}.resume.md"
    can_skip_read = (
        not force
        and resume_md.is_file()
        and _resume_text_match_pdf(run_dir, stem, current_sha)
    )
    text_reran = False
    if can_skip_read:
        _add(
            StepOutcome(
                name="read-resume",
                status="skipped",
                detail="已有抽出文本（PDF 哈希一致）",
                path=resume_md,
            ),
            t0,
        )
    else:
        text_reran = True
        had_resume = resume_md.is_file()
        try:
            extract_resume_text(pdf_path, root=root)
        except ReadResumeError as exc:
            _add(StepOutcome(name="read-resume", status="failed", detail=str(exc)), t0)
            bundle.hard_error = str(exc)
            return bundle
        if force and had_resume:
            detail = "强制重跑，已抽出文本"
        elif had_resume:
            detail = "PDF 已变更，重新抽出文本"
        else:
            detail = "已抽出文本"
        _add(
            StepOutcome(
                name="read-resume",
                status="ran",
                detail=detail,
                path=resume_md,
            ),
            t0,
        )

    t0 = clock.start("check-writing")
    writing_json = run_dir / f"{stem}.writing.json"
    if skip_writing:
        _add(
            StepOutcome(
                name="check-writing",
                status="disabled",
                detail="排查模式未查文字表达",
            ),
            t0,
        )
    else:
        can_skip_writing = (
            not force
            and not text_reran
            and writing_json.is_file()
            and _resume_text_match_pdf(run_dir, stem, current_sha)
        )
        if can_skip_writing:
            try:
                writing_pass, writing_path = _load_writing_from_disk(run_dir, stem)
            except ResumeError as exc:
                _add(
                    StepOutcome(name="check-writing", status="failed", detail=str(exc)),
                    t0,
                )
                bundle.hard_error = str(exc)
                return bundle
            bundle.writing_pass = writing_pass
            _add(
                StepOutcome(
                    name="check-writing",
                    status="skipped",
                    detail="文字表达无明显问题" if writing_pass else "有待改进项（沿用已有报告）",
                    path=writing_path,
                ),
                t0,
            )
        else:
            had_writing = writing_json.is_file()
            try:
                writing: WritingResult = check_writing(
                    pdf_path,
                    root=root,
                    writing_assessor=writing_assessor,
                )
            except CheckWritingError as exc:
                _add(
                    StepOutcome(name="check-writing", status="failed", detail=str(exc)),
                    t0,
                )
                bundle.hard_error = str(exc)
                return bundle
            bundle.writing_pass = writing.writing_pass
            base = (
                "文字表达无明显问题"
                if writing.writing_pass
                else f"有待改进项 {len(writing.findings)} 条"
            )
            if force and had_writing:
                detail = f"强制重跑，{base}"
            elif had_writing:
                detail = f"PDF 已变更，重新查文字表达（{base}）"
            else:
                detail = base
            _add(
                StepOutcome(
                    name="check-writing",
                    status="ran",
                    detail=detail,
                    path=writing.report_md,
                ),
                t0,
            )

    t0 = clock.start("judge-resume")
    judge_json = run_dir / f"{stem}.judge.json"
    can_skip_judge = (
        not force
        and not text_reran
        and judge_json.is_file()
        and _resume_text_match_pdf(run_dir, stem, current_sha)
    )
    if can_skip_judge:
        try:
            content_pass, judge_path = _load_judge_from_disk(run_dir, stem)
        except ResumeError as exc:
            _add(StepOutcome(name="judge-resume", status="failed", detail=str(exc)), t0)
            bundle.hard_error = str(exc)
            return bundle
        bundle.content_pass = content_pass
        _add(
            StepOutcome(
                name="judge-resume",
                status="skipped",
                detail="内容达标" if content_pass else "内容未达标（沿用已有报告）",
                path=judge_path,
            ),
            t0,
        )
    else:
        had_judge = judge_json.is_file()
        try:
            judged: JudgeResult = judge_resume(
                pdf_path,
                job_description=job_description,
                root=root,
                content_assessor=content_assessor,
            )
        except JudgeResumeError as exc:
            _add(StepOutcome(name="judge-resume", status="failed", detail=str(exc)), t0)
            bundle.hard_error = str(exc)
            return bundle
        bundle.content_pass = judged.content_pass
        base = "内容达标" if judged.content_pass else "内容未达标"
        if force and had_judge:
            detail = f"强制重跑，{base}"
        elif had_judge:
            detail = f"PDF 已变更，重新判断（{base}）"
        else:
            detail = base
        _add(
            StepOutcome(
                name="judge-resume",
                status="ran",
                detail=detail,
                path=judged.report_md,
            ),
            t0,
        )

    t0 = clock.start("draft-questions")
    if skip_questions:
        detail = "排查模式未出练习题" if triage else "已按选项跳过出练习题"
        _add(
            StepOutcome(
                name="draft-questions",
                status="disabled",
                detail=detail,
            ),
            t0,
        )
        return bundle

    questions_json = run_dir / f"{stem}.questions.json"
    can_skip_questions = (
        not force
        and not text_reran
        and questions_json.is_file()
        and _resume_text_match_pdf(run_dir, stem, current_sha)
    )
    if can_skip_questions:
        try:
            count, questions_path = _load_questions_from_disk(run_dir, stem)
        except ResumeError as exc:
            _add(
                StepOutcome(name="draft-questions", status="failed", detail=str(exc)),
                t0,
            )
            bundle.hard_error = str(exc)
            return bundle
        bundle.questions_count = count
        _add(
            StepOutcome(
                name="draft-questions",
                status="skipped",
                detail=f"已有 {count} 道练习题（PDF 哈希一致）",
                path=questions_path,
            ),
            t0,
        )
        return bundle

    had_questions = questions_json.is_file()
    try:
        questions: QuestionsResult = draft_questions(
            pdf_path,
            job_description=job_description,
            root=root,
            questions_assessor=questions_assessor,
        )
    except DraftQuestionsError as exc:
        _add(
            StepOutcome(name="draft-questions", status="failed", detail=str(exc)),
            t0,
        )
        bundle.hard_error = str(exc)
        return bundle
    bundle.questions_count = len(questions.questions)
    base = f"写出 {len(questions.questions)} 道练习题"
    if force and had_questions:
        detail = f"强制重跑，{base}"
    elif had_questions:
        detail = f"PDF 已变更，重新出题（{base}）"
    else:
        detail = base
    _add(
        StepOutcome(
            name="draft-questions",
            status="ran",
            detail=detail,
            path=questions.report_md,
        ),
        t0,
    )
    return bundle


def run_resume(
    source: Path,
    *,
    job_description: str | None = None,
    skip_questions: bool = False,
    triage: bool = False,
    force: bool = False,
    dpi: int = 200,
    root: Path | None = None,
    progress: ProgressHook | None = None,
    visual_assessor: VisualAssessor | None = None,
    writing_assessor: WritingAssessor | None = None,
    content_assessor: ContentAssessor | None = None,
    questions_assessor: QuestionsAssessor | None = None,
) -> ResumeRunResult:
    """布局路径与文本路径并行；已有结果且 PDF 哈希一致则跳过。"""
    try:
        pdf_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise ResumeError(str(exc)) from exc

    if pdf_path.suffix.lower() != ".pdf":
        raise ResumeError("输入不是 PDF，请先转成 PDF 再检查。")

    skip_questions = skip_questions or triage
    skip_writing = triage

    run_dir = output_run_dir(pdf_path, root=root)
    run_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    current_sha = _sha256_file(pdf_path)
    result = ResumeRunResult(pdf_path=pdf_path, run_dir=run_dir, triage=triage)
    clock = _StepClock(progress)

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_layout = pool.submit(
            _run_layout_path,
            pdf_path=pdf_path,
            run_dir=run_dir,
            stem=stem,
            current_sha=current_sha,
            force=force,
            dpi=dpi,
            root=root,
            clock=clock,
            visual_assessor=visual_assessor,
        )
        fut_text = pool.submit(
            _run_text_path,
            pdf_path=pdf_path,
            run_dir=run_dir,
            stem=stem,
            current_sha=current_sha,
            force=force,
            skip_writing=skip_writing,
            skip_questions=skip_questions,
            triage=triage,
            job_description=job_description,
            root=root,
            clock=clock,
            writing_assessor=writing_assessor,
            content_assessor=content_assessor,
            questions_assessor=questions_assessor,
        )
        layout_bundle = fut_layout.result()
        text_bundle = fut_text.result()

    by_name = {step.name: step for step in layout_bundle.steps + text_bundle.steps}
    result.steps = [by_name[name] for name in STEP_ORDER if name in by_name]
    result.layout_pass = layout_bundle.layout_pass
    result.writing_pass = text_bundle.writing_pass
    result.content_pass = text_bundle.content_pass
    result.questions_count = text_bundle.questions_count

    hard = layout_bundle.hard_error or text_bundle.hard_error
    if hard:
        result.hard_error = hard
        result.exit_code = 1
        return result

    layout_fail = result.layout_pass is False
    content_fail = result.content_pass is False
    if layout_fail or content_fail:
        result.exit_code = 2
    else:
        result.exit_code = 0

    result.actions = _collect_actions(run_dir, stem, limit=3)
    result.brief_path = write_brief(result)
    return result
