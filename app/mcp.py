"""Transport to the xiaohongshu-mcp container over Streamable HTTP.

This module knows about MCP and nothing else: no note shapes, no prompts, no
HTTP API surface. Login parsing lives in :mod:`app.login`; note shapes in
:mod:`app.notes`.
"""

from __future__ import annotations

import asyncio
import builtins
import logging

import httpx
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import settings

logger = logging.getLogger("xhs-planner.mcp")

# Python 3.11+ has BaseExceptionGroup; older interpreters simply never match.
_GROUPING = getattr(builtins, "BaseExceptionGroup", ())


def root_cause(exc: BaseException) -> str:
    """Dig through anyio/httpx wrapper exceptions to the actual failure."""
    current: BaseException = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _GROUPING) and current.exceptions:
            current = current.exceptions[0]
            continue
        if current.__cause__ is not None:
            current = current.__cause__
            continue
        break
    text = str(current).strip() or type(current).__name__
    return f"{type(current).__name__}: {text}"[:200]


def _is_auth_failure(message: str) -> bool:
    """A 401/403 from the MCP endpoint means the bearer token is wrong or missing."""
    lowered = message.lower()
    return any(hint in lowered for hint in ("401", "403", "unauthorized", "forbidden"))


def auth_headers() -> dict[str, str]:
    """Bearer token for the MCP endpoint, when the container enforces one."""
    if not settings.xhs.auth_token:
        return {}
    return {"Authorization": f"Bearer {settings.xhs.auth_token}"}


async def call_tool(tool: str, arguments: dict | None = None):
    """Call one MCP tool and return its raw ``CallToolResult``.

    Each call opens its own session: the container is the only stateful party,
    so there is nothing worth keeping alive between requests.
    """
    try:
        async with httpx.AsyncClient(
            headers=auth_headers(), timeout=settings.xhs.timeout_seconds
        ) as http_client:
            async with streamable_http_client(
                settings.xhs.mcp_url, http_client=http_client
            ) as streams:
                # mcp 1.x yields (read, write, get_session_id); mcp 2.x yields
                # (read, write). Only the first two are needed either way.
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await asyncio.wait_for(
                        session.call_tool(tool, arguments=arguments or {}),
                        timeout=settings.xhs.timeout_seconds,
                    )
    except TimeoutError as exc:
        logger.error("MCP tool %s timed out", tool)
        raise HTTPException(
            status_code=504,
            detail=(
                f"{tool} timed out after {settings.xhs.timeout_seconds}s. "
                f"Is the xhs-mcp container running at {settings.xhs.mcp_url}?"
            ),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        cause = root_cause(exc)
        logger.error("Cannot reach xhs-mcp at %s: %s", settings.xhs.mcp_url, cause)
        if _is_auth_failure(cause):
            detail = (
                f"The Xiaohongshu MCP server at {settings.xhs.mcp_url} rejected the "
                f"request ({cause}). If the container was started with AUTH_TOKEN, "
                "set XHS_AUTH_TOKEN in .env to the same value."
            )
        else:
            detail = (
                f"Cannot reach the Xiaohongshu MCP server at {settings.xhs.mcp_url} "
                f"({cause}). Start it with `docker compose up -d xhs-mcp`, or run "
                "`mise run doctor` for a diagnosis."
            )
        raise HTTPException(status_code=502, detail=detail) from exc


def texts(result) -> list[str]:
    """Text blocks from a tool result, in order."""
    found: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            found.append(text.strip())
    return found


def images(result) -> list[str]:
    """Image blocks as data URLs (MCP image content carries base64)."""
    found: list[str] = []
    for block in getattr(result, "content", []) or []:
        data = getattr(block, "data", None)
        if not isinstance(data, str) or not data:
            continue
        mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", None)
        if not isinstance(mime, str) or not mime.startswith("image/"):
            continue
        found.append(data if data.startswith("data:image") else f"data:{mime};base64,{data}")
    return found


def failed(result) -> bool:
    return bool(getattr(result, "isError", False))


def looks_like_auth_error(message: str) -> bool:
    """True when a tool error is about the Xiaohongshu session, not the request."""
    lowered = (message or "").lower()
    return any(
        hint in lowered
        for hint in ("登录", "login", "not signed in", "cookie", "unauthorized", "401")
    )
