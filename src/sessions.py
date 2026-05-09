"""Multi-step ad-creation flow.

The legacy one-shot LangGraph pipeline still exists (see `src/graph.py` and
`/api/run`), but the web UI uses this module instead, which models the
flow as four user-gated steps:

  1. chat_turn(session_id, user_msg)
       LLM either asks a clarifying question or proposes a multi-shot
       storyboard. Multi-turn — call repeatedly until the user is happy.

  2. confirm_storyboard(session_id)
       Locks in the latest proposed storyboard and queues all shots for
       Seedream image generation.

  3. start_image_generation(session_id, executor)
       Fires Seedream calls per shot in worker threads. Caller polls
       `list_shot_statuses(session_id)` for progress.

  4. start_video_generation(session_id, selected_shot_ids, executor)
       Sends selected images to Seedance, waits for completion, downloads
       the resulting MP4 to outputs/runs/<session_id>/video.mp4 so the
       browser can play it via the static mount.

Heavy work (Seedream / Seedance) runs on a ThreadPoolExecutor. Each task
re-establishes the per-request runtime config inside its worker thread
because contextvars don't propagate across threads automatically.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import storage
from .llm import call_claude
from .nodes.guardrail import _keyword_check
from .nodes.rag import DEFAULT_BRAND_DOC, _extract_constraints, _read_doc
from .runtime import reset_request_config, set_request_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "outputs" / "runs"


# ---------------------------------------------------------------------------
# Brand constraints — loaded once on first use
# ---------------------------------------------------------------------------

_brand_cache: list[str] | None = None


def _brand_constraints() -> list[str]:
    global _brand_cache
    if _brand_cache is not None:
        return _brand_cache
    try:
        doc = _read_doc(DEFAULT_BRAND_DOC)
        _brand_cache = _extract_constraints(doc)
    except Exception:
        _brand_cache = []
    return _brand_cache


# ---------------------------------------------------------------------------
# 1. Chat turn — multi-turn storyboard drafting
# ---------------------------------------------------------------------------

CHAT_SYSTEM = """You are an ad creative planner for a Saudi e-commerce brand.

You hold a multi-turn conversation with a marketer. After each user message,
decide ONE of:

A) ASK — the brief is missing critical info. Ask exactly ONE specific
   clarifying question. Cover at most 1-2 of: product details, price tier,
   primary feature to lead with, target audience, tone, target platform,
   total ad length.

B) STORYBOARD — you have enough info to draft a multi-shot storyboard.

OUTPUT — STRICT JSON, exactly one of these two shapes:

{"action": "ask", "question": "<your single clarifying question>"}

OR:

{
  "action": "storyboard",
  "summary": "<one short sentence acknowledging what you understood>",
  "storyboard": {
    "hook": "<= 8 words, English",
    "body": "1-2 sentences describing the ad",
    "cta": "<= 5 words",
    "voiceover": "<single line of speech in the locale's language>",
    "voice": "voice ID (e.g. en-US-male-warm)",
    "shots": [
      {
        "id": 1,
        "scene": "<one-line plain-English description>",
        "visual_prompt": "<image-gen prompt: scene, lighting, framing, palette, 9:16 vertical>",
        "motion_prompt": "<video-gen prompt: camera move + animation, 1 sentence>",
        "duration_s": <float, 2.0-5.0>
      },
      ... 3 to 6 shots total
    ]
  }
}

Hard rules:
- Total shot duration must sum to 8-15 seconds.
- Voiceover language matches the locale (Arabic for ar-*, English for en-*,
  Chinese for zh-*).
- No alcohol, no pork, no gambling iconography.
- For Ramadan briefs, evoke family / generosity, not discount urgency.
- Output ONLY the JSON. No markdown fences, no commentary.

