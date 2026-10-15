"""XHS Travel Planner — FastAPI Backend

Connects to Xiaohongshu MCP via Streamable HTTP transport to fetch travel notes,
then uses DeepSeek AI to generate structured, categorized itineraries.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# MCP client imports
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("xhs-planner")
logging.basicConfig(level=logging.INFO)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
XHS_COOKIE = os.getenv("XHS_COOKIE", "")
XHS_MCP_URL = os.getenv("XHS_MCP_URL", "http://localhost:18060/mcp")
MCP_TIMEOUT_SECONDS = int(os.getenv("MCP_TIMEOUT_SECONDS", "120"))
DEEPSEEK_TIMEOUT_SECONDS = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class TravelRequest(BaseModel):
    destination: str
    days: int = Field(ge=1, le=30)


class Activity(BaseModel):
    name: str
    description: str
    category: str
    estimated_hours: float


class ItineraryResponse(BaseModel):
    destination: str
    days: int
    viral_hits: list[Activity]
    fresh_finds: list[Activity]


# ---------------------------------------------------------------------------
# DeepSeek Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a travel itinerary expert that uses Xiaohongshu (Little Red Book) travel notes to create structured day-by-day itineraries.

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


def _build_user_prompt(raw_notes: list[dict], destination: str, days: int) -> str:
    """Build the user prompt from raw XHS notes."""
    notes_json = json.dumps(raw_notes, indent=2, ensure_ascii=False)
    return f"""Destination: {destination}
Number of days: {days}

Here are the Xiaohongshu travel notes found for this destination:

{notes_json}

Please analyze these notes and create a {days}-day itinerary organized into Viral Hits and Fresh Finds categories."""


# ---------------------------------------------------------------------------
# MCP Client — Xiaohongshu Notes Fetcher (Streamable HTTP)
# ---------------------------------------------------------------------------


async def fetch_xhs_notes(destination: str) -> list[dict]:
    """Connect to xiaohongshu-mcp via Streamable HTTP and search for travel notes.

    Connects to the xhs-mcp service (running as a separate container) via
    Streamable HTTP transport, calls the ``search_feeds`` tool, and returns
    parsed results.
    """
    if not XHS_COOKIE:
        logger.warning("XHS_COOKIE not set — returning empty notes list")
        return []

    try:
        async with streamable_http_client(XHS_MCP_URL) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Call the search_feeds tool with keyword argument
                # Tool signature from xiaohongshu-mcp source:
                #   search_feeds(keyword, filters?)
                result = await session.call_tool(
                    "search_feeds",
                    arguments={
                        "keyword": destination,
                        "filters": {
                            "sort_by": "最新",
                            "note_type": "图文",
                        },
                    },
                )

                if result.isError:
                    logger.error("MCP tool call returned error")
                    return []

                # Parse content blocks — extract text content
                parsed = []
                for block in result.content:
                    if hasattr(block, "text") and block.text:
                        try:
                            parsed.append(json.loads(block.text))
                        except (json.JSONDecodeError, TypeError):
                            parsed.append({"text": block.text})
                    elif hasattr(block, "text"):
                        parsed.append({"text": block.text})

                return parsed

    except Exception as exc:
        logger.error("Failed to fetch XHS notes: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Xiaohongshu notes: {exc}",
        )


# ---------------------------------------------------------------------------
# DeepSeek AI — Itinerary Generator
# ---------------------------------------------------------------------------


async def generate_itinerary(
    raw_notes: list[dict],
    destination: str,
    days: int,
) -> dict:
    """Send raw Xiaohongshu data to DeepSeek for structured itinerary generation.

    Returns a dict with ``viral_hits`` and ``fresh_finds`` keys, each
    containing a list of activity dicts.
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set — falling back to mock data")
        return _mock_itinerary(destination, days)

    client = AsyncOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1",
        timeout=DEEPSEEK_TIMEOUT_SECONDS,
    )

    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_prompt(raw_notes, destination, days),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=4096,
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("DeepSeek returned empty response")

        return json.loads(content)

    except Exception as exc:
        logger.error("DeepSeek API call failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to generate itinerary with AI: {exc}",
        )


# ---------------------------------------------------------------------------
# Fallback Mock Data (when no API keys are configured)
# ---------------------------------------------------------------------------


def _mock_itinerary(destination: str, days: int) -> dict:
    """Return a hardcoded mock itinerary when no DeepSeek key is configured."""
    return {
        "viral_hits": [
            {
                "name": "Senso-ji Temple",
                "description": "Tokyo's oldest temple, a must-visit iconic landmark with Nakamise shopping street.",
                "category": "sightseeing",
                "estimated_hours": 2.0,
            },
            {
                "name": "Shibuya Crossing",
                "description": "The world's busiest pedestrian crossing, surrounded by shops, restaurants, and entertainment.",
                "category": "sightseeing",
                "estimated_hours": 1.5,
            },
            {
                "name": "Tsukiji Outer Market",
                "description": "Famous seafood market with fresh sushi, street food stalls, and local delicacies.",
                "category": "food",
                "estimated_hours": 2.5,
            },
        ],
        "fresh_finds": [
            {
                "name": "Yanaka Ginza",
                "description": "A nostalgic shopping street preserving Showa-era charm, perfect for a quiet stroll.",
                "category": "shopping",
                "estimated_hours": 1.5,
            },
            {
                "name": "Shimokitazawa",
                "description": "Hip neighborhood with vintage shops, indie cafes, and live music venues.",
                "category": "shopping",
                "estimated_hours": 3.0,
            },
            {
                "name": "Kagurazaka",
                "description": "A hidden gem with French-Japanese fusion cuisine and traditional cobblestone alleys.",
                "category": "food",
                "estimated_hours": 2.0,
            },
        ],
    }


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="XHS Travel Planner")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serve the main frontend page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(str(index_path))


@app.post("/generate-itinerary", response_model=ItineraryResponse)
async def generate_itinerary_endpoint(request: TravelRequest):
    """Generate a travel itinerary from Xiaohongshu data using AI.

    1. Fetch raw notes from Xiaohongshu MCP
    2. Send to DeepSeek for structured categorization
    3. Return Viral Hits + Fresh Finds
    """
    # Step 1: Fetch raw notes from Xiaohongshu
    raw_notes = await fetch_xhs_notes(request.destination)

    # Step 2: Generate structured itinerary via DeepSeek
    itinerary_data = await generate_itinerary(
        raw_notes=raw_notes,
        destination=request.destination,
        days=request.days,
    )

    # Step 3: Build and return the response
    return ItineraryResponse(
        destination=request.destination,
        days=request.days,
        viral_hits=[Activity(**a) for a in itinerary_data.get("viral_hits", [])],
        fresh_finds=[Activity(**a) for a in itinerary_data.get("fresh_finds", [])],
    )
