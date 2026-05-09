"""Thin wrapper around an OpenAI-compatible LLM API.

No mock fallback. The Planner / Guardrail-judge / Eval-judge nodes call
`call_claude(...)` which routes to the configured provider:

1. OpenAI-compatible (preferred): per-request via `runtime.set_request_config`
   or `OPENAI_API_KEY` env. `openai_base_url` lets you point at any compatible
   endpoint (Volcengine Ark Doubao, OpenAI, Azure, DeepSeek, etc.).
2. Anthropic: `ANTHROPIC_API_KEY` env. Kept for backwards-compat.

If neither is configured, the call raises immediately rather than returning
a fake response.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .runtime import cfg_get

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _openai_key() -> str:
    return (cfg_get("openai_api_key", env_var="OPENAI_API_KEY", default="") or "").strip()


def _openai_base_url() -> Optional[str]:
    val = cfg_get("openai_base_url", env_var="OPENAI_BASE_URL", default="")
    if isinstance(val, str):
        v = val.strip()
        return v or None
    return val


def _openai_model() -> str:
    return cfg_get("openai_model", env_var="OPENAI_MODEL", default=OPENAI_MODEL_DEFAULT)


def _anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def active_provider() -> str:
    if _openai_key():
        return "openai"
    if _anthropic_key():
        return "anthropic"
    return "none"


def _strip_code_fence(text: str) -> str:
    fence = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fence.group(1) if fence else text


def _parse_json(text: str) -> Any:
    try:
        return json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}\n---\n{text}") from e


def _call_openai(
    system: str, user: str, *, max_tokens: int, json_mode: bool, api_key: str
) -> Any:
    import time

    from openai import APIConnectionError, APITimeoutError, OpenAI

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": 120.0,
        "max_retries": 0,  # we handle retry ourselves
    }
    base_url = _openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url

    kwargs: dict[str, Any] = {
        "model": _openai_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            client = OpenAI(**client_kwargs)
            resp = client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            return _parse_json(text) if json_mode else text
        except (APIConnectionError, APITimeoutError) as e:
            last_exc = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"LLM call failed after 3 attempts: {last_exc}") from last_exc


def _call_anthropic(system: str, user: str, *, max_tokens: int, json_mode: bool) -> Any:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    return _parse_json(text) if json_mode else text


def call_claude(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    json_mode: bool = False,
    **_ignored: Any,  # accept legacy `mock=` kwarg silently
) -> Any:
    """Run an LLM call against whichever provider is configured.

    Raises if no key is set — no mock fallback.
    """
    okey = _openai_key()
    if okey:
        return _call_openai(
            system, user, max_tokens=max_tokens, json_mode=json_mode, api_key=okey
        )
    if _anthropic_key():
        return _call_anthropic(
            system, user, max_tokens=max_tokens, json_mode=json_mode
        )
    raise RuntimeError(
        "No LLM key configured. Provide an OpenAI-compatible API key in the UI "
        "form, or set OPENAI_API_KEY / ANTHROPIC_API_KEY in the environment."
    )