If the user explicitly asks for revisions to a previous storyboard you
proposed, return a fresh storyboard incorporating their feedback.
"""


def _conversation_for_llm(session_id: str) -> str:
    msgs = storage.list_messages(session_id)
    lines: list[str] = []
    for m in msgs:
        role = m["role"].upper()
        lines.append(f"[{role}] {m['content']}")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    fence = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fence.group(1) if fence else text


def chat_turn(session_id: str, user_msg: str) -> dict[str, Any]:
    """Run one round of chat. Returns the assistant's reply payload.

    Side-effects:
      - Saves both the user message and the assistant message.
      - If the LLM returns a storyboard, runs the deterministic guardrail.
        Pass → save the storyboard, set state=storyboard_draft.
        Fail → tell the LLM to revise (one retry) inside this same turn.
    """
    session = storage.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    storage.add_message(session_id=session_id, role="user", content=user_msg)

    locale = session.get("locale") or "en-US"
    audience = session.get("target_audience") or ""
    constraints = _brand_constraints()

    user_payload = (
        f"LOCALE: {locale}\n"
        f"TARGET AUDIENCE: {audience}\n\n"
        "BRAND CONSTRAINTS:\n"
        + "\n".join(f"- {c}" for c in constraints[:30])
        + "\n\nCONVERSATION SO FAR:\n"
        + _conversation_for_llm(session_id)
    )

    raw = call_claude(
        system=CHAT_SYSTEM,
        user=user_payload,
        json_mode=True,
        max_tokens=1800,
    )

    if not isinstance(raw, dict):
        raise RuntimeError(f"Planner returned non-dict: {raw!r}")

    action = raw.get("action")

    if action == "ask":
        question = (raw.get("question") or "").strip() or "Could you tell me a bit more about the product and audience?"
        storage.add_message(session_id=session_id, role="assistant", content=question, payload={"action": "ask"})
        return {"action": "ask", "question": question}

    if action == "storyboard":
        storyboard = raw.get("storyboard") or {}
        # Run the cheap deterministic guardrail
        violations = _keyword_check(
            {
                "hook": storyboard.get("hook", ""),
                "body": storyboard.get("body", ""),
                "cta": storyboard.get("cta", ""),
                "visual_prompt": " ".join(s.get("visual_prompt", "") for s in storyboard.get("shots", [])),
                "motion_prompt": " ".join(s.get("motion_prompt", "") for s in storyboard.get("shots", [])),
                "voiceover": storyboard.get("voiceover", ""),
            },
            ramadan="ramadan" in user_msg.lower(),
        )
        if violations:
            # One automatic regeneration nudging the LLM to fix it.
            fix_user = (
                user_payload
                + "\n\nYour previous draft was rejected by the guardrail. "
                + "Address every one of these and re-emit a clean storyboard:\n"
                + "\n".join(f"- {v}" for v in violations)
            )
            raw2 = call_claude(system=CHAT_SYSTEM, user=fix_user, json_mode=True, max_tokens=1800)
            if isinstance(raw2, dict) and raw2.get("action") == "storyboard":
                storyboard = raw2.get("storyboard") or storyboard

        summary = raw.get("summary") or "Here's a draft storyboard for your ad."
        storage.update_session_storyboard(session_id, storyboard)
        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=summary,
            payload={"action": "storyboard", "storyboard": storyboard},
        )
        return {"action": "storyboard", "summary": summary, "storyboard": storyboard}

    # Unknown action — degrade to ask
    fallback = "I didn't quite get that — can you tell me more about the product and target audience?"
    storage.add_message(session_id=session_id, role="assistant", content=fallback, payload={"action": "ask"})
    return {"action": "ask", "question": fallback}


# ---------------------------------------------------------------------------
# 2. Storyboard confirmation
# ---------------------------------------------------------------------------


def confirm_storyboard(session_id: str) -> dict[str, Any]:
    session = storage.get_session(session_id)
    if session is None or not session.get("storyboard"):
        raise ValueError("No storyboard drafted yet")
    storyboard = session["storyboard"]
    shots = storyboard.get("shots") or []
    if not shots:
        raise ValueError("Storyboard has no shots")
    storage.update_session_state(session_id, "storyboard_confirmed")
    for shot in shots:
        storage.queue_shot_image(session_id, int(shot["id"]))
    return {"shots_queued": len(shots)}


# ---------------------------------------------------------------------------
# 3. Image generation (per shot, threaded)
# ---------------------------------------------------------------------------


def _run_with_config(fn, *args, **kwargs):
    """Set the per-request runtime config from SQLite for the duration of
    this thread's task, then reset. Each thread gets its own contextvar
    state so concurrent shots don't trample one another."""
    cfg = storage.load_config()
    token = set_request_config(**cfg)
    try:
        return fn(*args, **kwargs)
    finally:
        reset_request_config(token)


def _gen_one_shot(session_id: str, shot: dict) -> None:
    from .tools import bytedance_apis as apis

    shot_id = int(shot["id"])
    storage.update_shot_image(session_id, shot_id, status="running")
    try:
        result = _run_with_config(
            apis.seedream_generate,
            prompt=shot.get("visual_prompt", "") or shot.get("scene", ""),
            aspect="9:16",
        )
        storage.update_shot_image(
            session_id, shot_id, status="succeeded", url=result["url"], metadata=result
        )
    except apis.ContentModerationError as e:
        # No softening here — the user can re-prompt the chat to revise.
        storage.update_shot_image(
            session_id,
            shot_id,
            status="failed",
            error=f"{e.code}: {e.message}",
        )
    except Exception as e:
        storage.update_shot_image(session_id, shot_id, status="failed", error=str(e))


