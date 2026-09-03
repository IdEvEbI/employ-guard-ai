"""就业守护 · 命令行入口。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from employ_guard import __version__
from employ_guard.check_layout import CheckLayoutError, check_layout
from employ_guard.judge_resume import JudgeResumeError, judge_resume
from employ_guard.pdf_to_images import PdfToImagesError, render_pdf_to_images
from employ_guard.read_resume import ReadResumeError, extract_resume_text

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
    raise typer.Exit(code=2)


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
