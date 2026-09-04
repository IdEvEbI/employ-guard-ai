"""环境自检 check：缺密钥 / 缺依赖有提示。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from employ_guard.check_env import run_env_check
from employ_guard.cli import app

runner = CliRunner()


def test_run_env_check_reports_missing_key(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # 避免读到仓库真实 .env
    monkeypatch.setenv("LLM_API_KEY", "")
    result = run_env_check(root=tmp_path)
    by_name = {item.name: item for item in result.items}
    assert by_name["LLM_API_KEY"].ok is False
    assert by_name["LLM_API_KEY"].required is True
    assert result.exit_code == 1
    assert by_name[".env 文件"].ok is False
    assert by_name[".env 文件"].required is False


def test_run_env_check_passes_with_key_and_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env").write_text("LLM_API_KEY=test-key-not-real\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    result = run_env_check(root=tmp_path)
    by_name = {item.name: item for item in result.items}
    assert by_name["LLM_API_KEY"].ok is True
    assert by_name[".env 文件"].ok is True
    assert result.exit_code == 0
    assert "不打印密钥" in by_name["LLM_API_KEY"].detail


def test_cli_check_exit_1_without_key(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "employ_guard.cli.run_env_check",
        lambda: run_env_check(root=tmp_path),
    )
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "")
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    assert "LLM_API_KEY" in result.output
    assert "未通过" in result.output
