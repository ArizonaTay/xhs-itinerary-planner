"""Video draft rendering: timing, frames, and the encode step."""

import shutil

import pytest

from app import video

PAYLOAD = {
    "title": "Snow Town in One Day",
    "duration_seconds": 4,
    "scenes": [
        {
            "index": 1,
            "time_range": "0:00-0:02",
            "on_screen_text": "SNOW TOWN IN 1 DAY",
            "voiceover": "Here is the day-trip version.",
            "visual": "Walk toward the gate",
        },
        {
            "index": 2,
            "time_range": "0:02-0:04",
            "on_screen_text": "一日游也能玩",
            "voiceover": "从亚布力出发，包车当天往返。",
            "visual": "Car on a snowy road",
        },
    ],
    "sources": [],
}


def test_scene_durations_are_parsed_from_time_ranges():
    payload = {
        "duration_seconds": 30,
        "scenes": [{"time_range": "0:00–0:04"}, {"time_range": "0:04–0:10"}],
    }
    assert video.scene_durations(payload) == [4.0, 6.0]


def test_scene_durations_fall_back_to_an_even_split():
    payload = {"duration_seconds": 10, "scenes": [{}, {}]}
    assert video.scene_durations(payload) == [5.0, 5.0]


def test_frame_is_vertical_and_renders_without_a_cover():
    scene = PAYLOAD["scenes"][0]
    frame = video.build_frame(scene, 1, 2, "Title")

    assert frame.size == (video.WIDTH, video.HEIGHT)


@pytest.mark.asyncio
@pytest.mark.skipif(not video.available(), reason="ffmpeg is not installed")
async def test_render_produces_an_mp4():
    path, workdir = await video.render(PAYLOAD, notes=[])
    try:
        assert path.exists()
        assert path.stat().st_size > 10_000
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_video_endpoint_streams_an_mp4(client, monkeypatch, tmp_path):
    rendered = tmp_path / "out.mp4"
    rendered.write_bytes(b"fake-mp4-bytes")

    async def fake_build(request):
        return PAYLOAD, []

    async def fake_render(payload, notes):
        return rendered, tmp_path

    monkeypatch.setattr("app.routes._build_short", fake_build)
    monkeypatch.setattr("app.routes.video.render", fake_render)
    monkeypatch.setattr("app.routes.video.available", lambda: True)

    response = await client.post(
        "/generate-short/video", json={"query": "Snow Town day trip"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"fake-mp4-bytes"


@pytest.mark.asyncio
async def test_video_endpoint_explains_missing_ffmpeg(client, monkeypatch):
    monkeypatch.setattr("app.routes.video.available", lambda: False)

    response = await client.post(
        "/generate-short/video", json={"query": "Snow Town day trip"}
    )

    assert response.status_code == 503
    assert "ffmpeg" in response.json()["detail"]
