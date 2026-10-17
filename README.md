# XHS Travel Planner

Turns Xiaohongshu (Little Red Book) travel notes into two things:

- **Itineraries** — day-by-day plans split into *Viral Hits* and *Fresh Finds*
- **Short-video scripts** — a shootable Reels/Shorts/TikTok script (hook → timed scenes → caption → hashtags) grounded in the notes it was built from

## Architecture

```
Browser (Tailwind UI) → FastAPI → MCP Client (Streamable HTTP) → xhs-mcp container → Xiaohongshu
                            ↓
                      LLM API (OpenAI-compatible) → Itinerary / Short script
                            ↓
                      JSON Response → Browser
```

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with WSL2 backend on Windows)
- Python 3.11+ (for local development)
- An API key for any OpenAI-compatible LLM — DeepSeek, OpenAI, OpenRouter, Moonshot/Kimi, a local Ollama, etc.
- A Xiaohongshu account — you sign in by scanning a QR code in the app

Without an LLM key, AI endpoints answer `503`. Without a Xiaohongshu session, searches find no notes and the UI offers the QR sign-in.

## Setup

### 1. Clone and configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `DEEPSEEK_API_KEY` | API key for your LLM provider (any OpenAI-compatible one). Required for AI generation |
| `DEEPSEEK_BASE_URL` | Provider endpoint. Default `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | Model name. Default `deepseek-chat` |
| `XHS_MCP_URL` | MCP endpoint. Default `http://localhost:18060/mcp`; Compose overrides it in containers |
| `XHS_AUTH_TOKEN` | Optional. Only if the container was started with `AUTH_TOKEN`; sent as `Authorization: Bearer …` |

Everything else has a working default. The full list — LLM token budgets, MCP timeout/filters/note cap, short-video duration bounds, server title/port — is documented with its default in [`.env.example`](.env.example).

Variables are grouped by prefix: `APP_*`, `XHS_*`, `DEEPSEEK_*`, `SHORT_*`. Empty values are ignored, so leaving a line blank falls back to the default instead of breaking the app.

### Using a different model provider

Nothing here is DeepSeek-specific — it is a standard OpenAI-compatible client (`chat/completions` with `response_format: json_object`), so point the three `DEEPSEEK_*` variables at any provider:

```dotenv
# OpenAI
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.openai.com/v1
DEEPSEEK_MODEL=gpt-4o-mini

# OpenRouter
DEEPSEEK_API_KEY=sk-or-...
DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1
DEEPSEEK_MODEL=deepseek/deepseek-chat

# Local Ollama
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://localhost:11434/v1
DEEPSEEK_MODEL=llama3.1
```

The variable names stay `DEEPSEEK_*` for backwards compatibility; only the endpoint and model change. The provider must support **JSON mode** (`response_format={"type":"json_object"}`) — that is what keeps responses parseable, so a plain-text-only endpoint will fail with `502`.

### 2. Sign in to Xiaohongshu

The `xhs-mcp` container owns the Xiaohongshu session — it stores the cookies after a QR login and handles expiry, so no cookie belongs in `.env`.

1. Start the stack with the commands below
2. Open http://localhost:8000 — the app shows the Xiaohongshu status and, when you are signed out, a **Sign in** prompt
3. Click it, then scan the QR code with the Xiaohongshu app on your phone
4. The modal polls in the background and flips to *signed in* as soon as the scan lands

Re-login works the same way: click the status chip → confirm sign-out → scan a fresh QR code.

### 3. Spin up everything (mise)

One command starts the MCP container and the API:

```bash
mise run up
```

Then open http://localhost:8000. The first run builds the API image, so give it a minute.

| Task | What it does |
|------|--------------|
| `mise run up` | Build and start MCP + API in Docker, UI on `:8000` |
| `mise run dev` | Local dev: install deps → start MCP container → wait for it → API with reload |
| `mise run doctor` | Diagnose connectivity between the API and the MCP container |
| `mise run build` | Build the API image only |
| `mise run mcp` | Start only the `xhs-mcp` container |
| `mise run install` | Create `.venv` and install Python dependencies |
| `mise run test` | Run the test suite (mocked: no Docker, keys, or network) |
| `mise run check` | Print the API's Xiaohongshu sign-in state |
| `mise run logs` | Follow the container logs |
| `mise run down` | Stop and remove the containers |

`up` is the everything-in-Docker path; `dev` is the same stack with the Python API running locally so edits reload instantly. Both need Docker Desktop running for the MCP container.

First time on a fresh machine: `mise install` (pinned Python + uv), then `mise run up` or `mise run dev`.

