"""看图客户端：超时须收成 VisionError；超时 / 断连可重试。"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

import pytest

from employ_guard.vision import VisionError, chat_with_images

_SETTINGS = {
    "api_key": "k",
    "base_url": "https://example.com",
    "model": "deepseek-v4-flash-vision-exp",
}


def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("employ_guard.vision.load_dotenv", lambda: None)
    monkeypatch.delenv("LLM_VISION_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("LLM_VISION_RETRIES", raising=False)


def _ok_response() -> object:
    body = json.dumps(
        {"choices": [{"message": {"content": '{"ok": true}'}}]}
    ).encode("utf-8")

    class _Response:
        def read(self) -> bytes:
            return body

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    return _Response()


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
            settings=_SETTINGS,
            timeout_sec=120,
            retries=0,
        )


def test_default_timeout_aligns_with_text_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_env(monkeypatch)
    captured: dict[str, float] = {}

    def _fake(request: urllib.request.Request, timeout: float) -> object:
        captured["timeout"] = timeout
        return _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    chat_with_images(
        system="s",
        user_text="u",
        image_data_urls=["data:image/png;base64,e30="],
        settings=_SETTINGS,
    )
    assert captured["timeout"] == 180.0


def test_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    monkeypatch.setenv("LLM_VISION_TIMEOUT_SEC", "240")
    captured: dict[str, float] = {}

    def _fake(request: urllib.request.Request, timeout: float) -> object:
        captured["timeout"] = timeout
        return _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    chat_with_images(
        system="s",
        user_text="u",
        image_data_urls=["data:image/png;base64,e30="],
        settings=_SETTINGS,
    )
    assert captured["timeout"] == 240.0


def test_timeout_then_retry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    monkeypatch.setattr("employ_guard.vision.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def _flaky(_request: urllib.request.Request, timeout: float) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    text = chat_with_images(
        system="s",
        user_text="u",
        image_data_urls=["data:image/png;base64,e30="],
        settings=_SETTINGS,
        timeout_sec=12,
        retries=2,
    )
    assert text == '{"ok": true}'
    assert calls["n"] == 2


def test_remote_disconnect_then_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("employ_guard.vision.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def _flaky(_request: urllib.request.Request, timeout: float) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )
        return _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    text = chat_with_images(
        system="s",
        user_text="u",
        image_data_urls=["data:image/png;base64,e30="],
        settings=_SETTINGS,
        timeout_sec=12,
        retries=1,
    )
    assert text == '{"ok": true}'
    assert calls["n"] == 2


def test_urlerror_disconnect_then_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("employ_guard.vision.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def _flaky(_request: urllib.request.Request, timeout: float) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(
                http.client.RemoteDisconnected(
                    "Remote end closed connection without response"
                )
            )
        return _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    text = chat_with_images(
        system="s",
        user_text="u",
        image_data_urls=["data:image/png;base64,e30="],
        settings=_SETTINGS,
        timeout_sec=12,
        retries=1,
    )
    assert text == '{"ok": true}'
    assert calls["n"] == 2


def test_all_retries_exhausted_still_vision_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("employ_guard.vision.time.sleep", lambda _sec: None)
    calls = {"n": 0}

    def _boom(_request: urllib.request.Request, timeout: float) -> object:
        calls["n"] += 1
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(VisionError, match="已重试 2 次"):
        chat_with_images(
            system="s",
            user_text="u",
            image_data_urls=["data:image/png;base64,e30="],
            settings=_SETTINGS,
            timeout_sec=12,
            retries=2,
        )
    assert calls["n"] == 3


def test_http_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _boom(_request: urllib.request.Request, timeout: float) -> object:
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "https://example.com/chat/completions",
            400,
            "Bad Request",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(VisionError, match="HTTP 400"):
        chat_with_images(
            system="s",
            user_text="u",
            image_data_urls=["data:image/png;base64,e30="],
            settings=_SETTINGS,
            timeout_sec=12,
            retries=2,
        )
    assert calls["n"] == 1
