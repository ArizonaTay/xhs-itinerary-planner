"""Short-video generation: keyword resolution, timed scenes, grounding rules."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import HTTPException

from .config import settings
from .llm import complete_json
from .models import NoteRef
from .notes import clean_text, resolve_sources
from .prompts import (
    KEYWORD_SYSTEM_PROMPT,
    SHORT_SYSTEM_PROMPT,
    build_short_user_prompt,
    scene_budget,
)

_CLOCK_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[–—\-~]\s*(\d{1,2}):(\d{2})")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LEADING_FILLER = {
    "how", "what", "what's", "where", "when", "why", "which", "who",
    "to", "do", "does", "did", "can", "could", "should", "would", "will",
    "i", "you", "we", "one", "is", "are", "was", "were",
    "tell", "me", "show", "give", "about", "the", "a", "an",
}


def format_clock(total_seconds: int) -> str:
    """Format seconds as ``M:SS``."""
    total = max(0, int(total_seconds))
    return f"{total // 60}:{total % 60:02d}"


def parse_clock_range(value: str) -> tuple[int, int] | None:
    """Parse ``0:00-0:04`` (hyphen, en dash, em dash, tilde) into seconds."""
    match = _CLOCK_RANGE.search(value or "")
    if not match:
        return None
    start_min, start_sec, end_min, end_sec = (int(group) for group in match.groups())
    start = start_min * 60 + start_sec
    end = end_min * 60 + end_sec
    return (start, end) if end > start else None


def rebalance_timings(scenes: list[dict], duration_seconds: int) -> None:
    """Distribute scenes evenly across the target duration, in place."""
    if not scenes:
        return
    slice_seconds = duration_seconds / len(scenes)
    for offset, scene in enumerate(scenes):
        start = round(offset * slice_seconds)
        end = round((offset + 1) * slice_seconds)
        scene["time_range"] = f"{format_clock(start)}–{format_clock(end)}"


def apply_model_timings(scenes: list[dict], duration_seconds: int) -> bool:
    """Keep model timings when they are sane; otherwise signal a rebalance.

    Returns True when the scenes were left untouched.
    """
    parsed = [parse_clock_range(scene.get("time_range", "")) for scene in scenes]
    if not parsed or any(item is None for item in parsed):
        return False
    if abs(parsed[-1][1] - duration_seconds) > 2:  # type: ignore[index]
        return False
    for scene, (start, end) in zip(scenes, parsed):
        scene["time_range"] = f"{format_clock(start)}–{format_clock(end)}"  # type: ignore[index]
    return True


def fallback_keyword(query: str) -> str:
    """Heuristic search keyword used when no keyword model is available.

    Strips interrogative scaffolding; keeps an English query searchable rather
    than translating it, which degrades recall but never fails the request.
    """
    text = re.sub(r"[?!.,;:]+", " ", query or "")
    words = text.split()
    while words and words[0].lower() in _LEADING_FILLER:
        words.pop(0)
    return " ".join(words).strip()[:80] or (query or "").strip()[:80] or "travel"


def guess_language(text: str) -> str:
    """``zh`` when the text contains CJK characters, else ``en``."""
    return "zh" if _CJK.search(text or "") else "en"


def coerce_scenes(model_scenes, duration_seconds: int) -> list[dict]:
    """Normalize model scenes and guarantee timings that add up."""
    scenes: list[dict] = []
    if isinstance(model_scenes, list):
        for item in model_scenes[: settings.short.max_scenes]:
            if not isinstance(item, dict):
                continue
            voiceover = clean_text(item.get("voiceover"))
            on_screen = clean_text(item.get("on_screen_text"))
            if not (voiceover or on_screen):
                continue
            scenes.append(
                {
                    "index": len(scenes) + 1,
                    "time_range": str(item.get("time_range") or ""),
                    "on_screen_text": on_screen,
                    "voiceover": voiceover,
                    "visual": clean_text(item.get("visual")),
                }
            )
    if not apply_model_timings(scenes, duration_seconds):
        rebalance_timings(scenes, duration_seconds)
    return scenes


def normalize_hashtags(model_tags) -> list[str]:
    tags: list[str] = []
    for raw in model_tags or []:
        if not isinstance(raw, str):
            continue
        tag = raw.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        if tag not in tags:
            tags.append(tag)
    return tags[:8] or ["#travel", "#xiaohongshu"]


async def extract_search_plan(query: str) -> dict:
    """Turn a natural-language question into a Xiaohongshu search keyword.

    Returns ``{"search_keyword": str, "language": "en" | "zh"}``. Falls back to
    a heuristic when no API key is configured or the call fails, because a
    degraded search beats a failed request.
    """
    fallback = {
        "search_keyword": fallback_keyword(query),
        "language": guess_language(query),
    }
    if not settings.deepseek.api_key:
        return fallback

    try:
        data = await complete_json(
            KEYWORD_SYSTEM_PROMPT,
            query,
            max_tokens=settings.deepseek.max_tokens.keyword,
        )
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return fallback

    keyword = str(data.get("search_keyword") or "").strip()
    if not keyword:
        return fallback
    language = data.get("language")
    return {
        "search_keyword": keyword[:100],
        "language": language if language in ("en", "zh") else guess_language(query),
    }


async def generate_short(
    *,
    query: str,
    keyword: str,
    notes: list[dict],
    duration_seconds: int,
    platform: str,
    language: str,
) -> dict:
    """Generate a short-video script grounded in normalized XHS notes."""
    scene_count = min(scene_budget(duration_seconds), settings.short.max_scenes)
    data = await complete_json(
        SHORT_SYSTEM_PROMPT,
        build_short_user_prompt(
            query=query,
            keyword=keyword,
            notes=notes,
            duration_seconds=duration_seconds,
            platform=platform,
            language=language,
            scene_count=scene_count,
        ),
        max_tokens=settings.deepseek.max_tokens.short,
    )

    warnings = [
        clean_text(warning) for warning in (data.get("warnings") or []) if clean_text(warning)
    ]
    title = clean_text(data.get("title")) or query
    hook = clean_text(data.get("hook")) or f"Everything you need to know: {query}"
    scenes = coerce_scenes(data.get("scenes"), duration_seconds)
    if not scenes:
        raise HTTPException(
            status_code=502,
            detail="AI returned a script with no usable scenes — try again",
        )

    caption = clean_text(data.get("caption"))
    sources = resolve_sources(data.get("sources"), notes)
    if not sources:
        sources = [
            NoteRef(
                title=note["title"],
                author=note["author"],
                url=note["url"],
                likes=note["likes"],
            )
            for note in notes[:3]
        ]
        warnings.append(
            "The script did not cite specific notes; sources shown are the top "
            "search results and may not support every claim."
        )

    script_text = " ".join(
        [hook, caption]
        + [f"{scene['on_screen_text']} {scene['voiceover']} {scene['visual']}" for scene in scenes]
    )
    grounding: Literal["full", "partial", "none"] = (
        "partial" if "[verify]" in script_text.lower() or warnings else "full"
    )

    return {
        "query": query,
        "search_keyword": keyword,
        "platform": platform,
        "duration_seconds": duration_seconds,
        "language": language,
        "title": title,
        "hook": hook,
        "scenes": scenes,
        "caption": caption,
        "hashtags": normalize_hashtags(data.get("hashtags")),
        "sources": sources,
        "grounding": grounding,
        "warnings": warnings,
    }
