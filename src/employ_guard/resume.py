"""老师命令：投前看简历（按顺序调用各工具，已有结果则跳过）。"""

from __future__ import annotations

import hashlib
import json
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


@dataclass
class ResumeRunResult:
    """一次 `resume` 串跑的汇总。"""

    pdf_path: Path
    run_dir: Path
    steps: list[StepOutcome] = field(default_factory=list)
    layout_pass: bool | None = None
    writing_pass: bool | None = None
    content_pass: bool | None = None
    questions_count: int | None = None
    exit_code: int = 0
    hard_error: str | None = None


class ResumeError(Exception):
    """输入无效，或出图 / 读文本等硬失败（不得写成内容不能投）。"""


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


def run_resume(
    source: Path,
    *,
    job_description: str | None = None,
    skip_questions: bool = False,
    force: bool = False,
    dpi: int = 200,
    root: Path | None = None,
    visual_assessor: VisualAssessor | None = None,
    writing_assessor: WritingAssessor | None = None,
    content_assessor: ContentAssessor | None = None,
    questions_assessor: QuestionsAssessor | None = None,
) -> ResumeRunResult:
    """按产品说明 §5 顺序调用各工具；已有结果且 PDF 哈希一致则跳过。"""
    try:
        pdf_path = resolve_input_file(source)
    except FileNotFoundError as exc:
        raise ResumeError(str(exc)) from exc

    if pdf_path.suffix.lower() != ".pdf":
        raise ResumeError("输入不是 PDF，请先转成 PDF 再检查。")

    run_dir = output_run_dir(pdf_path, root=root)
    run_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    current_sha = _sha256_file(pdf_path)
    result = ResumeRunResult(pdf_path=pdf_path, run_dir=run_dir)

    # --- 1. pdf-to-images ---
    pages_dir = run_dir / "pages"
    existing_pages = _list_page_images(pages_dir) if pages_dir.is_dir() else []
    can_skip_images = (
        not force and bool(existing_pages) and _images_match_pdf(run_dir, current_sha)
    )
    images_reran = False
    if can_skip_images:
        result.steps.append(
            StepOutcome(
                name="pdf-to-images",
                status="skipped",
                detail=f"已有 {len(existing_pages)} 张页图（PDF 哈希一致）",
                path=pages_dir,
            )
        )
    else:
        images_reran = True
        try:
            pages_dir = render_pdf_to_images(pdf_path, dpi=dpi, root=root)
        except PdfToImagesError as exc:
            result.steps.append(
                StepOutcome(name="pdf-to-images", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        count = len(_list_page_images(pages_dir))
        if force and existing_pages:
            reason = f"强制重跑，写出 {count} 张页图"
        elif existing_pages:
            reason = f"PDF 已变更，重新写出 {count} 张页图"
        else:
            reason = f"写出 {count} 张页图"
        result.steps.append(
            StepOutcome(
                name="pdf-to-images",
                status="ran",
                detail=reason,
                path=pages_dir,
            )
        )

    # --- 2. check-layout ---
    layout_json = run_dir / f"{stem}.layout.json"
    layout_sha = _json_sha256_field(layout_json)
    layout_hash_ok = (
        layout_sha == current_sha
        if layout_sha is not None
        else _images_match_pdf(run_dir, current_sha)
    )
    can_skip_layout = (
        not force
        and not images_reran
        and layout_json.is_file()
        and layout_hash_ok
    )
    if can_skip_layout:
        try:
            layout_pass, layout_path = _load_layout_from_disk(run_dir, stem)
        except ResumeError as exc:
            result.steps.append(
                StepOutcome(name="check-layout", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.layout_pass = layout_pass
        result.steps.append(
            StepOutcome(
                name="check-layout",
                status="skipped",
                detail="排版达标" if layout_pass else "排版未达标（沿用已有报告）",
                path=layout_path,
            )
        )
    else:
        had_layout = layout_json.is_file()
        try:
            layout: LayoutResult = check_layout(
                pdf_path,
                root=root,
                visual_assessor=visual_assessor,
            )
        except CheckLayoutError as exc:
            result.steps.append(
                StepOutcome(name="check-layout", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.layout_pass = layout.layout_pass
        detail = "排版达标" if layout.layout_pass else "排版未达标"
        if force and had_layout:
            detail = f"强制重跑，{detail}"
        elif had_layout:
            detail = f"PDF 已变更，重新查排版（{detail}）"
        result.steps.append(
            StepOutcome(
                name="check-layout",
                status="ran",
                detail=detail,
                path=layout.report_md,
            )
        )

    # --- 3. read-resume ---
    resume_md = run_dir / f"{stem}.resume.md"
    can_skip_read = (
        not force
        and resume_md.is_file()
        and _resume_text_match_pdf(run_dir, stem, current_sha)
    )
    text_reran = False
    if can_skip_read:
        result.steps.append(
            StepOutcome(
                name="read-resume",
                status="skipped",
                detail="已有抽出文本（PDF 哈希一致）",
                path=resume_md,
            )
        )
    else:
        text_reran = True
        had_resume = resume_md.is_file()
        try:
            extract_resume_text(pdf_path, root=root)
        except ReadResumeError as exc:
            result.steps.append(
                StepOutcome(name="read-resume", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        if force and had_resume:
            detail = "强制重跑，已抽出文本"
        elif had_resume:
            detail = "PDF 已变更，重新抽出文本"
        else:
            detail = "已抽出文本"
        result.steps.append(
            StepOutcome(
                name="read-resume",
                status="ran",
                detail=detail,
                path=resume_md,
            )
        )

    # --- 4. check-writing ---
    writing_json = run_dir / f"{stem}.writing.json"
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
            result.steps.append(
                StepOutcome(name="check-writing", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.writing_pass = writing_pass
        result.steps.append(
            StepOutcome(
                name="check-writing",
                status="skipped",
                detail="文字表达无明显问题" if writing_pass else "有待改进项（沿用已有报告）",
                path=writing_path,
            )
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
            result.steps.append(
                StepOutcome(name="check-writing", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.writing_pass = writing.writing_pass
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
        result.steps.append(
            StepOutcome(
                name="check-writing",
                status="ran",
                detail=detail,
                path=writing.report_md,
            )
        )

    # --- 5. judge-resume ---
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
            result.steps.append(
                StepOutcome(name="judge-resume", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.content_pass = content_pass
        result.steps.append(
            StepOutcome(
                name="judge-resume",
                status="skipped",
                detail="内容达标" if content_pass else "内容未达标（沿用已有报告）",
                path=judge_path,
            )
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
            result.steps.append(
                StepOutcome(name="judge-resume", status="failed", detail=str(exc))
            )
            result.hard_error = str(exc)
            result.exit_code = 1
            return result
        result.content_pass = judged.content_pass
        base = "内容达标" if judged.content_pass else "内容未达标"
        if force and had_judge:
            detail = f"强制重跑，{base}"
        elif had_judge:
            detail = f"PDF 已变更，重新判断（{base}）"
        else:
            detail = base
        result.steps.append(
            StepOutcome(
                name="judge-resume",
                status="ran",
                detail=detail,
                path=judged.report_md,
            )
        )

    # --- 6. draft-questions（可关）---
    if skip_questions:
        result.steps.append(
            StepOutcome(
                name="draft-questions",
                status="disabled",
                detail="已按选项跳过出练习题",
            )
        )
    else:
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
                result.steps.append(
                    StepOutcome(name="draft-questions", status="failed", detail=str(exc))
                )
                result.hard_error = str(exc)
                result.exit_code = 1
                return result
            result.questions_count = count
            result.steps.append(
                StepOutcome(
                    name="draft-questions",
                    status="skipped",
                    detail=f"已有 {count} 道练习题（PDF 哈希一致）",
                    path=questions_path,
                )
            )
        else:
            had_questions = questions_json.is_file()
            try:
                questions: QuestionsResult = draft_questions(
                    pdf_path,
                    job_description=job_description,
                    root=root,
                    questions_assessor=questions_assessor,
                )
            except DraftQuestionsError as exc:
                result.steps.append(
                    StepOutcome(name="draft-questions", status="failed", detail=str(exc))
                )
                result.hard_error = str(exc)
                result.exit_code = 1
                return result
            result.questions_count = len(questions.questions)
            base = f"写出 {len(questions.questions)} 道练习题"
            if force and had_questions:
                detail = f"强制重跑，{base}"
            elif had_questions:
                detail = f"PDF 已变更，重新出题（{base}）"
            else:
                detail = base
            result.steps.append(
                StepOutcome(
                    name="draft-questions",
                    status="ran",
                    detail=detail,
                    path=questions.report_md,
                )
            )

    layout_fail = result.layout_pass is False
    content_fail = result.content_pass is False
    if layout_fail or content_fail:
        result.exit_code = 2
    else:
        result.exit_code = 0
    return result
