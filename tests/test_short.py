"""Short-video endpoint behaviour, timing repair, and grounding rules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.notes import normalize_notes

# Mirrors the real xiaohongshu-mcp payload: the id and xsecToken sit on the
# wrapper, while the human-readable fields live inside noteCard.
NOTES_PAYLOAD = {
    "feeds": [
        {
            "id": "abc123",
            "xsecToken": "token-abc",
            "modelType": "note",
            "noteCard": {
                "type": "normal",
                "displayTitle": "雪乡一日游攻略｜哈尔滨出发当天往返",
                "user": {"nickname": "旅行的意义"},
                "interactInfo": {"likedCount": "1.2万"},
            },
        },
        {
            "id": "def456",
            "xsecToken": "token-def",
            "modelType": "note",
            "noteCard": {
                "type": "video",
                "displayTitle": "雪乡避坑指南｜门票和交通",
                "user": {"nickname": "东北小张"},
                "interactInfo": {"likedCount": 998},
            },
        },
    ],
    "count": 2,
}

SCRIPT_PAYLOAD = {
    "title": "Snow Town in One Day",
    "hook": "Most people think Snow Town needs three days. It doesn't.",
    "scenes": [
        {
            "index": 1,
            "time_range": "0:00-0:04",
            "on_screen_text": "SNOW TOWN IN 1 DAY",
            "voiceover": "Here's the day-trip version.",
            "visual": "Walk toward the village gate",
        },
        {
            "index": 2,
            "time_range": "0:04-0:10",
            "on_screen_text": "LEAVE AT DAWN",
            "voiceover": "The drive from Harbin takes about five hours each way.",
            "visual": "Bus window",
        },
        {
            "index": 3,
            "time_range": "0:10-0:18",
            "on_screen_text": "WALK THE VILLAGE",
            "voiceover": "Snow-covered roofs and wooden fences.",
            "visual": "Slow pan across rooftops",
        },
        {
            "index": 4,
            "time_range": "0:18-0:24",
            "on_screen_text": "EAT BEFORE YOU GO",
            "voiceover": "Grab a hot meal before the ride back.",
            "visual": "Steaming bowl",
        },
        {
            "index": 5,
            "time_range": "0:24-0:30",
            "on_screen_text": "CHECK THE RETURN BUS",
            "voiceover": "Confirm the last return time. Save this for winter.",
            "visual": "Timetable close-up",
        },
    ],
    "caption": "One day, not three.",
    "hashtags": ["#snowtown", "雪乡", "#harbin", "#daytrip"],
    "sources": [
        {"title": "雪乡一日游攻略｜哈尔滨出发当天往返"},
        {"title": "雪乡避坑指南｜门票和交通"},
    ],
    "warnings": [],
}


def _seconds(value: str) -> tuple[int, int]:
    start, end = value.replace("–", "-").split("-")

    def parse(clock: str) -> int:
        minutes, seconds = clock.split(":")
        return int(minutes) * 60 + int(seconds)

    return parse(start), parse(end)


@pytest.fixture
def short_services():
    with patch("app.routes.extract_search_plan", new_callable=AsyncMock) as plan, \
         patch("app.routes.search_notes", new_callable=AsyncMock) as search, \
         patch("app.short.complete_json", new_callable=AsyncMock) as complete:
        plan.return_value = {"search_keyword": "雪乡 一日游 攻略", "language": "en"}
        search.return_value = [NOTES_PAYLOAD]
        complete.return_value = SCRIPT_PAYLOAD
        yield SimpleNamespace(plan=plan, search=search, complete=complete)


@pytest.mark.asyncio
async def test_returns_grounded_script(client, short_services):
    response = await client.post(
        "/generate-short",
        json={"query": "How to do a day trip to Snow Town?", "duration_seconds": 30},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["search_keyword"] == "雪乡 一日游 攻略"
    assert len(data["scenes"]) == 5
    assert [scene["index"] for scene in data["scenes"]] == [1, 2, 3, 4, 5]
    assert _seconds(data["scenes"][0]["time_range"])[0] == 0
    assert _seconds(data["scenes"][-1]["time_range"])[1] == 30

    assert data["grounding"] == "full"
    assert len(data["sources"]) == 2
    for source in data["sources"]:
        assert source["url"].startswith("https://www.xiaohongshu.com/explore/")
    assert all(tag.startswith("#") for tag in data["hashtags"])


@pytest.mark.asyncio
async def test_searches_with_resolved_keyword(client, short_services):
    await client.post(
        "/generate-short", json={"query": "How to do a day trip to Snow Town?"}
    )

    short_services.search.assert_awaited_once()
    assert short_services.search.await_args.args[0] == "雪乡 一日游 攻略"


@pytest.mark.asyncio
async def test_rejects_invalid_requests(client):
    assert (await client.post("/generate-short", json={})).status_code == 422
    for duration in (5, 200):
        response = await client.post(
            "/generate-short",
            json={"query": "Snow Town", "duration_seconds": duration},
        )
        assert response.status_code == 422
    response = await client.post(
        "/generate-short", json={"query": "Snow Town", "platform": "myspace"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_without_notes_returns_404(client, short_services):
    with patch("app.routes.search_notes", new_callable=AsyncMock) as empty:
        empty.return_value = []
        response = await client.post(
            "/generate-short", json={"query": "How to do a day trip to Snow Town?"}
        )

    assert response.status_code == 404
    assert "雪乡 一日游 攻略" in response.json()["detail"]


@pytest.mark.asyncio
async def test_script_without_scenes_is_502(client, short_services):
    short_services.complete.return_value = {**SCRIPT_PAYLOAD, "scenes": []}

    response = await client.post(
        "/generate-short", json={"query": "How to do a day trip to Snow Town?"}
    )

    assert response.status_code == 502
    assert "no usable scenes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_rebalances_model_timings(client, short_services):
    short_services.complete.return_value = {
        **SCRIPT_PAYLOAD,
        "scenes": [
            {
                "index": 1,
                "time_range": "0:00-0:02",
                "on_screen_text": "ONE",
                "voiceover": "First beat.",
                "visual": "Gate",
            },
            {
                "index": 2,
                "time_range": "0:02-0:03",
                "on_screen_text": "TWO",
                "voiceover": "Second beat.",
                "visual": "Bus",
            },
        ],
    }

    response = await client.post(
        "/generate-short",
        json={"query": "How to do a day trip to Snow Town?", "duration_seconds": 30},
    )

    scenes = response.json()["scenes"]
    assert _seconds(scenes[0]["time_range"])[0] == 0
    assert _seconds(scenes[-1]["time_range"])[1] == 30


@pytest.mark.asyncio
async def test_verify_markers_downgrade_grounding(client, short_services):
    flagged = {
        **SCRIPT_PAYLOAD,
        "scenes": [
            {
                **SCRIPT_PAYLOAD["scenes"][0],
                "voiceover": "The bus leaves at 6am [verify].",
            },
            *SCRIPT_PAYLOAD["scenes"][1:],
        ],
        "warnings": ["Departure time is not stated in the source notes."],
    }
    short_services.complete.return_value = flagged

    response = await client.post(
        "/generate-short", json={"query": "How to do a day trip to Snow Town?"}
    )

    data = response.json()
    assert data["grounding"] == "partial"
    assert data["warnings"]


@pytest.mark.asyncio
async def test_uncited_script_uses_top_notes(client, short_services):
    short_services.complete.return_value = {**SCRIPT_PAYLOAD, "sources": []}

    response = await client.post(
        "/generate-short", json={"query": "How to do a day trip to Snow Town?"}
    )

    data = response.json()
    assert len(data["sources"]) == 2
    assert data["grounding"] == "partial"
    assert any("did not cite" in warning for warning in data["warnings"])


def test_normalize_notes_handles_mcp_payload_shapes():
    notes = normalize_notes([NOTES_PAYLOAD, {"text": "unstructured noise"}])

    assert len(notes) == 2
    by_title = {note["title"]: note for note in notes}
    day_trip = by_title["雪乡一日游攻略｜哈尔滨出发当天往返"]
    assert day_trip["url"] == "https://www.xiaohongshu.com/explore/abc123"
    assert day_trip["author"] == "旅行的意义"
    assert day_trip["likes"] == 12000
    assert day_trip["xsec_token"] == "token-abc"
    assert by_title["雪乡避坑指南｜门票和交通"]["likes"] == 998
    assert normalize_notes([{"unexpected": {"shape": True}}]) == []
