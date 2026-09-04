"""本机环境自检：缺密钥 / 缺依赖时给出可执行提示。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from employ_guard.paths import repo_root


@dataclass
class CheckItem:
    """单项检查结果。"""

    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class EnvCheckResult:
    """`check` 命令汇总。"""

    items: list[CheckItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.ok or not item.required for item in self.items)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def _can_import(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def run_env_check(*, root: Path | None = None) -> EnvCheckResult:
    """检查简历侧跑通所需环境；面试相关标为可选。"""
    base = root or repo_root()
    # 测试可注入 root，只读该目录下的 .env，避免误读仓库真实密钥。
    load_dotenv(base / ".env", override=True)
    if root is None:
        load_dotenv(override=False)
    result = EnvCheckResult()

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 12)
    result.items.append(
        CheckItem(
            name="Python",
            ok=py_ok,
            detail=(
                f"{py_ver}（要求 >= 3.12）"
                if py_ok
                else f"{py_ver} 未通过；请安装 Python 3.12+ 后重试"
            ),
        )
    )

    for module, hint in (
        ("pymupdf", "请在仓库根执行：uv sync"),
        ("typer", "请在仓库根执行：uv sync"),
        ("dotenv", "请在仓库根执行：uv sync（包名 python-dotenv）"),
    ):
        ok = _can_import(module)
        result.items.append(
            CheckItem(
                name=f"依赖 {module}",
                ok=ok,
                detail="可导入" if ok else f"未安装；{hint}",
            )
        )

    env_path = base / ".env"
    example = base / ".env.example"
    if env_path.is_file():
        env_detail = f"已找到 {env_path.name}"
        env_ok = True
    else:
        env_ok = False
        env_detail = (
            f"未找到 .env；请复制 {example.name if example.is_file() else '.env.example'} "
            "为 .env 并填写 LLM_API_KEY"
        )
    result.items.append(
        CheckItem(name=".env 文件", ok=env_ok, detail=env_detail, required=False)
    )

    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    key_ok = bool(api_key)
    result.items.append(
        CheckItem(
            name="LLM_API_KEY",
            ok=key_ok,
            detail=(
                "已配置（不打印密钥）"
                if key_ok
                else "未配置；查排版 / 判能不能投等需要 LLM。"
                "请从 .env.example 复制为 .env 并填写 LLM_API_KEY"
            ),
        )
    )

    vision = (os.environ.get("LLM_VISION_MODEL") or "").strip()
    result.items.append(
        CheckItem(
            name="LLM_VISION_MODEL",
            ok=True,
            detail=(
                f"已配置：{vision}"
                if vision
                else "未单独配置（将使用代码默认视觉模型）；查排版看图需要支持 image 的模型"
            ),
            required=False,
        )
    )

    ffmpeg_path = shutil.which("ffmpeg")
    result.items.append(
        CheckItem(
            name="ffmpeg",
            ok=True,
            detail=(
                f"已找到 {ffmpeg_path}（听后做复盘暂缓；简历侧不需要）"
                if ffmpeg_path
                else "未找到（听后做复盘暂缓时可选；简历侧不需要。"
                "需要时可用 brew install ffmpeg）"
            ),
            required=False,
        )
    )

    return result
