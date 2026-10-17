"""Xiaohongshu note retrieval and normalization.

MCP payload shapes are undocumented and change between releases, so
normalization is defensive: unknown structures yield ``[]`` instead of raising.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException

from . import mcp
from .config import settings
from .models import NoteRef

logger = logging.getLogger("xhs-planner.notes")

XHS_WEB_URL = "https://www.xiaohongshu.com"

_NOTE_ID_KEYS = ("note_id", "noteId", "id", "noteID")
_NOTE_TITLE_KEYS = ("title", "display_title", "displayTitle", "note_title")
_NOTE_DESC_KEYS = ("desc", "description", "content", "note_content", "noteContent")
_NOTE_URL_KEYS = ("url", "note_url", "noteUrl", "share_url", "shareUrl")
_NOTE_AUTHOR_KEYS = ("author", "user", "user_info", "userInfo", "nickname", "user_name")
_NOTE_CARD_KEYS = ("noteCard", "note_card", "note")
_NOTE_TOKEN_KEYS = ("xsecToken", "xsec_token")
_NOTE_LIKE_KEYS = (
    "likes",
    "liked_count",
    "likedCount",
    "like_count",
    "likeCount",
    "interact_info",
    "interactInfo",
)


def _first_present(source: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def clean_text(value: Any) -> str:
    """Flatten a scraped string: strip tags, entities, and whitespace."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def parse_count(value: Any) -> int | None:
    """Parse engagement counts, including Chinese shorthand such as ``1.2万``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    match = re.match(r"^\s*([\d.]+)\s*(万|w|k)?", value, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in ("万", "w"):
        number *= 10_000
    elif unit == "k":
        number *= 1_000
    return int(number)


def _extract_author(source: dict) -> str | None:
    value = _first_present(source, _NOTE_AUTHOR_KEYS)
    if isinstance(value, dict):
        nested = _first_present(value, ("nickname", "name", "nick_name", "nickName"))
        return clean_text(nested) or None
    if isinstance(value, str):
        return clean_text(value) or None
    return None


def _extract_likes(source: dict) -> int | None:
    value = _first_present(source, _NOTE_LIKE_KEYS)
    if isinstance(value, dict):
        value = _first_present(
            value, ("liked_count", "likedCount", "like_count", "likeCount", "count")
        )
    return parse_count(value)


def _merge_card(source: dict) -> dict:
    """Flatten wrapper+card payloads.

    Search results look like ``{id, xsecToken, noteCard: {displayTitle, ...}}``:
    identity lives on the wrapper, the human-readable fields inside the card.
    """
    merged = dict(source)
    for key in _NOTE_CARD_KEYS:
        card = source.get(key)
        if not isinstance(card, dict):
            continue
        for field, value in card.items():
            merged.setdefault(field, value)
    return merged


def _extract_cover(source: dict) -> str | None:
    """Cover image URL, when the payload carries one (used for video frames)."""
    cover = source.get("cover") or source.get("coverUrl") or source.get("cover_url")
    if isinstance(cover, dict):
        cover = _first_present(cover, ("urlDefault", "urlPre", "url", "url_default"))
    if isinstance(cover, str) and cover.startswith("http"):
        return cover
    return None


def _note_from_dict(source: dict) -> dict | None:
    """Build a normalized note from a dict, or None when it is not a note."""
    note_id = _first_present(source, _NOTE_ID_KEYS)
    url = _first_present(source, _NOTE_URL_KEYS)
    title = clean_text(_first_present(source, _NOTE_TITLE_KEYS))
    desc = clean_text(_first_present(source, _NOTE_DESC_KEYS))

    if not (note_id or url) or not (title or desc):
        return None
    if not url and note_id:
        url = f"{XHS_WEB_URL}/explore/{note_id}"

    return {
        "id": str(note_id or url),
        "title": title or desc[:60],
        "desc": desc or title,
        "author": _extract_author(source),
        "likes": _extract_likes(source),
        "url": str(url),
        "xsec_token": _first_present(source, _NOTE_TOKEN_KEYS),
        "cover_url": _extract_cover(source),
    }


def _collect_notes(node: Any, found: list[dict], depth: int = 0) -> None:
    """Walk an arbitrary JSON payload collecting note-like dicts."""
    if depth > 8:
        return
    if isinstance(node, dict):
        note = _note_from_dict(_merge_card(node))
        if note:
            found.append(note)
        for value in node.values():
            _collect_notes(value, found, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _collect_notes(item, found, depth + 1)


def normalize_notes(parsed: list[dict], limit: int | None = None) -> list[dict]:
    """Turn MCP search output into deduplicated note dicts."""
    cap = limit or settings.xhs.max_notes
    found: list[dict] = []
    for blob in parsed or []:
        _collect_notes(blob, found)

    unique: list[dict] = []
    seen: set[str] = set()
    for note in found:
        if note["id"] in seen:
            continue
        seen.add(note["id"])
        unique.append(note)
        if len(unique) >= cap:
            break
    return unique


async def search_notes(keyword: str) -> list[dict]:
    """Search Xiaohongshu and return the raw parsed payload blocks."""
    result = await mcp.call_tool(
        "search_feeds",
        arguments={
            "keyword": keyword,
            "filters": {
                "sort_by": settings.xhs.sort_by,
                "note_type": settings.xhs.note_type,
            },
        },
    )

    if mcp.failed(result):
        message = " ".join(mcp.texts(result))[:300]
        logger.error("search_feeds failed for %r: %s", keyword, message)
        if mcp.looks_like_auth_error(message):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "XHS_NOT_LOGGED_IN",
                    "message": message or "Xiaohongshu is not signed in.",
                },
            )
        raise HTTPException(
            status_code=502,
            detail=f"Xiaohongshu search failed: {message or 'unknown error'}",
        )

    parsed: list[dict] = []
    for text in mcp.texts(result):
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            parsed.append({"text": text})
    return parsed


def source_key(title: str) -> str:
    """Loose comparison key so model-quoted titles match normalized ones."""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title or "").lower()


def resolve_sources(
    model_sources: Any, notes: list[dict], limit: int | None = None
) -> list[NoteRef]:
    """Map model-cited titles back to real notes.

    URLs, authors, and like counts always come from the scraped notes â€” a quoted
    title is the only thing the model is trusted with.
    """
    cap = limit or settings.short.max_sources
    by_key = {source_key(note["title"]): note for note in notes}
    resolved: list[NoteRef] = []
    seen: set[str] = set()
    for entry in model_sources or []:
        title = entry.get("title") if isinstance(entry, dict) else entry
        if not isinstance(title, str):
            continue
        note = by_key.get(source_key(title))
        if not note or note["url"] in seen:
            continue
        seen.add(note["url"])
        resolved.append(
            NoteRef(
                title=note["title"],
                author=note["author"],
                url=note["url"],
                likes=note["likes"],
            )
        )
        if len(resolved) >= cap:
            break
    return resolved
