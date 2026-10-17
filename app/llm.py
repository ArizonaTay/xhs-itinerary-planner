"""LLM access over any OpenAI-compatible endpoint, in JSON mode.

Defaults to DeepSeek but the endpoint and model are configuration
(``DEEPSEEK_BASE_URL`` / ``DEEPSEEK_MODEL``). Callers get a dict or an
``HTTPException``; there is no silent fallback to fake content.
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger("xhs-planner.llm")

_REPAIR_PROMPT = (
    "Your previous reply was not valid JSON. Reply again with ONLY the JSON "
    "object described above, no prose and no code fences."
)


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.deepseek.api_key,
        base_url=settings.deepseek.base_url,
        timeout=settings.deepseek.timeout_seconds,
    )


async def complete_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int | None = None,
) -> dict:
    """Run a JSON-mode completion, retrying once if the reply is not JSON."""
    if not settings.deepseek.api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "DEEPSEEK_API_KEY is not set. Add your provider key to .env "
                "(any OpenAI-compatible API works) to generate content."
            ),
        )

    client = _client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw = ""

    for attempt in (1, 2):
        try:
            response = await client.chat.completions.create(
                model=settings.deepseek.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.7 if attempt == 1 else 0.2,
                max_tokens=max_tokens or settings.deepseek.max_tokens.itinerary,
            )
            raw = response.choices[0].message.content or ""
            if not raw:
                raise json.JSONDecodeError("empty response", "", 0)
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "LLM returned invalid JSON (attempt %d): %s", attempt, exc
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": _REPAIR_PROMPT},
            ]
        except HTTPException:
            raise
        except Exception as exc:  # network, auth, rate limit
            logger.error("LLM call failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=f"LLM API call failed: {exc}",
            ) from exc

    raise HTTPException(
        status_code=502,
        detail="The LLM did not return valid JSON after retrying",
    )
