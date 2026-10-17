"""Application settings, read from the environment (``.env`` in development).

Every field has a default, so the app boots with an empty environment. Secrets
(``DEEPSEEK_API_KEY``, ``XHS_AUTH_TOKEN``) default to empty; without the key the
AI endpoints answer 503 rather than inventing content.

Environment variables are grouped by section::

    APP_*       server title/host/port
    XHS_*       MCP endpoint, timeout, search filters
    DEEPSEEK_*  credentials, model, token budgets
    SHORT_*     short-video duration bounds and limits

The full list with defaults lives in ``.env.example``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# .env supplies values during development; real environment variables win.
load_dotenv(PROJECT_ROOT / ".env")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AppSettings(_Section):
    title: str = "XHS Travel Planner"
    host: str = "127.0.0.1"
    port: int = 8000


class XhsSettings(_Section):
    mcp_url: str = "http://localhost:18060/mcp"
    auth_token: str = ""
    timeout_seconds: int = 120
    sort_by: str = "最新"
    note_type: str = "图文"
    max_notes: int = 20


class TokenBudget(_Section):
    itinerary: int = 4096
    short: int = 3000
    keyword: int = 200


class DeepSeekSettings(_Section):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_seconds: int = 60
    max_tokens: TokenBudget = TokenBudget()


class ShortSettings(_Section):
    min_duration_seconds: int = 15
    max_duration_seconds: int = 90
    default_duration_seconds: int = 30
    max_scenes: int = 10
    max_sources: int = 5

    @model_validator(mode="after")
    def _durations_must_be_ordered(self) -> "ShortSettings":
        if not (
            self.min_duration_seconds
            <= self.default_duration_seconds
            <= self.max_duration_seconds
        ):
            raise ValueError("short durations must satisfy min <= default <= max")
        return self


class Settings(_Section):
    app: AppSettings = AppSettings()
    xhs: XhsSettings = XhsSettings()
    deepseek: DeepSeekSettings = DeepSeekSettings()
    short: ShortSettings = ShortSettings()


def _from_env(model_cls: type[_Section], prefix: str) -> _Section:
    """Build a section from ``<PREFIX><FIELD>`` variables, ignoring empties.

    Nested sections recurse, so ``TokenBudget.itinerary`` reads
    ``DEEPSEEK_MAX_TOKENS_ITINERARY``.
    """
    values: dict[str, object] = {}
    for name, field in model_cls.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            values[name] = _from_env(annotation, f"{prefix}{name.upper()}_")
            continue
        raw = os.getenv(f"{prefix}{name.upper()}")
        if raw not in (None, ""):
            values[name] = raw
    return model_cls(**values)


def load_settings() -> Settings:
    """Read settings from the current environment."""
    try:
        return Settings(
            app=_from_env(AppSettings, "APP_"),
            xhs=_from_env(XhsSettings, "XHS_"),
            deepseek=_from_env(DeepSeekSettings, "DEEPSEEK_"),
            short=_from_env(ShortSettings, "SHORT_"),
        )
    except ValidationError as exc:
        raise RuntimeError(
            f"Invalid settings: check the *_TIMEOUT_SECONDS / *_MAX_TOKENS* / "
            f"SHORT_* values in your environment or .env.\n{exc}"
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return load_settings()


settings = get_settings()
