"""Xiaohongshu sign-in relay.

The MCP container owns the session. This module interprets the container's
``check_login_status`` / ``get_login_qrcode`` replies and nothing more.
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import HTTPException

from . import mcp
from .models import XhsLoginQr

logger = logging.getLogger("xhs-planner.login")

_FALSE_HINTS = (
    "未登录",
    "尚未登录",
    "登录失效",
    "登录已过期",
    "not logged in",
    "login required",
    "no login",
    "not login",
)
_TRUE_HINTS = (
    "已登录",
    "已登陆",
    "登录成功",
    "logged in",
    "login success",
    "already logged",
)
_USERNAME_PATTERN = re.compile(
    r"(?:用户名|账号|昵称|username|nickname|user(?:name)?)\s*[:：]\s*([^\s,，。;；]+)",
    re.IGNORECASE,
)


def parse_status(texts: list[str]) -> tuple[bool, str | None]:
    """Interpret ``check_login_status`` output.

    The tool's reply is prose today, so both a JSON payload and Chinese/English
    phrasing are accepted. Anything ambiguous counts as "not logged in", which
    prompts the QR flow instead of failing a search later.
    """
    blob = " ".join(texts)
    username_match = _USERNAME_PATTERN.search(blob)
    username = username_match.group(1) if username_match else None

    for text in texts:
        stripped = text.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("username", "nickname", "user_name", "userName", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                username = value.strip()
                break
        for key in (
            "is_login",
            "isLogin",
            "logged_in",
            "loggedIn",
            "login_status",
            "loginStatus",
        ):
            value = payload.get(key)
            if isinstance(value, bool):
                return value, username

    lowered = blob.lower()
    if any(hint in lowered for hint in _FALSE_HINTS):
        return False, None
    if any(hint in lowered for hint in _TRUE_HINTS):
        return True, username
    return False, None


async def status() -> tuple[bool, str | None, str]:
    """Return ``(logged_in, username, raw_message)`` for the current session."""
    result = await mcp.call_tool("check_login_status")
    message = " ".join(mcp.texts(result))[:300]
    if mcp.failed(result):
        raise HTTPException(
            status_code=502,
            detail=f"check_login_status failed: {message or 'unknown error'}",
        )
    logged_in, username = parse_status(mcp.texts(result))
    return logged_in, username, message


async def qrcode() -> XhsLoginQr:
    """Fetch the login QR code the container renders for phone scanning."""
    result = await mcp.call_tool("get_login_qrcode")
    found = mcp.images(result)
    hint = " ".join(mcp.texts(result))[:300]
    if not found:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "XHS_QRCODE_UNAVAILABLE",
                "message": hint or "The MCP server returned no QR code image.",
            },
        )
    return XhsLoginQr(image=found[0], hint=hint)


async def logout() -> dict:
    """Clear the container's session (``delete_cookies``)."""
    result = await mcp.call_tool("delete_cookies")
    message = " ".join(mcp.texts(result))[:300]
    return {"ok": not mcp.failed(result), "message": message}
