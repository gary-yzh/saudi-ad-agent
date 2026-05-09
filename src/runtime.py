"""Per-request runtime config carried through the LangGraph pipeline.

Nodes are pure(ish) but need to know which provider/key to use for LLM and
tool calls. Threading config through every node would be noisy, so we stash
it in a contextvar that the request handler sets at the start and resets at
the end. Works with sync `graph.invoke()` and is safe across concurrent
requests.

Used by `src/llm.py` (OpenAI-compatible LLM config) and
`src/tools/dashscope_apis.py` (Aliyun DashScope keys + region).
"""
from __future__ import annotations

import contextvars
from typing import Any, Optional

_var: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "request_config", default=None
)


def set_request_config(**fields: Any) -> contextvars.Token:
    """Set the per-request config dict.

    Empty / None values are dropped so callers don't have to pre-clean.
    """
    cfg: dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        cfg[k] = v
    return _var.set(cfg or None)


def reset_request_config(token: contextvars.Token) -> None:
    _var.reset(token)


def get_config() -> dict:
    return _var.get() or {}


def cfg_get(key: str, env_var: Optional[str] = None, default: Any = None) -> Any:
    """Per-request value, falling back to env var, then default."""
    cfg = get_config()
    if key in cfg and cfg[key]:
        return cfg[key]
    if env_var:
        import os
        v = os.getenv(env_var, "").strip()
        if v:
            return v
    return default
