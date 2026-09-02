"""OpenAI 兼容的多模态（看图）调用。默认 DeepSeek Vision。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_VISION_MODEL = "deepseek-v4-flash-vision-exp"


class VisionError(Exception):
    """看图调用失败（缺密钥、网络或模型拒绝）。"""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def vision_settings() -> dict[str, str]:
    """从环境变量读取看图配置；先加载仓库根附近的 `.env`。"""
    load_dotenv()
    api_key = _env("LLM_API_KEY")
    if not api_key:
        raise VisionError(
            "未配置 LLM_API_KEY。查排版的视觉项需要能看图的模型；请从 .env.example 复制为 .env 并填写。"
        )
    base_url = (_env("LLM_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    model = (
        _env("LLM_VISION_MODEL")
        or _env("LAYOUT_VISION_MODEL")
        or DEFAULT_VISION_MODEL
    )
    return {"api_key": api_key, "base_url": base_url, "model": model}


def chat_with_images(
    *,
    system: str,
    user_text: str,
    image_data_urls: list[str],
    settings: dict[str, str] | None = None,
    timeout_sec: float = 120.0,
    detail: str = "high",
) -> str:
    """发送文本 + 页图，返回助手纯文本（期望为 JSON）。"""
    cfg = settings or vision_settings()
    if not image_data_urls:
        raise VisionError("没有可发送的页图。")

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for url in image_data_urls:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": url, "detail": detail},
            }
        )

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
    }
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
        raise VisionError(f"看图接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise VisionError(f"无法连接看图接口：{exc}") from exc

    try:
        data = json.loads(raw)
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise VisionError(f"看图接口返回无法解析：{raw[:500]}") from exc
