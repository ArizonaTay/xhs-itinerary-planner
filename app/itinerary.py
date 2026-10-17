"""Itinerary generation: raw notes in, categorized activities out."""

from __future__ import annotations

from .config import settings
from .llm import complete_json
from .prompts import ITINERARY_SYSTEM_PROMPT, build_itinerary_user_prompt


async def generate_itinerary(
    raw_notes: list[dict], destination: str, days: int
) -> dict:
    """Return a dict with ``viral_hits`` and ``fresh_finds`` activity lists."""
    return await complete_json(
        ITINERARY_SYSTEM_PROMPT,
        build_itinerary_user_prompt(raw_notes, destination, days),
        max_tokens=settings.deepseek.max_tokens.itinerary,
    )
