"""ASGI entrypoint: ``uvicorn main:app``."""

from __future__ import annotations

import logging

from app.config import settings
from app.routes import app

__all__ = ["app"]

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app.host, port=settings.app.port)
