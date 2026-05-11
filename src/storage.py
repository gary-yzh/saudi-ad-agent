"""SQLite-backed persistence.

Two concerns share one DB file (`data/app.db`):

1. **Settings** — long-lived model / API config (`config` table). Read via
   `load_config()`, written by `POST /api/config`.

2. **Sessions** — the new multi-step ad-creation flow:
   - `sessions`     — one row per user session, holds run-level state.
   - `messages`     — chat history (user ↔ assistant).
   - `shot_images`  — one row per storyboard shot's generated image.
   - `videos`       — one row per video gen request.

Sessions are read / written by `src/sessions.py`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "app.db"


# ---------------------------------------------------------------------------
# Settings — config key/value table
# ---------------------------------------------------------------------------

ALLOWED_KEYS: set[str] = {
    "openai_api_key", "openai_base_url", "openai_model",
    "ark_api_key", "ark_base_url",
    "image_model", "image_size", "image_watermark",
    "video_model", "video_ratio", "video_generate_audio", "video_watermark",
    "tts_api_key", "tts_url", "tts_resource_id", "tts_speaker", "tts_format",
    "tts_sample_rate", "tts_speech_rate", "tts_loudness_rate",
    "tts_silence_duration", "tts_explicit_language",
}
# Removed in 2026-05-11 product polish — these were per-brief / per-content
# decisions miscategorised as global Settings, see README §9:
#   video_duration  → drives shot total length, now flows from storyboard
#   tts_emotion / tts_emotion_scale → per-brief tone, fixed at "neutral"

REQUIRED_KEYS: tuple[str, ...] = ("openai_api_key", "ark_api_key", "tts_api_key")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            locale TEXT,
            target_audience TEXT,
            state TEXT NOT NULL DEFAULT 'chat',
            storyboard_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shot_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            shot_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            url TEXT,
            error TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id, shot_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            selected_shot_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            remote_url TEXT,
            local_url TEXT,
            error TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brand_manuals (
            session_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            pages INTEGER NOT NULL,
            bytes INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brand_logos (
            session_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            width INTEGER,
            height INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (TypeError, json.JSONDecodeError):
            out[r["key"]] = r["value"]
    return out


def save_config(updates: dict[str, Any], *, replace_missing: bool = True) -> None:
    filtered = {k: v for k, v in updates.items() if k in ALLOWED_KEYS}
    with _connect() as conn:
        if replace_missing:
            existing = {r["key"] for r in conn.execute("SELECT key FROM config").fetchall()}
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
                      value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (k, json.dumps(v, ensure_ascii=False)),
                )
        conn.commit()


def has_required_keys() -> bool:
    cfg = load_config()
    return all(not _is_empty(cfg.get(k)) for k in REQUIRED_KEYS)


def status() -> dict[str, Any]:
    cfg = load_config()
    return {
        "configured": all(not _is_empty(cfg.get(k)) for k in REQUIRED_KEYS),
        "missing": [k for k in REQUIRED_KEYS if _is_empty(cfg.get(k))],
        "set_keys": sorted(k for k in cfg if not _is_empty(cfg.get(k))),
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

# Session lifecycle states (string enum):
#   chat            — clarifying conversation in progress
#   storyboard_draft — assistant proposed a storyboard, awaiting confirm
#   images_running   — image gen kicked off
#   images_done      — all shots have a status (succeeded or failed)
#   video_running    — video gen kicked off
#   video_done       — local video file ready


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def create_session(*, session_id: str, locale: str, target_audience: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, locale, target_audience, state) VALUES (?, ?, ?, 'chat')",
            (session_id, locale, target_audience),
        )
        conn.commit()


def get_session(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    out = _row_to_dict(row)
    if out and out.get("storyboard_json"):
        try:
            out["storyboard"] = json.loads(out["storyboard_json"])
        except json.JSONDecodeError:
            out["storyboard"] = None
    elif out:
        out["storyboard"] = None
    return out


def update_session_state(session_id: str, state: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET state = ?, updated_at = datetime('now') WHERE id = ?",
            (state, session_id),
        )
        conn.commit()


def update_session_storyboard(session_id: str, storyboard: dict | None) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET storyboard_json = ?, state = 'storyboard_draft', updated_at = datetime('now')
            WHERE id = ?
            """,
            (json.dumps(storyboard, ensure_ascii=False) if storyboard else None, session_id),
        )
        conn.commit()


