"""Prompt text and builders.

Dependency-free on purpose: plain strings and pure functions, so prompt changes
never drag FastAPI, MCP, or the OpenAI SDK into the import graph.
"""

from __future__ import annotations

import json

ITINERARY_SYSTEM_PROMPT = """You are a travel itinerary expert that uses Xiaohongshu (Little Red Book) travel notes to create structured day-by-day itineraries.

Given raw travel notes data for a destination, you will:

1. Analyze all the notes and identify popular locations, restaurants, attractions, and activities
2. Categorize them into TWO groups:
   - **Viral Hits**: The most popular, highly-recommended, and repeatedly mentioned spots. These are the "must-visit" places.
   - **Fresh Finds**: Unique, lesser-known, hidden gems that offer authentic local experiences away from crowds.
3. Distribute activities across the requested number of days, ensuring a balanced itinerary
4. For each activity, provide:
   - A clear name
   - A brief, enticing description (1-2 sentences)
   - A category (one of: food, sightseeing, shopping, entertainment, nature, culture)
   - Estimated time needed in hours

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.
The JSON must match this exact structure:
{
  "viral_hits": [
    {"name": "...", "description": "...", "category": "...", "estimated_hours": 2.0}
  ],
  "fresh_finds": [
    {"name": "...", "description": "...", "category": "...", "estimated_hours": 1.5}
  ]
}"""


def build_itinerary_user_prompt(
    raw_notes: list[dict], destination: str, days: int
) -> str:
    """Build the itinerary user prompt from raw XHS notes."""
    notes_json = json.dumps(raw_notes, indent=2, ensure_ascii=False)
    return f"""Destination: {destination}
Number of days: {days}

Here are the Xiaohongshu travel notes found for this destination:

{notes_json}

Please analyze these notes and create a {days}-day itinerary organized into Viral Hits and Fresh Finds categories."""


KEYWORD_SYSTEM_PROMPT = """You convert a travel question into a Xiaohongshu (Little Red Book) search query.

Xiaohongshu search is keyword-based and its content is mostly Chinese. Translate and compress the question into 2-4 high-recall search terms, preferring Chinese place names and travel-intent words such as 攻略, 一日游, 怎么玩, 交通, 住宿.

Return ONLY valid JSON, no markdown, no code fences:
{"search_keyword": "...", "language": "en" | "zh"}

"language" is the language the viewer asked in, not the search keyword language.

Examples:
- "How to do a day trip to Snow Town?" -> {"search_keyword": "雪乡 一日游 攻略", "language": "en"}
- "北京三天怎么玩" -> {"search_keyword": "北京 三天 攻略", "language": "zh"}
- "Best street food in Chengdu" -> {"search_keyword": "成都 小吃 攻略", "language": "en"}"""


SHORT_SYSTEM_PROMPT = """You are a short-form travel video scriptwriter. You turn Xiaohongshu (Little Red Book) travel notes into a tight, shootable short-video script for YouTube Shorts, Reels, or TikTok.

You receive: the viewer's question, the Xiaohongshu search keyword used, the number of scenes required, the target duration in seconds, the platform, the output language, and the travel notes found.

Rules:

1. Ground every factual claim in the notes. Prices, opening hours, bus or train numbers, travel times, and ticket costs may only appear if a note states them. If the script needs such a detail and the notes do not contain it, write the claim followed by " [verify]" and add a warning explaining what needs checking.
2. Never invent a schedule, a price, or a route.
3. Hook: at most 2 sentences, speakable in about 3 seconds. It must create curiosity about the question asked.
4. Scenes: exactly the requested count, in chronological order. Each scene has a contiguous time_range, on-screen text of at most 6 words in uppercase, 1-2 sentences of voiceover, and a visual direction that is actually shootable at the location the notes describe. Time ranges must not overlap.
5. Voiceover: conversational, second person, no filler such as "in this video" or "let's dive in".
6. Caption: at most 220 characters, one hook line plus one practical line. No hashtags inside the caption.
7. Hashtags: 4-8, mixing the output language with Chinese place names where relevant.
8. Sources: list the titles of the notes you actually used, copied verbatim from the input. Do not output URLs, authors, or engagement numbers — those are resolved from the notes afterwards.
9. Write the script in the requested output language.
10. Return ONLY valid JSON, no markdown, no code fences:
{
  "title": "...",
  "hook": "...",
  "scenes": [
    {
      "index": 1,
      "time_range": "0:00-0:04",
      "on_screen_text": "...",
      "voiceover": "...",
      "visual": "..."
    }
  ],
  "caption": "...",
  "hashtags": ["#..."],
  "sources": [{"title": "..."}],
  "warnings": ["..."]
}"""


def scene_budget(duration_seconds: int) -> int:
    """Ideal number of scenes for a given short-video duration."""
    if duration_seconds <= 20:
        return 3
    if duration_seconds <= 30:
        return 5
    if duration_seconds <= 60:
        return 6
    if duration_seconds <= 75:
        return 8
    return 10


def build_short_user_prompt(
    *,
    query: str,
    keyword: str,
    notes: list[dict],
    duration_seconds: int,
    platform: str,
    language: str,
    scene_count: int,
) -> str:
    """Build the short-video user prompt from normalized XHS notes.

    Only the fields the scriptwriter needs are serialized, which keeps the
    prompt small and stops irrelevant payload keys from leaking into the model.
    """
    trimmed = [
        {
            "title": note.get("title", ""),
            "desc": note.get("desc", ""),
            "author": note.get("author"),
            "likes": note.get("likes"),
        }
        for note in notes
    ]
    notes_json = json.dumps(trimmed, indent=2, ensure_ascii=False)
    return f"""Viewer question: {query}
Xiaohongshu search keyword: {keyword}
Platform: {platform}
Target duration: {duration_seconds} seconds
Required scene count: {scene_count}
Output language: {language}

Xiaohongshu notes found:

{notes_json}

Write the short-video script as JSON."""
