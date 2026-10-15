# XHS Travel Planner

Automates travel itinerary extraction from Xiaohongshu into structured, categorized plans using AI.

## Architecture

```
Browser (Tailwind UI) → FastAPI → MCP Client (Streamable HTTP) → xhs-mcp container → Xiaohongshu API
                          ↓
                    DeepSeek API → Categorized Itinerary
                          ↓
                    JSON Response → Browser
```

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with WSL2 backend on Windows)
- Python 3.11+ (for local development)
- A [DeepSeek API key](https://platform.deepseek.com/api_keys)
- A Xiaohongshu cookie (see below)

## Setup

### 1. Clone and configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `DEEPSEEK_API_KEY` | Your DeepSeek API key |
| `XHS_COOKIE` | Full cookie string from xiaohongshu.com |

### 2. Get your XHS_COOKIE

1. Log into [xiaohongshu.com](https://www.xiaohongshu.com) in Chrome/Edge
2. Open DevTools → Application → Cookies → xiaohongshu.com
3. Copy the entire cookie string and paste it as `XHS_COOKIE` in `.env`

### 3. Run with Docker (recommended)

```bash
docker compose up --build
```

Open http://localhost:8000 in your browser.

### 4. Run locally (development)

```bash
uv pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 5. Run tests

```bash
pytest -v
```

## How it works

1. Enter a **destination** (e.g., "Japan") and **duration** (e.g., 3 days) in the UI
2. FastAPI connects to the `xhs-mcp` container via Streamable HTTP and calls `search_feeds` to fetch notes
3. Raw Xiaohongshu data is sent to DeepSeek for structured itinerary generation
4. Results are returned as two categories:
   - **Viral Hits** — Popular/recommended spots
   - **Fresh Finds** — Hidden gems and lesser-known places
5. The frontend renders both categories side by side

## Project Structure

```
c:/xhs-itinerary-planner/
├── .env                     # API keys (gitignored)
├── .env.example             # Template for env vars
├── .gitignore
├── .mise.toml               # Tool versions
├── docker-compose.yml       # API service + xhs-mcp
├── Dockerfile               # Python 3.11
├── main.py                  # FastAPI + MCP client + DeepSeek
├── requirements.txt         # Python dependencies
├── static/
│   └── index.html           # Tailwind UI
├── tests/
│   └── test_main.py         # Test suite
└── plans/
    └── implementation-plan.md
```

## Tech Stack

- **Backend**: FastAPI (Python 3.11)
- **Frontend**: Tailwind CSS (CDN) + Vanilla JS
- **MCP Integration**: Python `mcp` client via Streamable HTTP transport
- **AI**: DeepSeek API (`deepseek-chat` model)
- **Data Source**: Xiaohongshu via `xpzouying/xiaohongshu-mcp` Docker image
