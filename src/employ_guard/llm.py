"""OpenAI 兼容的文本 LLM 调用。默认 DeepSeek。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


class LLMError(Exception):
    """文本 LLM 调用失败（缺密钥、网络或模型拒绝）。"""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def llm_settings() -> dict[str, str]:
    """从环境变量读取 LLM 配置；先加载仓库根附近的 `.env`。"""
    load_dotenv()
    api_key = _env("LLM_API_KEY")
    if not api_key:
        raise LLMError(
            "未配置 LLM_API_KEY。判能不能投需要 LLM；请从 .env.example 复制为 .env 并填写。"
        )
    base_url = (_env("LLM_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    model = _env("LLM_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    return {"api_key": api_key, "base_url": base_url, "model": model}


def _assistant_text(message: dict[str, object], finish_reason: str | None) -> str:
    """从 Chat Completions 的 message 取出可用正文。"""
    content = message.get("content")
    if content is not None and str(content).strip():
        return str(content)
    reasoning = message.get("reasoning_content")
    if reasoning is not None and str(reasoning).strip():
        return str(reasoning)
    reason = finish_reason or "unknown"
    raise LLMError(f"LLM 返回内容为空（finish_reason={reason}）。")


def chat_completion(
    *,
    system: str,
    user_text: str,
    settings: dict[str, str] | None = None,
    timeout_sec: float = 180.0,
    json_object: bool = False,
    max_tokens: int | None = None,
    disable_thinking: bool = True,
) -> str:
    """发送 system + user 文本，返回助手纯文本。"""
    cfg = settings or llm_settings()
    payload: dict[str, object] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if disable_thinking and cfg["model"].startswith("deepseek-v4"):
        # 结构化抽取关闭 thinking，避免 reasoning 占满 token 导致 content 为空。
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"LLM 接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"无法连接 LLM 接口：{exc}") from exc

    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message 不是对象")
        return _assistant_text(message, choice.get("finish_reason"))
    except LLMError:
        raise
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM 接口返回无法解析：{raw[:500]}") from exc