def start_image_generation(session_id: str, executor: ThreadPoolExecutor) -> None:
    session = storage.get_session(session_id)
    if session is None or not session.get("storyboard"):
        raise ValueError("No storyboard")
    storage.update_session_state(session_id, "images_running")
    for shot in session["storyboard"].get("shots") or []:
        executor.submit(_gen_one_shot, session_id, shot)


def list_shot_statuses(session_id: str) -> dict[str, Any]:
    rows = storage.list_shot_images(session_id)
    all_done = bool(rows) and all(r["status"] in ("succeeded", "failed") for r in rows)
    if all_done:
        # Persist state transition once
        s = storage.get_session(session_id)
        if s and s.get("state") == "images_running":
            storage.update_session_state(session_id, "images_done")
    return {
        "shots": rows,
        "all_done": all_done,
    }


# ---------------------------------------------------------------------------
# 4. Video generation
# ---------------------------------------------------------------------------


def _build_motion_prompt(storyboard: dict, selected: list[dict]) -> str:
    """Concatenate the motion_prompt of each selected shot, in order, prefixed
    with the overall hook/body for context."""
    pieces: list[str] = []
    if storyboard.get("hook"):
        pieces.append(f"Concept: {storyboard['hook']}.")
    if storyboard.get("body"):
        pieces.append(storyboard["body"])
    for i, s in enumerate(selected, 1):
        mp = s.get("motion_prompt") or s.get("scene") or ""
        if mp:
            pieces.append(f"Shot {i}: {mp}")
    if storyboard.get("cta"):
        pieces.append(f"End with: {storyboard['cta']}.")
    return "\n".join(pieces)


def _download_to(local_path: Path, url: str, timeout: float = 300) -> int:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            n = 0
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    n += len(chunk)
    return n


def _gen_video(session_id: str, selected_ids: list[int]) -> None:
    from .tools import bytedance_apis as apis

    session = storage.get_session(session_id)
    storyboard = (session or {}).get("storyboard") or {}
    shots = storyboard.get("shots") or []
    images = storage.list_shot_images(session_id)
    image_url_by_shot: dict[int, str] = {
        r["shot_id"]: r["url"] for r in images if r.get("url")
    }

    # Preserve the user's order; drop shots without a successful image.
    selected_shots: list[dict] = []
    selected_image_urls: list[str] = []
    for sid in selected_ids:
        url = image_url_by_shot.get(int(sid))
        shot = next((s for s in shots if int(s["id"]) == int(sid)), None)
        if url and shot:
            selected_shots.append(shot)
            selected_image_urls.append(url)

    if not selected_shots:
        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="failed",
            error="No selected shot has a generated image yet.",
        )
        storage.update_session_state(session_id, "images_done")
        return

    motion_prompt = _build_motion_prompt(storyboard, selected_shots)
    duration = sum(float(s.get("duration_s") or 3.0) for s in selected_shots)
    duration = max(3, min(15, int(round(duration))))

    storage.upsert_video(
        session_id=session_id,
        selected_shot_ids=selected_ids,
        status="running",
    )
    storage.update_session_state(session_id, "video_running")

    try:
        result = _run_with_config(
            apis.seedance_generate,
            image_urls=selected_image_urls,
            motion_prompt=motion_prompt,
            duration_s=float(duration),
        )
        remote_url = result["url"]

        # Download to local so the browser plays it from our origin
        local_path = RUNS_DIR / session_id / "video.mp4"
        bytes_written = _download_to(local_path, remote_url)
        local_url = f"/runs/{session_id}/video.mp4"

        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="succeeded",
            remote_url=remote_url,
            local_url=local_url,
            metadata={
                "bytes": bytes_written,
                "task_id": result.get("task_id"),
                "duration_s": result.get("duration_s"),
                "ratio": result.get("ratio"),
                "model": result.get("model"),
            },
        )
        storage.update_session_state(session_id, "video_done")
    except apis.ContentModerationError as e:
        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="failed",
            error=f"Doubao safety filter rejected the video: {e.code}: {e.message}",
        )
        storage.update_session_state(session_id, "images_done")
    except Exception as e:
        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="failed",
            error=str(e),
        )
        storage.update_session_state(session_id, "images_done")


def start_video_generation(
    session_id: str, selected_shot_ids: list[int], executor: ThreadPoolExecutor
) -> None:
    storage.upsert_video(
        session_id=session_id,
        selected_shot_ids=selected_shot_ids,
        status="queued",
    )
    executor.submit(_gen_video, session_id, selected_shot_ids)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def new_session_id() -> str:
    return uuid.uuid4().hex
