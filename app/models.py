"""HTTP request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .config import settings

_short = settings.short


class TravelRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=100)
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


class NoteRef(BaseModel):
    """A Xiaohongshu note the generated script was grounded in."""

    title: str
    author: str | None = None
    url: str | None = None
    likes: int | None = None


class ShortScene(BaseModel):
    index: int
    time_range: str
    on_screen_text: str
    voiceover: str
    visual: str


class ShortRequest(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=200,
        examples=["How to do a day trip to Snow Town?"],
    )
    duration_seconds: int = Field(
        default=_short.default_duration_seconds,
        ge=_short.min_duration_seconds,
        le=_short.max_duration_seconds,
    )
    platform: Literal["shorts", "reels", "tiktok", "xhs"] = "shorts"
    language: Literal["en", "zh"] | None = None


class ShortResponse(BaseModel):
    query: str
    search_keyword: str
    platform: str
    duration_seconds: int
    language: str
    title: str
    hook: str
    scenes: list[ShortScene]
    caption: str
    hashtags: list[str]
    sources: list[NoteRef]
    grounding: Literal["full", "partial", "none"]
    warnings: list[str]


class XhsLoginStatus(BaseModel):
    """Whether the MCP container currently holds a Xiaohongshu session."""

    logged_in: bool
    username: str | None = None
    message: str = ""
    mcp_url: str = ""


class XhsLoginQr(BaseModel):
    image: str
    hint: str = ""
    expires_in_seconds: int = 180
