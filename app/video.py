"""Render a short-video script into a silent vertical MP4 draft.

Frames are composed with Pillow, then encoded with ffmpeg. Visuals are the
cited notes' cover images, blurred behind a scrim; when none are available the
frame falls back to a gradient. There is no audio: voiceover text is burned in
as a caption so it can be re-recorded in an editor.

ffmpeg is a system dependency. If it is missing, :func:`available` returns
False and the API answers 503 with install instructions.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from fastapi import HTTPException

from .notes import source_key

logger = logging.getLogger("xhs-planner.video")

WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_COVER_BYTES = 8 * 1024 * 1024

_CLOCK_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[–—\-~]\s*(\d{1,2}):(\d{2})")
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)


def available() -> bool:
    """True when ffmpeg can be executed."""
    return shutil.which("ffmpeg") is not None


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size, index=0)
            except OSError:
                continue
    logger.warning("No TrueType font found; falling back to the bitmap default")
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy wrap that also splits over-long words (CJK has no spaces)."""
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
            # A single word wider than the frame: break it by characters.
            current = ""
            for char in word:
                if draw.textlength(current + char, font=font) <= max_width:
                    current += char
                else:
                    lines.append(current)
                    current = char
        lines.append(current)
    return [line for line in lines if line]


def _fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
    max_lines: int,
) -> list[str]:
    lines = _wrap(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    kept = lines[: max_lines]
    kept[-1] = kept[-1].rstrip() + "…"
    return kept


def _cover_fit(image: Image.Image) -> Image.Image:
    """Scale-and-crop to the vertical frame."""
    ratio = max(WIDTH / image.width, HEIGHT / image.height)
    resized = image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.LANCZOS,
    )
    left = (resized.width - WIDTH) // 2
    top = (resized.height - HEIGHT) // 2
    return resized.crop((left, top, left + WIDTH, top + HEIGHT))


def _gradient(index: int, total: int) -> Image.Image:
    """Vertical gradient that shifts slightly per scene."""
    palette = ((15, 23, 42), (30, 27, 75), (49, 16, 61), (12, 32, 46))
    top = palette[index % len(palette)]
    bottom = (8, 11, 20)
    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        blend = y / HEIGHT
        draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(round(a + (b - a) * blend) for a, b in zip(top, bottom)),
        )
    return canvas


def _vignette(image: Image.Image) -> Image.Image:
    """Darken toward the bottom so captions keep contrast over any cover."""
    mask = Image.new("L", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(mask)
    for y in range(HEIGHT):
        draw.line([(0, y), (WIDTH, y)], fill=round(40 + 150 * (y / HEIGHT)))
    return Image.composite(Image.new("RGB", (WIDTH, HEIGHT), (3, 5, 12)), image, mask)


def _backdrop(background: Path | None, index: int, total: int) -> Image.Image:
    """Blurred, heavily darkened cover — or a gradient when there is none."""
    if background is not None:
        try:
            with Image.open(background) as raw:
                blurred = _cover_fit(raw.convert("RGB")).filter(
                    ImageFilter.GaussianBlur(30)
                )
            scrim = Image.new("RGB", (WIDTH, HEIGHT), (6, 10, 20))
            return _vignette(Image.blend(blurred, scrim, 0.72))
        except OSError:
            logger.warning("Could not read cover image %s", background)
    return _gradient(index, total)


def build_frame(
    scene: dict,
    index: int,
    total: int,
    title: str,
    background: Path | None = None,
) -> Image.Image:
    """Compose one frame: background, scene number, headline, voiceover."""
    base = _backdrop(background, index, total)
    draw = ImageDraw.Draw(base)
    margin = 72

    draw.text((margin, 110), f"SCENE {index}/{total}", font=_font(34), fill=(148, 163, 184))
    draw.text(
        (margin, 154),
        scene.get("time_range", ""),
        font=_font(30),
        fill=(100, 116, 139),
    )

    headline_font = _font(94)
    headline = (scene.get("on_screen_text") or "").upper()
    lines = _fit(draw, headline, headline_font, WIDTH - margin * 2, 3)
    line_height = 116
    block_height = line_height * len(lines)
    y = 470 - block_height // 2
    for line in lines:
        for offset, colour in ((3, (0, 0, 0)), (0, (255, 255, 255))):
            draw.text(
                (margin + offset, y + offset),
                line,
                font=headline_font,
                fill=colour,
            )
        y += line_height

    voiceover = scene.get("voiceover") or ""
    body_font = _font(46)
    body_lines = _fit(draw, voiceover, body_font, WIDTH - margin * 2, 6)
    y = max(y + 90, 640)
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=(226, 232, 240))
        y += 62

    visual = scene.get("visual") or ""
    if visual:
        note_font = _font(30)
        note_lines = _fit(draw, f"Visual: {visual}", note_font, WIDTH - margin * 2, 3)
        y = max(y + 40, HEIGHT - 420)
        for line in note_lines:
            draw.text((margin, y), line, font=note_font, fill=(148, 163, 184))
            y += 40

    footer_font = _font(28)
    draw.text((margin, HEIGHT - 120), title[:60], font=footer_font, fill=(100, 116, 139))
    draw.text(
        (margin, HEIGHT - 80),
        "draft · built from Xiaohongshu notes",
        font=footer_font,
        fill=(71, 85, 105),
    )
    return base


