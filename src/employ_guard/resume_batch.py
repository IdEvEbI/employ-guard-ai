"""老师命令：对目录逐份调用 resume，写出本地总表。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from employ_guard.check_layout import VisualAssessor
from employ_guard.check_writing import WritingAssessor
from employ_guard.draft_questions import QuestionsAssessor
from employ_guard.judge_resume import ContentAssessor
from employ_guard.paths import (
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    repo_root,
)
from employ_guard.resume import (
    ProgressHook,
    ResumeError,
    ResumeRunResult,
    run_resume,
)


@dataclass
class BatchRow:
    """一份简历在批跑中的摘要行。"""

    pdf_path: Path
    exit_code: int
    layout_pass: bool | None = None
    content_pass: bool | None = None
    brief_path: Path | None = None
    hard_error: str | None = None
    run_dir: Path | None = None


@dataclass
class BatchRunResult:
    """一次目录批跑的汇总。"""

    source_dir: Path
    rows: list[BatchRow] = field(default_factory=list)
    summary_md: Path | None = None
    summary_json: Path | None = None
    exit_code: int = 0
    triage: bool = False


def list_resume_pdfs(directory: Path) -> list[Path]:
    """列出目录下一层的 PDF（非递归，按文件名排序）。"""
    if not directory.is_dir():
        raise ResumeError(f"不是目录：{directory}")
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")


def output_batch_dir(
    source_dir: Path,
    *,
    root: Path | None = None,
    input_root: Path | None = None,
    output_root: Path | None = None,
) -> Path:
    """批跑总表目录：data/input/foo → data/output/foo/。"""
    base = root or repo_root()
    resolved = source_dir.resolve()
    in_root = (input_root or base / DEFAULT_INPUT_DIR).resolve()
    out_root = output_root or (base / DEFAULT_OUTPUT_DIR)
    try:
        relative = resolved.relative_to(in_root)
        return out_root / relative
    except ValueError:
        return out_root / "batch" / resolved.name


def _pass_cell(value: bool | None) -> str:
    if value is True:
        return "达标"
    if value is False:
        return "未达标"
    return "—"


def write_batch_summary(result: BatchRunResult) -> tuple[Path, Path]:
    """写出 Markdown 总表与 JSON 副本；仅本地文件，不点名、不上门户。"""
    assert result.summary_md is not None
    assert result.summary_json is not None
    result.summary_md.parent.mkdir(parents=True, exist_ok=True)

    mode = "排查（triage）" if result.triage else "完整"
    lines = [
        "# 投前看简历 · 批跑总表",
        "",
        f"- 目录：`{result.source_dir}`",
        f"- 份数：{len(result.rows)}",
        f"- 模式：{mode}",
        f"- 批跑退出码：{result.exit_code}",
        "",
        "| 文件 | 排版 | 内容 | 退出码 | brief |",
        "| ---- | ---- | ---- | ------ | ----- |",
    ]
    for row in result.rows:
        brief = f"`{row.brief_path.name}`" if row.brief_path else "—"
        if row.hard_error and not row.brief_path:
            brief = "硬失败（无 brief）"
        lines.append(
            f"| `{row.pdf_path.name}` | {_pass_cell(row.layout_pass)} | "
            f"{_pass_cell(row.content_pass)} | {row.exit_code} | {brief} |"
        )
    lines.extend(
        [
            "",
            "说明：总表仅供本机排查；不合并排版与内容结论；不上门户、不在班级群点名。",
            "",
        ]
    )
    result.summary_md.write_text("\n".join(lines), encoding="utf-8")

    payload = {
        "source_dir": str(result.source_dir),
        "triage": result.triage,
        "exit_code": result.exit_code,
        "rows": [
            {
                "pdf": row.pdf_path.name,
                "pdf_path": str(row.pdf_path),
                "exit_code": row.exit_code,
                "layout_pass": row.layout_pass,
                "content_pass": row.content_pass,
                "brief": str(row.brief_path) if row.brief_path else None,
                "hard_error": row.hard_error,
                "run_dir": str(row.run_dir) if row.run_dir else None,
            }
            for row in result.rows
        ],
    }
    result.summary_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result.summary_md, result.summary_json


def _aggregate_exit(rows: list[BatchRow]) -> int:
    if any(row.exit_code == 1 for row in rows):
        return 1
    if any(row.exit_code == 2 for row in rows):
        return 2
    return 0


def run_resume_batch(
    source_dir: Path,
    *,
    job_description: str | None = None,
    skip_questions: bool = False,
    triage: bool = False,
    force: bool = False,
    dpi: int = 200,
    root: Path | None = None,
    progress: ProgressHook | None = None,
    file_progress: Callable[[int, int, Path], None] | None = None,
    visual_assessor: VisualAssessor | None = None,
    writing_assessor: WritingAssessor | None = None,
    content_assessor: ContentAssessor | None = None,
    questions_assessor: QuestionsAssessor | None = None,
) -> BatchRunResult:
    """对目录下一层 PDF 逐份调用 `run_resume`，并写本地总表。"""
    if not source_dir.is_dir():
        raise ResumeError(f"不是目录：{source_dir}")

    pdfs = list_resume_pdfs(source_dir)
    if not pdfs:
        raise ResumeError(f"目录下没有 PDF：{source_dir}")

    out_dir = output_batch_dir(source_dir, root=root)
    result = BatchRunResult(
        source_dir=source_dir.resolve(),
        triage=triage,
        summary_md=out_dir / "batch-summary.md",
        summary_json=out_dir / "batch-summary.json",
    )

    total = len(pdfs)
    for index, pdf in enumerate(pdfs, start=1):
        if file_progress is not None:
            file_progress(index, total, pdf)
        elif progress is not None:
            progress(f"—— [{index}/{total}] {pdf.name} ——")

        try:
            one: ResumeRunResult = run_resume(
                pdf,
                job_description=job_description,
                skip_questions=skip_questions,
                triage=triage,
                force=force,
                dpi=dpi,
                root=root,
                progress=progress,
                visual_assessor=visual_assessor,
                writing_assessor=writing_assessor,
                content_assessor=content_assessor,
                questions_assessor=questions_assessor,
            )
        except ResumeError as exc:
            result.rows.append(
                BatchRow(
                    pdf_path=pdf.resolve(),
                    exit_code=1,
                    hard_error=str(exc),
                )
            )
            continue

        result.rows.append(
            BatchRow(
                pdf_path=one.pdf_path,
                exit_code=one.exit_code,
                layout_pass=one.layout_pass,
                content_pass=one.content_pass,
                brief_path=one.brief_path,
                hard_error=one.hard_error,
                run_dir=one.run_dir,
            )
        )

    result.exit_code = _aggregate_exit(result.rows)
    write_batch_summary(result)
    return result
