"""Thin wrapper around Anthropic's API with an offline-mock fallback.

Why a wrapper instead of using the SDK directly in nodes?
- Lets the entire pipeline run with no API key (CI, demos, take-home review).
- Centralises model name + system prompt conventions.
- Returns plain strings or parsed JSON so node code stays declarative.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _has_api_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` fences if the model wrapped its JSON output."""
    fence = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fence.group(1) if fence else text


def call_claude(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    json_mode: bool = False,
    mock: Optional[dict[str, Any] | str] = None,
) -> Any:
    """Call Claude. If no API key is set, return the supplied mock instead.

    Args:
        system: system prompt.
        user: user message.
        max_tokens: cap on output length.
        json_mode: if True, parse the response as JSON before returning.
        mock: object/string returned verbatim when running offline.
    """
    if not _has_api_key():
        if mock is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set and this call has no offline mock."
            )
        return mock

    # Lazy import so the package isn't required for offline mode.
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")

    if json_mode:
        try:
            return json.loads(_strip_code_fence(text))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM did not return valid JSON: {e}\n---\n{text}") from e
    return text