def scene_durations(payload: dict) -> list[float]:
    """Seconds per scene, parsed from the script, falling back to an even split."""
    scenes = payload.get("scenes") or []
    total = float(payload.get("duration_seconds") or len(scenes) or 1)
    durations: list[float] = []
    for scene in scenes:
        match = _CLOCK_RANGE.search(str(scene.get("time_range", "")))
        if match:
            start = int(match.group(1)) * 60 + int(match.group(2))
            end = int(match.group(3)) * 60 + int(match.group(4))
            durations.append(max(0.5, end - start))
        else:
            durations.append(0.0)
    if not any(durations):
        even = total / max(1, len(scenes))
        return [even] * len(scenes)
    return durations


def _covers_for_scenes(payload: dict, notes: list[dict]) -> list[str]:
    """Cover URLs ordered by the notes the script cited, then any leftovers."""
    by_key = {
        source_key(note.get("title", "")): note["cover_url"]
        for note in notes
        if note.get("cover_url")
    }
    ordered: list[str] = []
    for source in payload.get("sources") or []:
        title = source.get("title") if isinstance(source, dict) else source
        url = by_key.get(source_key(title or ""))
        if url and url not in ordered:
            ordered.append(url)
    for note in notes:
        if len(ordered) >= len(payload.get("scenes") or []):
            break
        cover = note.get("cover_url")
        if cover and cover not in ordered:
            ordered.append(cover)
    return ordered


async def _download(url: str, destination: Path) -> Path | None:
    """Fetch one cover image, ignoring size limits, redirects, and failures."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                if not response.headers.get("content-type", "").startswith("image/"):
                    return None
                size = 0
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_COVER_BYTES:
                            return None
                        handle.write(chunk)
        return destination
    except Exception as exc:  # noqa: BLE001 - covers are best-effort
        logger.warning("Cover download failed for %s: %s", url[:80], exc)
        return None


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        logger.error("ffmpeg failed: %s", completed.stderr[-800:])
        raise HTTPException(
            status_code=500,
            detail="Video rendering failed inside ffmpeg; see server logs.",
        )


def _render_sync(payload: dict, covers: list[Path | None], workdir: Path) -> Path:
    """Compose frames and encode them into one MP4."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg is not installed")

    scenes = payload.get("scenes") or []
    durations = scene_durations(payload)
    clips: list[Path] = []

    for index, scene in enumerate(scenes, start=1):
        frame_path = workdir / f"frame_{index:02d}.png"
        frame = build_frame(
            scene,
            index,
            len(scenes),
            payload.get("title", ""),
            covers[index - 1] if index - 1 < len(covers) else None,
        )
        frame.save(frame_path)

        duration = durations[index - 1]
        fade = min(0.3, duration / 4)
        clip_path = workdir / f"clip_{index:02d}.mp4"
        _run(
            [
                ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS),
                "-i", str(frame_path),
                "-t", f"{duration:.2f}",
                "-vf",
                (
                    f"scale={WIDTH}:{HEIGHT},"
                    f"fade=t=in:st=0:d={fade:.2f},"
                    f"fade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f},"
                    "format=yuv420p"
                ),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                str(clip_path),
            ]
        )
        clips.append(clip_path)

    concat_file = workdir / "clips.txt"
    concat_file.write_text(
        "\n".join(f"file '{clip.name}'" for clip in clips), encoding="utf-8"
    )
    output = workdir / "short-draft.mp4"
    _run(
        [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy", "-movflags", "+faststart",
            str(output),
        ]
    )
    return output


async def render(payload: dict, notes: list[dict]) -> tuple[Path, Path]:
    """Render the script; returns ``(video_path, workdir)`` for cleanup."""
    workdir = Path(tempfile.mkdtemp(prefix="xhs-video-"))
    cover_urls = _covers_for_scenes(payload, notes)
    covers: list[Path | None] = []
    for index, url in enumerate(cover_urls):
        covers.append(await _download(url, workdir / f"cover_{index:02d}.jpg"))

    try:
        video = await asyncio.to_thread(_render_sync, payload, covers, workdir)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return video, workdir
