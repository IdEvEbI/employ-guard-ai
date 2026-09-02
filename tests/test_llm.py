"""文本 LLM 客户端。"""

from __future__ import annotations

import json
import urllib.request

import pytest

from employ_guard.llm import LLMError, chat_completion


def _mock_response(payload: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(payload).encode("utf-8")

    class _Response:
        def read(self) -> bytes:
            return body

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


def test_chat_completion_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_response(
        {
            "choices": [
                {
                    "message": {"content": '{"ok": true}'},
                    "finish_reason": "stop",
                }
            ]
        },
        monkeypatch,
    )
    text = chat_completion(
        system="s",
        user_text="u",
        settings={"api_key": "k", "base_url": "https://example.com", "model": "deepseek-v4-flash"},
    )
    assert text == '{"ok": true}'


def test_chat_completion_uses_reasoning_when_content_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_response(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": '{"from_reasoning": 1}',
                    },
                    "finish_reason": "stop",
                }
            ]
        },
        monkeypatch,
    )
    text = chat_completion(
        system="s",
        user_text="u",
        settings={"api_key": "k", "base_url": "https://example.com", "model": "deepseek-v4-flash"},
    )
    assert text == '{"from_reasoning": 1}'


def test_chat_completion_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_response(
        {
            "choices": [
                {
                    "message": {"content": None},
                    "finish_reason": "length",
                }
            ]
        },
        monkeypatch,
    )
    with pytest.raises(LLMError, match="为空"):
        chat_completion(
            system="s",
            user_text="u",
            settings={"api_key": "k", "base_url": "https://example.com", "model": "deepseek-v4-flash"},
        )
