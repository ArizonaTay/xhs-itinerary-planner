"""Itinerary endpoint behaviour."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ITINERARY = {
    "viral_hits": [
        {
            "name": "Senso-ji Temple",
            "description": "Tokyo's oldest temple.",
            "category": "sightseeing",
            "estimated_hours": 2.0,
        }
    ],
    "fresh_finds": [
        {
            "name": "Yanaka Ginza",
            "description": "A nostalgic shopping street.",
            "category": "shopping",
            "estimated_hours": 1.5,
        }
    ],
}


@pytest.fixture
def services():
    with patch("app.routes.search_notes", new_callable=AsyncMock) as search, \
         patch("app.routes.generate_itinerary", new_callable=AsyncMock) as generate:
        search.return_value = [{"text": "Great food at Tsukiji Market"}]
        generate.return_value = ITINERARY
        yield SimpleNamespace(search=search, generate=generate)


@pytest.mark.asyncio
async def test_returns_categorized_itinerary(client, services):
    response = await client.post(
        "/generate-itinerary", json={"destination": "Japan", "days": 3}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "Japan"
    assert data["days"] == 3
    assert len(data["viral_hits"]) == 1
    assert len(data["fresh_finds"]) == 1
    assert set(data["viral_hits"][0]) == {
        "name",
        "description",
        "category",
        "estimated_hours",
    }
    services.search.assert_awaited_once_with("Japan")
    assert services.generate.await_args.kwargs["days"] == 3


@pytest.mark.asyncio
async def test_rejects_out_of_range_days(client):
    for days in (0, 31):
        response = await client.post(
            "/generate-itinerary", json={"destination": "Japan", "days": days}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_rejects_missing_destination(client):
    response = await client.post("/generate-itinerary", json={"days": 3})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_serves_frontend(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