def add_message(*, session_id: str, role: str, content: str, payload: dict | None = None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (session_id, role, content, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, json.dumps(payload, ensure_ascii=False) if payload else None),
        )
        conn.commit()
        return cur.lastrowid


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("payload_json"):
            try:
                d["payload"] = json.loads(d["payload_json"])
            except json.JSONDecodeError:
                d["payload"] = None
        else:
            d["payload"] = None
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Shot images
# ---------------------------------------------------------------------------


def queue_shot_image(session_id: str, shot_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO shot_images (session_id, shot_id, status)
            VALUES (?, ?, 'queued')
            ON CONFLICT (session_id, shot_id) DO UPDATE SET
              status = 'queued', url = NULL, error = NULL, updated_at = datetime('now')
            """,
            (session_id, shot_id),
        )
        conn.commit()


def update_shot_image(
    session_id: str,
    shot_id: int,
    *,
    status: str,
    url: str | None = None,
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE shot_images
            SET status = ?, url = ?, error = ?, metadata_json = ?, updated_at = datetime('now')
            WHERE session_id = ? AND shot_id = ?
            """,
            (
                status,
                url,
                error,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
                session_id,
                shot_id,
            ),
        )
        conn.commit()


def list_shot_images(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM shot_images WHERE session_id = ? ORDER BY shot_id ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


def upsert_video(
    *,
    session_id: str,
    selected_shot_ids: list[int],
    status: str,
    remote_url: str | None = None,
    local_url: str | None = None,
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO videos (session_id, selected_shot_ids_json, status, remote_url, local_url, error, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_id) DO UPDATE SET
              selected_shot_ids_json = excluded.selected_shot_ids_json,
              status = excluded.status,
              remote_url = COALESCE(excluded.remote_url, videos.remote_url),
              local_url = COALESCE(excluded.local_url, videos.local_url),
              error = excluded.error,
              metadata_json = COALESCE(excluded.metadata_json, videos.metadata_json),
              updated_at = datetime('now')
            """,
            (
                session_id,
                json.dumps(selected_shot_ids),
                status,
                remote_url,
                local_url,
                error,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        conn.commit()


def get_video(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["selected_shot_ids"] = json.loads(d.get("selected_shot_ids_json") or "[]")
    except json.JSONDecodeError:
        d["selected_shot_ids"] = []
    return d


# ---------------------------------------------------------------------------
# Brand manuals (per-session uploaded PDFs, used as a RAG source)
# ---------------------------------------------------------------------------


def save_brand_manual(
    *,
    session_id: str,
    filename: str,
    pages: int,
    byte_size: int,
    text: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO brand_manuals (session_id, filename, pages, bytes, text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (session_id) DO UPDATE SET
              filename = excluded.filename,
              pages = excluded.pages,
              bytes = excluded.bytes,
              text = excluded.text,
              created_at = datetime('now')
            """,
            (session_id, filename, pages, byte_size, text),
        )
        conn.commit()


def get_brand_manual(session_id: str) -> dict[str, Any] | None:
    """Return manual record (without text) for status display."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT session_id, filename, pages, bytes, created_at
            FROM brand_manuals WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def get_brand_manual_text(session_id: str) -> str | None:
    """Return the extracted text body for chat-time consumption."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT text FROM brand_manuals WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["text"] if row else None


def delete_brand_manual(session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM brand_manuals WHERE session_id = ?", (session_id,)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Brand logos (per-session uploaded image, composited onto stills + hinted to
# the planner so it leaves room in the bottom-right of every shot)
# ---------------------------------------------------------------------------


def save_brand_logo(
    *,
    session_id: str,
    filename: str,
    path: str,
    byte_size: int,
    width: int | None = None,
    height: int | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO brand_logos (session_id, filename, path, bytes, width, height)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_id) DO UPDATE SET
              filename = excluded.filename,
              path = excluded.path,
              bytes = excluded.bytes,
              width = excluded.width,
              height = excluded.height,
              created_at = datetime('now')
            """,
            (session_id, filename, path, byte_size, width, height),
        )
        conn.commit()


def get_brand_logo(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM brand_logos WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_brand_logo(session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM brand_logos WHERE session_id = ?", (session_id,)
        )
        conn.commit()
