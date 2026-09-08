"""看图客户端：超时须收成 VisionError。"""

from __future__ import annotations

import urllib.request

import pytest

from employ_guard.vision import VisionError, chat_with_images


def test_chat_with_images_timeout_becomes_vision_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_request: urllib.request.Request, timeout: float) -> object:
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(VisionError, match="超时"):
        chat_with_images(
            system="s",
            user_text="u",
            image_data_urls=["data:image/png;base64,e30="],
            settings={
                "api_key": "k",
                "base_url": "https://example.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            timeout_sec=120,
        )
