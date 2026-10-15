"""Xiaohongshu Web Scraper — Direct HTTP scraping module.

Provides an alternative data source to the MCP-based ``fetch_xhs_notes``
approach.  Supports two strategies:

1. **HTTPX** (default) — Sends browser-emulated HTTP requests to
   Xiaohongshu's web search page and extracts note data from the initial
   server-rendered HTML + embedded JSON payloads.

2. **Playwright** (optional) — Launches a headless Chromium browser for
   full JavaScript rendering.  More reliable but requires ``playwright``
   to be installed and browsers downloaded.

Usage::

    from scraper import scrape_xhs_notes

    notes = await scrape_xhs_notes("Tokyo travel", cookie="...")
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import httpx

logger = logging.getLogger("xhs-scraper")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XHS_BASE_URL = "https://www.xiaohongshu.com"
XHS_SEARCH_URL = f"{XHS_BASE_URL}/search_result"

# Realistic browser fingerprint
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Not/A)Brand";v="99", "Google Chrome";v="125", "Chromium";v="125"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Dnt": "1",
    "Connection": "keep-alive",
}

# Regex patterns to extract embedded JSON from Xiaohongshu's HTML
# XHS embeds initial state data in <script> tags with specific IDs
RE_INITIAL_STATE = re.compile(
    r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
    re.DOTALL,
)
RE_NOTE_DATA = re.compile(
    r'<script[^>]*>window\.__nuxt_ssr_data\s*=\s*({.*?})</script>',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _make_cookie_headers(cookie: str) -> dict[str, str]:
    """Build a Cookie header dict from a raw cookie string."""
    return {"Cookie": cookie.strip()}


def _generate_xs_xt() -> tuple[str, str]:
    """Generate mock X-S / X-T signature headers.

    .. note::
       Xiaohongshu uses a signature mechanism (X-S, X-T) for API calls.
       This implementation provides placeholder values that work for the
       public web search page but will NOT work for the private API.
       For production use, integrate a proper signature generator or use
       the Playwright strategy.
    """
    # X-T is a 13-digit millisecond timestamp
    xt = str(int(__import__("time").time() * 1000))
    # X-S is a 32-character hex string (placeholder)
    xs = uuid.uuid4().hex
    return xs, xt


# ---------------------------------------------------------------------------
# HTML Parsing
# ---------------------------------------------------------------------------


def _parse_search_html(html: str, keyword: str) -> list[dict]:
    """Parse Xiaohongshu search result HTML for note data.

    Extracts note information from:
    1. ``window.__INITIAL_STATE__`` JSON payload
    2. Server-rendered HTML note cards (fallback)

    Returns a list of note dicts with keys: ``id``, ``title``, ``desc``,
    ``user``, ``likes``, ``tags``, ``url``.
    """
    notes: list[dict] = []
    seen_ids: set[str] = set()

    # --- Strategy 1: Extract from __INITIAL_STATE__ ---
    match = RE_INITIAL_STATE.search(html)
    if match:
        try:
            state = json.loads(match.group(1))
            # Navigate to the search result section
            # Structure varies; try common paths
            for key in ("searchResult", "search", "feed"):
                feed_data = _deep_get(state, key, "items") or \
                            _deep_get(state, key, "notes")
                if feed_data:
                    break
            else:
                # Try to find any array of note objects in the state
                feed_data = _find_note_list(state)

            if feed_data and isinstance(feed_data, list):
                for item in feed_data:
                    note = _extract_note_from_state(item)
                    if note and note["id"] not in seen_ids:
                        seen_ids.add(note["id"])
                        notes.append(note)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug("Failed to parse __INITIAL_STATE__: %s", exc)

    # --- Strategy 2: Regex-based extraction from HTML ---
    if not notes:
        # Try to find note cards in the rendered HTML
        note_pattern = re.compile(
            r'<div[^>]*class="[^"]*note-item[^"]*"[^>]*>.*?</div>',
            re.DOTALL | re.IGNORECASE,
        )
        for card in note_pattern.finditer(html):
            card_html = card.group()
            note_id_match = re.search(r'data-note-id=["\']([^"\']+)', card_html)
            title_match = re.search(r'<[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</',
                                    card_html, re.DOTALL)
            desc_match = re.search(r'<[^>]*class="[^"]*desc[^"]*"[^>]*>(.*?)</',
                                   card_html, re.DOTALL)

            note_id = note_id_match.group(1) if note_id_match else str(uuid.uuid4())
            if note_id not in seen_ids:
                seen_ids.add(note_id)
                notes.append({
                    "id": note_id,
                    "title": _clean_html(title_match.group(1)) if title_match else "",
                    "desc": _clean_html(desc_match.group(1)) if desc_match else "",
                    "url": f"{XHS_BASE_URL}/explore/{note_id}",
                    "source": "scraper",
                })

    # --- Strategy 3: Extract from script JSON payloads ---
    if not notes:
        script_pattern = re.compile(
            r'<script[^>]+type="application/json"[^>]*>({.*?})</script>',
            re.DOTALL,
        )
        for script_match in script_pattern.finditer(html):
            try:
                data = json.loads(script_match.group(1))
                extracted = _extract_notes_from_json(data)
                for note in extracted:
                    if note["id"] not in seen_ids:
                        seen_ids.add(note["id"])
                        notes.append(note)
            except (json.JSONDecodeError, TypeError):
                continue

    logger.info(
        "Parsed %d notes from search HTML for keyword '%s'",
        len(notes), keyword,
    )
    return notes


def _deep_get(obj: Any, *keys: str) -> Any:
    """Safely traverse nested dicts."""
    for key in keys:
        try:
            obj = obj[key]
        except (KeyError, TypeError, IndexError):
            return None
    return obj


