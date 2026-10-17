"""HTTP surface: routes, response assembly, and app lifecycle.

Business rules live in the feature modules (``notes``, ``short``,
``itinerary``, ``login``); this module only wires them to endpoints.
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import login, video
from .config import PROJECT_ROOT, settings
from .itinerary import generate_itinerary
from .models import (
    Activity,
    ItineraryResponse,
    ShortRequest,
    ShortResponse,
    TravelRequest,
    XhsLoginQr,
    XhsLoginStatus,
)
from .notes import normalize_notes, search_notes
from .short import extract_search_plan, generate_short

logger = logging.getLogger("xhs-planner")

STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Surface configuration problems at boot instead of mid-request."""
    logger.info("XHS MCP endpoint: %s", settings.xhs.mcp_url)
    if settings.xhs.auth_token:
        logger.info("MCP bearer token configured via XHS_AUTH_TOKEN")
    if settings.deepseek.api_key:
        logger.info("DeepSeek API key configured")
    else:
        logger.warning(
            "DEEPSEEK_API_KEY is not set — AI endpoints will answer 503 until it is."
        )
    logger.info(
        "Xiaohongshu sign-in lives in the MCP container; the UI prompts with a QR code "
        "when a search needs a session."
    )
    yield


app = FastAPI(title=settings.app.title, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serve the frontend page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(str(index_path))


@app.post("/generate-itinerary", response_model=ItineraryResponse)
async def create_itinerary(request: TravelRequest):
    """Search -> categorize -> Viral Hits + Fresh Finds."""
    raw_notes = await search_notes(request.destination)
    data = await generate_itinerary(
        raw_notes=raw_notes,
        destination=request.destination,
        days=request.days,
    )
    return ItineraryResponse(
        destination=request.destination,
        days=request.days,
        viral_hits=[Activity(**activity) for activity in data.get("viral_hits", [])],
        fresh_finds=[Activity(**activity) for activity in data.get("fresh_finds", [])],
    )


async def _build_short(request: ShortRequest) -> tuple[dict, list[dict]]:
    """Shared pipeline: keyword -> notes -> grounded script payload."""
    plan = await extract_search_plan(request.query)
    keyword = plan["search_keyword"]
    language = request.language or plan["language"]

    notes = normalize_notes(await search_notes(keyword))
    if not notes:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No Xiaohongshu notes found for '{keyword}'. "
                "Try a different phrasing, or add the place name in Chinese."
            ),
        )

    payload = await generate_short(
        query=request.query,
        keyword=keyword,
        notes=notes,
        duration_seconds=request.duration_seconds,
        platform=request.platform,
        language=language,
    )
    return payload, notes


@app.post("/generate-short", response_model=ShortResponse)
async def create_short(request: ShortRequest):
    """Question -> search keyword -> notes -> grounded, timed script."""
    payload, _notes = await _build_short(request)
    return ShortResponse(**payload)


@app.post("/generate-short/video")
async def create_short_video(request: ShortRequest):
    """Same pipeline, rendered to a silent vertical MP4 draft."""
    if not video.available():
        raise HTTPException(
            status_code=503,
            detail=(
                "ffmpeg is not installed, so the video cannot be rendered. "
                "Install it (Ubuntu: `sudo apt install ffmpeg`; Windows: "
                "`winget install Gyan.FFmpeg`) and retry."
            ),
        )

    payload, notes = await _build_short(request)
    path, workdir = await video.render(payload, notes)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename="short-draft.mp4",
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


@app.get("/api/xhs/login", response_model=XhsLoginStatus)
async def xhs_login_status():
    """Report whether the MCP container holds a Xiaohongshu session."""
    logged_in, username, message = await login.status()
    return XhsLoginStatus(
        logged_in=logged_in,
        username=username,
        message=message,
        mcp_url=settings.xhs.mcp_url,
    )


@app.post("/api/xhs/login/qrcode", response_model=XhsLoginQr)
async def xhs_login_qrcode():
    """Return the login QR code so the user can scan it with the XHS app."""
    return await login.qrcode()


@app.post("/api/xhs/logout")
async def xhs_logout():
    """Clear the container's session (MCP tool ``delete_cookies``)."""
    return await login.logout()
