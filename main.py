"""ASGI entrypoint: ``uvicorn main:app``."""

from __future__ import annotations

import logging

from app.config import settings
from app.routes import app

__all__ = ["app"]

logging.basicConfig(level=logging.INFO)

# The MCP SDK polls an optional server-notification SSE stream that the
# xiaohongshu-mcp container does not implement (it answers 405 and the client
# retries). Calls still work; the reconnect chatter is not actionable.
for _noisy in ("httpx", "httpcore", "mcp.client.streamable_http"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app.host, port=settings.app.port)
