"""就业守护 · 命令行入口。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from employ_guard import __version__
from employ_guard.check_layout import CheckLayoutError, check_layout
from employ_guard.check_writing import CheckWritingError, check_writing
from employ_guard.draft_questions import DraftQuestionsError, draft_questions
from employ_guard.judge_resume import JudgeResumeError, judge_resume
from employ_guard.pdf_to_images import PdfToImagesError, render_pdf_to_images
from employ_guard.read_resume import ReadResumeError, extract_resume_text
from employ_guard.resume import ResumeError, run_resume

app = typer.Typer(
    no_args_is_help=True,
    help="就业守护：检查简历与面试录音并生成报告。",
)


@app.command()
def version() -> None:
    """打印版本号。"""
    typer.echo(__version__)


@app.command()
def check() -> None:
    """检查本机是否具备后续工具所需的基础环境。"""
    python_ok = sys.version_info >= (3, 12)
    ffmpeg_path = shutil.which("ffmpeg")

    typer.echo(
        f"Python {sys.version.split()[0]} （要求 >= 3.12）："
        f"{'通过' if python_ok else '未通过'}"
    )
    typer.echo(
        f"ffmpeg：{'已找到 ' + ffmpeg_path if ffmpeg_path else '未找到（面试录音抽轨时需要，可用 brew install ffmpeg 安装）'}"
    )

    if not python_ok:
        raise typer.Exit(code=1)


@app.command("pdf-to-images")
def pdf_to_images(
    pdf: Path = typer.Argument(..., help="投递用 PDF。本期只接受 PDF。"),
    dpi: int = typer.Option(200, "--dpi", help="出图分辨率，默认 200。"),
) -> None:
    """把 PDF 按页转成图片。本步不评价排版好坏。"""
    try:
        pages_dir = render_pdf_to_images(pdf, dpi=dpi)
    except PdfToImagesError as exc:
        typer.secho(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    count = len(list(pages_dir.glob("*.png")))
    typer.echo(f"已按页写出 {count} 张图片：{pages_dir}")
    typer.echo("本步只出图，不评价排版。")


@app.command("read-resume")
def read_resume(
    pdf: Path = typer.Argument(..., help="投递用 PDF。本期只接受 PDF。"),
) -> None:
    """从 PDF 抽出文本。本步不判断能不能投，也不评价排版。"""
    try:
        run_dir = extract_resume_text(pdf)
    except ReadResumeError as exc:
        typer.secho(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    md_files = sorted(run_dir.glob("*.resume.md"))
    typer.echo(f"已抽出文本：{md_files[0] if md_files else run_dir}")
    typer.echo("本步不判断能不能投，也不评价排版。")


@app.command("check-layout")
def check_layout_cmd(
    pdf: Path = typer.Argument(..., help="投递用 PDF。须已先跑过 pdf-to-images。"),
) -> None:
    """只凭页图查排版。本步不判断内容能不能投。"""
    try:
        result = check_layout(pdf)
    except CheckLayoutError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"已写出排版报告：{result.report_md}")
    typer.echo("本步只查排版，不判断内容能不能投。")

    failed = [item for item in result.pass_line if not item.get("pass")]
    found_defects = [item for item in result.defects if item.get("found")]

    if result.layout_pass:
        typer.secho(
            f"结论：排版达标（共 {result.page_count} 页，对照简历合格线 §3）。",
            fg=typer.colors.GREEN,
            bold=True,
        )
        if found_defects:
            typer.echo("仍有细项可改（未构成合格线未过）：")
            for item in found_defects:
                typer.echo(f"  - {item.get('code')}: {item.get('note') or '见报告'}")
        if result.revision_tips:
            typer.echo("改稿 / 人工复核提示：")
            for tip in result.revision_tips:
                typer.echo(f"  - {tip}")
        return

    typer.secho(
        f"结论：排版未达标（共 {result.page_count} 页）。请先改合格线未过项后再投。",
        fg=typer.colors.RED,
        bold=True,
        err=True,
    )
    typer.secho("未过项：", fg=typer.colors.RED, err=True)
    for item in failed:
        typer.secho(f"  - {item['id']}: {item.get('note', '')}", fg=typer.colors.RED, err=True)
    if found_defects:
        typer.echo("相关细项缺陷：", err=True)
        for item in found_defects:
            typer.echo(f"  - {item.get('code')}: {item.get('note') or '见报告'}", err=True)
    if result.revision_tips:
        typer.echo("改稿要点：", err=True)
        for tip in result.revision_tips:
            typer.echo(f"  - {tip}", err=True)
    raise typer.Exit(code=2)


@app.command("check-writing")
def check_writing_cmd(
    source: Path = typer.Argument(
        ...,
        help="PDF（须已 read-resume）或 *.resume.md / 文本文件。",
    ),
) -> None:
    """查文字表达。本步不判能不能投，不评价排版。"""
    try:
        result = check_writing(source)
    except CheckWritingError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"已写出文字表达报告：{result.report_md}")
    typer.echo("本步只查文字表达，不判能不能投，不评价排版。")

    if result.writing_pass:
        typer.secho("结论：文字表达未发现明显问题。", fg=typer.colors.GREEN, bold=True)
        return

    typer.secho("结论：有待改进的文字表达项（不自动等同「不能投」）。", fg=typer.colors.YELLOW, bold=True)
    by_id: dict[str, list[dict[str, object]]] = {}
    for item in result.findings:
        code = str(item.get("id") or "?")
        by_id.setdefault(code, []).append(item)
    for code in ("W1", "W2", "W3", "W4"):
        items = by_id.get(code) or []
        if not items:
            continue
        typer.echo(f"{code}（{len(items)} 项）：")
        for item in items[:5]:
            line_no = item.get("line")
            prefix = f"  第 {line_no} 行" if line_no else "  "
            typer.echo(f"{prefix}：{item.get('note') or item.get('excerpt')}")
        if len(items) > 5:
            typer.echo(f"  … 另有 {len(items) - 5} 项，见报告。")


@app.command("judge-resume")
def judge_resume_cmd(
    source: Path = typer.Argument(
        ...,
        help="PDF（须已 read-resume）或 *.resume.md / 文本文件。",
    ),
    job_desc: Path | None = typer.Option(
        None,
        "--job-desc",
        help="可选：目标岗位说明文本文件。",
    ),
) -> None:
    """判内容能不能投。本步不评价排版，不出练习题。"""
    job_text: str | None = None
    if job_desc is not None:
        if not job_desc.is_file():
            typer.secho(f"找不到岗位说明：{job_desc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        job_text = job_desc.read_text(encoding="utf-8")

    try:
        result = judge_resume(source, job_description=job_text)
    except JudgeResumeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"已写出内容报告：{result.report_md}")
    typer.echo("本步只判内容能不能投，不评价排版，不出练习题。")

    failed = [item for item in result.pass_line if not item.get("pass")]

    if result.content_pass:
        typer.secho(
            f"结论：内容达标（{result.scope}）。",
            fg=typer.colors.GREEN,
            bold=True,
        )
        if result.doubtful_items:
            typer.echo("存疑项（老师复核，不自动等同未合格）：")
            for note in result.doubtful_items:
                typer.echo(f"  - {note}")
        if result.level_line:
            high = [i["id"] for i in result.level_line if i.get("level") == "high"]
            mid = [i["id"] for i in result.level_line if i.get("level") == "mid"]
            low = [i["id"] for i in result.level_line if i.get("level") == "low"]
            parts = []
            if high:
                parts.append(f"高 {', '.join(high)}")
            if mid:
                parts.append(f"中 {', '.join(mid)}")
            if low:
                parts.append(f"低 {', '.join(low)}")
            if parts:
                typer.echo("水平线：" + "；".join(parts) + "。")
            weak = mid + low
            if weak:
                typer.echo("偏弱项：" + "、".join(weak) + "（详见报告水平线）。")
        return

    typer.secho(
        "结论：内容未达标。请先改合格线未过项后再投。",
        fg=typer.colors.RED,
        bold=True,
        err=True,
    )
    typer.secho("未过项：", fg=typer.colors.RED, err=True)
    for item in failed:
        typer.secho(f"  - {item['id']}: {item.get('note', '')}", fg=typer.colors.RED, err=True)
    if result.main_blockers:
        typer.echo("主要卡点：", err=True)
        for note in result.main_blockers:
            typer.echo(f"  - {note}", err=True)
    raise typer.Exit(code=2)


@app.command("draft-questions")
def draft_questions_cmd(
    source: Path = typer.Argument(
        ...,
        help="PDF（须已 read-resume）或 *.resume.md / 文本文件。",
    ),
    job_desc: Path | None = typer.Option(
        None,
        "--job-desc",
        help="可选：目标岗位说明文本文件。",
    ),
) -> None:
    """根据简历文本出练习题。本步不判能不能投，不评排版。"""
    job_text: str | None = None
    if job_desc is not None:
        if not job_desc.is_file():
            typer.secho(f"找不到岗位说明：{job_desc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        job_text = job_desc.read_text(encoding="utf-8")

    try:
        result = draft_questions(source, job_description=job_text)
    except DraftQuestionsError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"已写出练习题：{result.report_md}")
    typer.echo("本步只出练习题（推测），不判能不能投，不评排版。")
    typer.echo(f"范围：{result.scope}")
    typer.echo(f"共 {len(result.questions)} 道题（详见报告）。")
    for item in result.questions[:5]:
        typer.echo(f"  - {item.get('id')}（{item.get('category')}）：{item.get('question')}")
    if len(result.questions) > 5:
        typer.echo(f"  … 另有 {len(result.questions) - 5} 道，见报告。")


@app.command("resume")
def resume_cmd(
    pdf: Path = typer.Argument(..., help="投递用 PDF。本期只接受 PDF。"),
    job_desc: Path | None = typer.Option(
        None,
        "--job-desc",
        help="可选：目标岗位说明文本文件（传给判能不能投与出练习题）。",
    ),
    no_questions: bool = typer.Option(
        False,
        "--no-questions",
        help="关掉出练习题这一步。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="强制重跑各步，忽略已有结果文件。",
    ),
    dpi: int = typer.Option(200, "--dpi", help="出图分辨率，默认 200。"),
) -> None:
    """投前看简历：按顺序调用各工具；已有结果且 PDF 未变则跳过。"""
    job_text: str | None = None
    if job_desc is not None:
        if not job_desc.is_file():
            typer.secho(f"找不到岗位说明：{job_desc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        job_text = job_desc.read_text(encoding="utf-8")

    try:
        result = run_resume(
            pdf,
            job_description=job_text,
            skip_questions=no_questions,
            force=force,
            dpi=dpi,
        )
    except ResumeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"运行目录：{result.run_dir}")
    typer.echo("步骤：")
    for step in result.steps:
        status_label = {
            "ran": "已跑",
            "skipped": "跳过",
            "failed": "失败",
            "disabled": "关闭",
        }.get(step.status, step.status)
        line = f"  - {step.name}（{status_label}）"
        if step.detail:
            line += f"：{step.detail}"
        if step.status == "failed":
            typer.secho(line, err=True, fg=typer.colors.RED)
        else:
            typer.echo(line)

    if result.hard_error:
        typer.secho(
            "硬失败（出图 / 读文本或工具错误）。不得据此写成内容不能投或排版不合格。",
            err=True,
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=result.exit_code)

    typer.echo("摘要：")
    if result.layout_pass is True:
        typer.secho("  排版：达标", fg=typer.colors.GREEN)
    elif result.layout_pass is False:
        typer.secho("  排版：未达标", fg=typer.colors.RED)
    else:
        typer.echo("  排版：未得到结论")

    if result.writing_pass is True:
        typer.secho("  文字表达：无明显问题", fg=typer.colors.GREEN)
    elif result.writing_pass is False:
        typer.secho("  文字表达：有待改进（不自动等同不能投）", fg=typer.colors.YELLOW)
    else:
        typer.echo("  文字表达：未得到结论")

    if result.content_pass is True:
        typer.secho("  内容：达标", fg=typer.colors.GREEN)
    elif result.content_pass is False:
        typer.secho("  内容：未达标", fg=typer.colors.RED)
    else:
        typer.echo("  内容：未得到结论")

    if no_questions:
        typer.echo("  练习题：已关闭")
    elif result.questions_count is not None:
        typer.echo(f"  练习题：{result.questions_count} 道（推测，供练习）")
    else:
        typer.echo("  练习题：未写出")

    if result.exit_code == 2:
        typer.secho(
            "结论：排版或内容未过合格线。请先改对应报告中的未过项后再投。",
            err=True,
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=2)

    typer.secho("结论：排版与内容均达标（文字表达问题不自动否决投递）。", fg=typer.colors.GREEN, bold=True)

