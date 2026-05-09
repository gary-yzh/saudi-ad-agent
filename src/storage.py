"""SQLite-backed persistence for model / API configuration.

Single-table key-value store. The frontend Settings page reads / writes via
GET / POST /api/config. The Run page never sees the values — when /api/run
fires, the FastAPI handler loads from this store into the per-request
contextvar (`runtime.set_request_config`) that the LangGraph nodes read.

DB lives at `data/app.db`. The schema is created on first connect, so a
brand-new install just works.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "app.db"

# Allowlist — keys outside this set are silently dropped on save. These names
# match the kwarg names accepted by `runtime.set_request_config`, so loading
# the config and splatting it into that function "just works".
ALLOWED_KEYS: set[str] = {
    # LLM (OpenAI-compatible)
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    # Volcengine Ark — image + video
    "ark_api_key",
    "ark_base_url",
    "image_model",
    "image_size",
    "image_watermark",
    "video_model",
    "video_ratio",
    "video_duration",
    "video_generate_audio",
    "video_watermark",
    # ByteDance OpenSpeech — TTS
    "tts_api_key",
    "tts_url",
    "tts_resource_id",
    "tts_speaker",
    "tts_format",
    "tts_sample_rate",
    "tts_speech_rate",
    "tts_loudness_rate",
    "tts_emotion",
    "tts_emotion_scale",
    "tts_silence_duration",
    "tts_explicit_language",
}

REQUIRED_KEYS: tuple[str, ...] = ("openai_api_key", "ark_api_key", "tts_api_key")

SECRET_KEYS: tuple[str, ...] = ("openai_api_key", "ark_api_key", "tts_api_key")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return conn


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def load_config() -> dict[str, Any]:
    """Return the saved config as a dict. Booleans/numbers come back typed."""
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    out: dict[str, Any] = {}
    for k, v in rows:
        try:
            out[k] = json.loads(v)
        except (TypeError, json.JSONDecodeError):
            out[k] = v
    return out


def save_config(updates: dict[str, Any], *, replace_missing: bool = True) -> None:
    """Persist a settings dict.

    - Keys outside ALLOWED_KEYS are silently ignored (defense in depth).
    - Empty values delete the row (so the env-var fallback can take over).
    - When `replace_missing` is True (the default — settings page always
      submits the whole form), any saved key not present in `updates` is
      also deleted.
    """
    filtered = {k: v for k, v in updates.items() if k in ALLOWED_KEYS}
    with _connect() as conn:
        if replace_missing:
            existing = {r[0] for r in conn.execute("SELECT key FROM config").fetchall()}
            for k in existing - filtered.keys():
                conn.execute("DELETE FROM config WHERE key = ?", (k,))
        for k, v in filtered.items():
            if _is_empty(v):
                conn.execute("DELETE FROM config WHERE key = ?", (k,))
            else:
                conn.execute(
                    """
                    INSERT INTO config (key, value, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT (key) DO UPDATE SET
                      value = excluded.value,
                      updated_at = excluded.updated_at
                    """,
                    (k, json.dumps(v, ensure_ascii=False)),
                )
        conn.commit()


def has_required_keys() -> bool:
    cfg = load_config()
    return all(not _is_empty(cfg.get(k)) for k in REQUIRED_KEYS)


def status() -> dict[str, Any]:
    """Lightweight status payload for the Run page nav badge."""
    cfg = load_config()
    return {
        "configured": all(not _is_empty(cfg.get(k)) for k in REQUIRED_KEYS),
        "missing": [k for k in REQUIRED_KEYS if _is_empty(cfg.get(k))],
        "set_keys": sorted(k for k in cfg if not _is_empty(cfg.get(k))),
    }
