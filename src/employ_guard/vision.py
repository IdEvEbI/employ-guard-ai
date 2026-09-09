"""OpenAI 兼容的多模态（看图）调用。默认 DeepSeek Vision。"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_VISION_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_TIMEOUT_SEC = 180.0
DEFAULT_RETRIES = 2
MAX_RETRIES = 5
RETRY_SLEEP_SEC = 1.0


class VisionError(Exception):
    """看图调用失败（缺密钥、网络或模型拒绝）。"""


class _TransientVisionError(Exception):
    """可重试的瞬时失败（超时、连接被掐）。"""


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


def vision_timeout_sec() -> float:
    """看图超时秒数；默认与文本 LLM 对齐，可用 `LLM_VISION_TIMEOUT_SEC` 覆盖。"""
    load_dotenv()
    raw = _env("LLM_VISION_TIMEOUT_SEC")
    if raw is None:
        return DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError as exc:
        raise VisionError(f"LLM_VISION_TIMEOUT_SEC 不是有效数字：{raw}") from exc
    if value <= 0:
        raise VisionError(f"LLM_VISION_TIMEOUT_SEC 必须大于 0，当前为 {raw}")
    return value


def vision_retries() -> int:
    """超时或断连后的额外重试次数（不含第一次）。"""
    load_dotenv()
    raw = _env("LLM_VISION_RETRIES")
    if raw is None:
        return DEFAULT_RETRIES
    try:
        value = int(raw)
    except ValueError as exc:
        raise VisionError(f"LLM_VISION_RETRIES 不是整数：{raw}") from exc
    if value < 0:
        raise VisionError(f"LLM_VISION_RETRIES 不能为负，当前为 {raw}")
    return min(value, MAX_RETRIES)


def _read_chat_response(request: urllib.request.Request, timeout_sec: float) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VisionError(f"看图接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise _TransientVisionError(f"无法连接看图接口：{exc}") from exc
    except TimeoutError as exc:
        # urlopen 连接阶段超时会变成 URLError；读响应超时会原样抛 TimeoutError。
        raise _TransientVisionError(
            f"看图接口超时（超过 {timeout_sec:.0f} 秒）：{exc}"
        ) from exc
    except (
        ConnectionError,
        http.client.RemoteDisconnected,
        http.client.IncompleteRead,
    ) as exc:
        raise _TransientVisionError(f"看图接口连接中断：{exc}") from exc


def chat_with_images(
    *,
    system: str,
    user_text: str,
    image_data_urls: list[str],
    settings: dict[str, str] | None = None,
    timeout_sec: float | None = None,
    retries: int | None = None,
    detail: str = "high",
) -> str:
    """发送文本 + 页图，返回助手纯文本（期望为 JSON）。"""
    cfg = settings or vision_settings()
    if not image_data_urls:
        raise VisionError("没有可发送的页图。")
    wait_sec = vision_timeout_sec() if timeout_sec is None else timeout_sec
    extra_tries = vision_retries() if retries is None else retries
    if extra_tries < 0:
        raise VisionError(f"看图重试次数不能为负，当前为 {extra_tries}")

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
    attempts = extra_tries + 1
    raw = ""
    for attempt in range(attempts):
        try:
            raw = _read_chat_response(request, wait_sec)
            break
        except _TransientVisionError as exc:
            if attempt + 1 >= attempts:
                suffix = f"（已重试 {extra_tries} 次）" if extra_tries else ""
                raise VisionError(f"{exc}{suffix}") from exc.__cause__
            time.sleep(RETRY_SLEEP_SEC)

    try:
        data = json.loads(raw)
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise VisionError(f"看图接口返回无法解析：{raw[:500]}") from exc