No mise yet? `winget install jdx.mise` (or https://mise.run).

### 4. Without mise

```bash
uv pip install -r requirements-dev.txt
docker compose up -d --build     # whole stack
```

Local development against a containerised MCP server:

```bash
docker compose up -d xhs-mcp
uvicorn main:app --reload --port 8000
```

### 5. Run tests

```bash
python -m pytest -q
```

The suite is fully mocked: no Docker, no API keys, no network.

## Troubleshooting

Start with `mise run doctor`. It prints the configured URL, the container state, and which candidate URLs actually answer, then tells you the exact `XHS_MCP_URL` to use.

### "Cannot reach the Xiaohongshu MCP server" after `mise run dev`

`dev` starts the container, then waits up to 45s for the endpoint before launching the API. If that wait times out, the container is running but your shell cannot reach the published port:

- **WSL + Docker Desktop**: WSL shell may not see Docker Desktop's localhost forwarding, so `localhost:18060` fails while the container is healthy. `mise run doctor` tests `127.0.0.1`, `host.docker.internal`, and the Windows host gateway (the default route) and prints the one that works.
- `host.docker.internal` only resolves inside containers, not in a WSL shell.
- Gateway IPs change when WSL restarts. For a stable setup, run `mise run up` (both services inside Docker, talking over the compose network) or run `dev` from Windows PowerShell.
- Nothing answers? `docker compose logs --tail 50 xhs-mcp` — first boot downloads a headless browser, so give it a minute.

## How it works

### Signing in

1. The page asks `GET /api/xhs/login`, which calls the container's `check_login_status`
2. Signed out → the UI requests `POST /api/xhs/login/qrcode`, which calls `get_login_qrcode` and returns the PNG as a data URL
3. The QR is displayed and the page polls `GET /api/xhs/login` every 3s until the container reports a session
4. If a search hits an auth error, the API answers `401` and the UI opens the same QR prompt instead of a dead end

### Itinerary mode

1. Enter a **destination** (e.g., "Japan") and **duration** (e.g., 3 days)
2. FastAPI connects to the `xhs-mcp` container and calls `search_feeds`
3. Raw notes go to the LLM for categorization into **Viral Hits** and **Fresh Finds**
4. The frontend renders both columns

### Short-video mode

1. Ask a question the way a viewer would, e.g. *"How to do a day trip to Snow Town?"*
2. The LLM resolves the question into a Xiaohongshu search keyword (`雪乡 一日游 攻略`) — searching the raw sentence would return junk, so this step matters
3. Notes are fetched and normalized (title, author, likes, URL), capped at 20 per request
4. The LLM writes a timed script; scene timings are validated and rebalanced when the model's numbers don't add up
5. Cited note titles are resolved back to **real** note URLs — the model never supplies a link
6. If a detail (price, bus time, opening hours) is missing from the notes, the script marks it `[verify]` and the response downgrades from `grounding: "full"` to `"partial"` with warnings shown in the UI

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Tailwind UI |
| `POST` | `/generate-itinerary` | `{"destination": str, "days": 1-30}` → `{viral_hits[], fresh_finds[]}` |
| `POST` | `/generate-short` | `{"query": str, "duration_seconds": 15-90, "platform": "shorts"\|"reels"\|"tiktok"\|"xhs", "language"?: "en"\|"zh"}` → script |
| `GET` | `/api/xhs/login` | Xiaohongshu session status for the MCP container |
| `POST` | `/api/xhs/login/qrcode` | Login QR code as a `data:image/png;base64,…` URL |
| `POST` | `/api/xhs/logout` | Clears the container's session (`delete_cookies`) |

```bash
curl -s localhost:8000/generate-short \
  -H 'content-type: application/json' \
  -d '{"query":"How to do a day trip to Snow Town?","duration_seconds":30}' | jq
```

Response shape:

```jsonc
{
  "query": "How to do a day trip to Snow Town?",
  "search_keyword": "雪乡 一日游 攻略",
  "platform": "shorts",
  "duration_seconds": 30,
  "language": "en",
  "title": "Snow Town in One Day — Without the Tour Bus",
  "hook": "Most people think Snow Town needs three days. It doesn't.",
  "scenes": [
    {
      "index": 1,
      "time_range": "0:00–0:04",
      "on_screen_text": "SNOW TOWN IN 1 DAY",
      "voiceover": "...",
      "visual": "..."
    }
  ],
  "caption": "...",
  "hashtags": ["#snowtown", "#雪乡"],
  "sources": [{"title": "...", "author": "...", "url": "https://www.xiaohongshu.com/explore/...", "likes": 12000}],
  "grounding": "full",
  "warnings": []
}
```

Error behaviour: `401` not signed in to Xiaohongshu (the UI opens the QR prompt), `422` invalid input, `404` no notes found for the resolved keyword, `502` MCP/LLM failure or unreachable container, `503` missing `DEEPSEEK_API_KEY`, `504` search timeout.

## Project Structure

```
.
├── app/                     # application package
│   ├── config.py            # settings read from the environment (.env)
│   ├── models.py            # request/response models
│   ├── prompts.py           # prompt text and builders (dependency-free)
│   ├── mcp.py               # Streamable HTTP transport to xhs-mcp
│   ├── notes.py             # note normalization, search, source attribution
│   ├── login.py             # sign-in relay (status, QR, logout)
│   ├── llm.py               # OpenAI-compatible JSON client with one repair retry
│   ├── itinerary.py         # itinerary generation
│   ├── short.py             # keyword resolution, timing repair, grounding
│   └── routes.py            # FastAPI app and endpoints
├── main.py                  # ASGI entrypoint: `uvicorn main:app`
├── static/index.html        # Tailwind UI: tabs, QR sign-in, script renderer
├── tests/                   # pytest suite (no network, no Docker)
├── requirements.txt         # runtime dependencies
├── requirements-dev.txt     # + test dependencies
├── docker-compose.yml       # API service + xhs-mcp
├── Dockerfile
├── .mise.toml               # tool versions + tasks
└── pytest.ini               # test paths + import root
```

## Tech Stack

- **Backend**: FastAPI (Python 3.11+)
- **Frontend**: Tailwind CSS (CDN) + Vanilla JS
- **MCP Integration**: Python `mcp` client via Streamable HTTP transport
- **AI**: any OpenAI-compatible API — defaults to DeepSeek `deepseek-chat`
- **Data Source**: Xiaohongshu via `xpzouying/xiaohongshu-mcp` Docker image
