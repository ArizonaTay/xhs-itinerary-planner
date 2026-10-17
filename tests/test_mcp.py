"""MCP transport compatibility across client library versions."""

from types import SimpleNamespace

import pytest

from app import mcp


class _FakeStreams:
    """Async context manager that mimics streamable_http_client's yield shape."""

    def __init__(self, arity: int):
        self._arity = arity

    async def __aenter__(self):
        read, write = object(), object()
        if self._arity == 2:  # mcp >= 2
            return read, write
        return read, write, (lambda: "session-id")  # mcp 1.x

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, read, write):
        self.read = read
        self.write = write

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, tool, arguments=None):
        return SimpleNamespace(
            isError=False, content=[SimpleNamespace(text="ok")]
        )


@pytest.mark.parametrize("arity", [2, 3])
@pytest.mark.asyncio
async def test_call_tool_accepts_both_stream_shapes(monkeypatch, arity):
    monkeypatch.setattr(
        mcp, "streamable_http_client", lambda url, **kwargs: _FakeStreams(arity)
    )
    monkeypatch.setattr(mcp, "ClientSession", _FakeSession)

    result = await mcp.call_tool("check_login_status")

    assert mcp.texts(result) == ["ok"]
