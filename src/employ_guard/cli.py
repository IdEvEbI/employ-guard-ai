"""就业守护 · 命令行入口。"""

from __future__ import annotations

import shutil
import sys

import typer

from employ_guard import __version__

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
