"""The LLM boundary: missing key is a 503, bad JSON gets one repair attempt."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import llm


def _settings(api_key: str = "test-key") -> SimpleNamespace:
    return SimpleNamespace(
        deepseek=SimpleNamespace(
            api_key=api_key,
            base_url="https://example.invalid/v1",
            model="deepseek-chat",
            timeout_seconds=5,
            max_tokens=SimpleNamespace(itinerary=100, short=100, keyword=50),
        )
    )


class _Completions:
    def __init__(self, replies):
        self.replies = replies
        self.calls = 0

    async def create(self, **kwargs):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]
        )


class _Client:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=_Completions(replies))


@pytest.mark.asyncio
async def test_missing_api_key_is_503(monkeypatch):
    monkeypatch.setattr(llm, "settings", _settings(api_key=""))

    with pytest.raises(HTTPException) as excinfo:
        await llm.complete_json("system", "user")

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_invalid_json_retries_once_then_fails(monkeypatch):
    stub = _Client(["not json", "still not json"])
    monkeypatch.setattr(llm, "settings", _settings())
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: stub)

    with pytest.raises(HTTPException) as excinfo:
        await llm.complete_json("system", "user")

    assert excinfo.value.status_code == 502
    assert stub.chat.completions.calls == 2
