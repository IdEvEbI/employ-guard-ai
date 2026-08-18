"""输入与输出目录约定。"""

from __future__ import annotations

from pathlib import Path

DEFAULT_INPUT_DIR = Path("data/input")
DEFAULT_OUTPUT_DIR = Path("data/output")


def repo_root(start: Path | None = None) -> Path:
    """从当前目录向上查找含 pyproject.toml 的仓库根。找不到则用当前工作目录。"""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return here


def resolve_input_file(source: Path, *, input_root: Path | None = None) -> Path:
    """已存在的文件直接用；相对路径再尝试默认输入目录。"""
    if source.exists():
        if source.is_file():
            return source.resolve()
        raise FileNotFoundError(f"不是文件：{source}")

    tried = [source]
    if not source.is_absolute():
        nested = (input_root or repo_root() / DEFAULT_INPUT_DIR) / source
        tried.append(nested)
        if nested.is_file():
            return nested.resolve()

    detail = "；".join(str(path) for path in tried)
    raise FileNotFoundError(f"找不到输入文件（已尝试：{detail}）")


def output_run_dir(
    source: Path,
    *,
    output_root: Path | None = None,
    input_root: Path | None = None,
    root: Path | None = None,
) -> Path:
    """按输入相对路径建目录：data/input/a/b/c.pdf → data/output/a/b/c/ 。"""
    base = root or repo_root()
    resolved = source.resolve()
    in_root = (input_root or base / DEFAULT_INPUT_DIR).resolve()
    out_root = output_root or (base / DEFAULT_OUTPUT_DIR)
    try:
        relative_parent = resolved.parent.relative_to(in_root)
    except ValueError:
        return out_root / resolved.stem
    return out_root / relative_parent / resolved.stem
