"""Test suite for the XHS Travel Planner API.

These tests follow TDD principles:
- Phase 1 (Red): Tests written before main.py exists — they will fail
- Phase 2 (Green): Minimal main.py with mocks — tests pass
- Phase 3 (Refactor): Real integration with proper unit test mocks — tests still pass
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from main import app

# ---------------------------------------------------------------------------
# Mock data used across test cases
# ---------------------------------------------------------------------------

MOCK_DEEPSEEK_RESPONSE = {
    "viral_hits": [
        {
            "name": "Senso-ji Temple",
            "description": "Tokyo's oldest temple, a must-visit iconic landmark.",
            "category": "sightseeing",
            "estimated_hours": 2.0,
        },
    ],
    "fresh_finds": [
        {
            "name": "Yanaka Ginza",
            "description": "A nostalgic shopping street preserving Showa-era charm.",
            "category": "shopping",
            "estimated_hours": 1.5,
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create an async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock MCP and DeepSeek calls for all tests.

    This ensures tests are fast, deterministic, and don't require
    Docker or API keys.
    """
    with patch("main.fetch_xhs_notes", new_callable=AsyncMock) as mock_fetch, \
         patch("main.generate_itinerary", new_callable=AsyncMock) as mock_gen:

        mock_fetch.return_value = [
            {"text": "Great food at Tsukiji Market! #Tokyo #food"}
        ]
        mock_gen.return_value = MOCK_DEEPSEEK_RESPONSE

        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_itinerary_structure(client):
    """POST /generate-itinerary with valid data returns 200 + expected JSON structure."""
    payload = {"destination": "Japan", "days": 3}
    response = await client.post("/generate-itinerary", json=payload)

    assert response.status_code == 200
    data = response.json()

    # Check top-level keys
    assert data["destination"] == "Japan"
    assert data["days"] == 3
    assert "viral_hits" in data
    assert "fresh_finds" in data

    # Check that both categories are lists of activities
    for category in ["viral_hits", "fresh_finds"]:
        assert isinstance(data[category], list)
        if data[category]:
            activity = data[category][0]
            assert "name" in activity
            assert "description" in activity
            assert "category" in activity
            assert "estimated_hours" in activity


@pytest.mark.asyncio
async def test_generate_itinerary_invalid_days(client):
    """POST /generate-itinerary with days=0 or days=31 returns 422 validation error."""
    # Test days = 0 (below minimum)
    response = await client.post(
        "/generate-itinerary", json={"destination": "Japan", "days": 0}
    )
    assert response.status_code == 422

    # Test days = 31 (above maximum)
    response = await client.post(
        "/generate-itinerary", json={"destination": "Japan", "days": 31}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_itinerary_missing_destination(client):
    """POST /generate-itinerary without destination returns 422 validation error."""
    response = await client.post(
        "/generate-itinerary", json={"days": 3}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_root_serves_html(client):
    """GET / returns 200 with content-type text/html."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_generate_itinerary_calls_external_services(client):
    """Verify that the endpoint actually calls MCP fetcher and DeepSeek generator."""
    payload = {"destination": "Paris", "days": 2}
    response = await client.post("/generate-itinerary", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "Paris"
    assert data["days"] == 2
    assert len(data["viral_hits"]) == 1
    assert len(data["fresh_finds"]) == 1
