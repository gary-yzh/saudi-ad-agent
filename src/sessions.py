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

from . import guard, storage
from .llm import call_claude
from .nodes.guardrail import _keyword_check
from .nodes.rag import DEFAULT_BRAND_DOC, _extract_constraints, _read_doc
from .runtime import reset_request_config, set_request_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "outputs" / "runs"
LOGOS_DIR = PROJECT_ROOT / "outputs" / "logos"

# Logo overlay: scale relative to the still's width, padding from edges
LOGO_SCALE = 0.13
LOGO_MIN_WIDTH = 80
LOGO_PADDING_FRAC = 0.025


# ---------------------------------------------------------------------------
# Brand constraints — loaded once on first use
# ---------------------------------------------------------------------------

_brand_cache: list[str] | None = None


def _default_brand_constraints() -> list[str]:
    """Constraints from the bundled demo brand manual (Markdown)."""
    global _brand_cache
    if _brand_cache is not None:
        return _brand_cache
    try:
        doc = _read_doc(DEFAULT_BRAND_DOC)
        _brand_cache = _extract_constraints(doc)
    except Exception:
        _brand_cache = []
    return _brand_cache


def _session_brand_excerpt(session_id: str, max_chars: int = 8000) -> str | None:
    """The full text of a session-uploaded brand manual, capped to keep the
    LLM prompt under control. Returns None if no manual was uploaded."""
    text = storage.get_brand_manual_text(session_id)
    if not text:
        return None
    text = text.strip()
    if len(text) <= max_chars:
        return text
    # Take the head + tail so we still include closing rules / sign-off
    head = text[: max_chars - 1500]
    tail = text[-1500:]
    return head + "\n\n[…manual truncated…]\n\n" + tail


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
        "scene": "<one-line plain-English description, including subject specifics>",
        "visual_prompt": "<image-gen prompt. MUST repeat the scene's subject specifics verbatim (gender, attire, ethnicity, age) before adding lighting/framing/palette/9:16. Diffusion models default to category stereotypes (e.g. women for perfume/beauty, men for tools/sport) when subject is vague — explicit subject markers are MANDATORY.>",
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
- Subject consistency: each shot's `visual_prompt` MUST repeat the
  subject specifics from its `scene` (gender, attire, ethnicity, age).
  If the scene says "man in thobe", the visual_prompt MUST also say
  "man in thobe" — never generalize to "person", "model" or
  "spokesperson". Brand modesty defaults like "hijab for spokesperson
  roles" apply only when the scene specifies a woman; never let a
  brand-manual default flip a scene's explicit gender.
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


CONSISTENCY_SYSTEM = """You are a strict brand-compliance reviewer. You will
be given (a) a brand manual and (b) a proposed multi-shot storyboard.

Decide whether the storyboard violates any concrete rule from the brand
manual — palette, typography, modesty defaults, allowed imagery, banned
words, prayer-time / Ramadan rules, GAMR / KSA advertising rules, sign-off
phrasing, etc.

Return STRICT JSON:
{"ok": true,  "violations": []}                 -- compliant
{"ok": false, "violations": [{"rule": "<short rule from manual>", "issue": "<what's wrong with the storyboard>"}, ...]}

Rules to follow:
- Be concrete: cite the manual rule each violation maps to.
- Do NOT flag stylistic differences that aren't actual rule violations.
- If the manual is silent on a topic, don't invent rules.
- Output ONLY the JSON object, no commentary."""


def _check_brand_consistency(storyboard: dict[str, Any], manual_text: str) -> list[dict[str, str]]:
    """Run a second LLM pass that judges the draft storyboard against the
    uploaded brand manual. Returns a list of {rule, issue} dicts.

    On any LLM error we return [] rather than raising — the storyboard is
    already useful even if the consistency pass flakes."""
    if not manual_text:
        return []
    capped = manual_text if len(manual_text) <= 9000 else (manual_text[:8000] + "\n[…truncated…]")
    user = (
        "BRAND MANUAL EXCERPT:\n"
        + capped
        + "\n\nPROPOSED STORYBOARD (JSON):\n"
        + json.dumps(storyboard, ensure_ascii=False, indent=2)
    )
    try:
        result = call_claude(
            system=CONSISTENCY_SYSTEM,
            user=user,
            json_mode=True,
            max_tokens=600,
        )
    except Exception as e:
        print(f"[brand-consistency] check failed (non-fatal): {e}", flush=True)
        return []
    if isinstance(result, dict) and not result.get("ok", True):
        return [v for v in (result.get("violations") or []) if isinstance(v, dict)]
    return []


def chat_turn(session_id: str, user_msg: str) -> dict[str, Any]:
    """Run one round of chat. Returns the assistant's reply payload.

    Side-effects (in order):
      0. **Content guard** on the user's input — if they typed a banned or
         Muslim-sensitive term, raise `guard.UserInputViolation` *before*
         we save the message or call the LLM. The endpoint catches this
         and returns a 400 to the UI.
      1. Save the user message.
      2. Build the LLM prompt using the session's uploaded brand manual
         (if any), otherwise fall back to the bundled demo manual's
         extracted constraint bullets.
      3. Ask the LLM — either clarifying question or storyboard.
      4. If storyboard, run the deterministic AR/EN keyword guardrail.
         Fail → ask the LLM to revise once.
      5. If a session brand manual is uploaded, run a second LLM pass
         that judges the draft against the manual; surface violations to
         the user (and tell the LLM to revise once if any are found).
      6. Save the assistant message + the storyboard payload.
    """
    session = storage.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # Step 0 — content guard
    guard.assert_user_input_clean(user_msg)

    # Step 1 — persist the user turn
    storage.add_message(session_id=session_id, role="user", content=user_msg)

    locale = session.get("locale") or "en-US"
    audience = session.get("target_audience") or ""

    # Step 2 — choose RAG source
    manual_text = _session_brand_excerpt(session_id)
    if manual_text:
        rag_block = (
            "BRAND MANUAL (uploaded by the user — treat as authoritative):\n"
            + manual_text
        )
    else:
        constraints = _default_brand_constraints()
        rag_block = "BRAND CONSTRAINTS (from default demo manual):\n" + "\n".join(
            f"- {c}" for c in constraints[:30]
        )

    user_payload = (
        f"LOCALE: {locale}\n"
        f"TARGET AUDIENCE: {audience}\n\n"
        + rag_block
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

        # Step 4 — keyword guardrail
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

        # Step 5 — brand-manual consistency check (only if user uploaded one)
        consistency_viols = _check_brand_consistency(storyboard, manual_text or "")

        if violations or consistency_viols:
            fix_user = user_payload + "\n\nYour previous draft was rejected. Address every issue below and re-emit a clean storyboard:\n"
            if violations:
                fix_user += "\n# Keyword guardrail violations:\n" + "\n".join(f"- {v}" for v in violations)
            if consistency_viols:
                fix_user += "\n# Brand-manual consistency violations:\n" + "\n".join(
                    f"- rule: {v.get('rule')} — issue: {v.get('issue')}" for v in consistency_viols
                )
            raw2 = call_claude(system=CHAT_SYSTEM, user=fix_user, json_mode=True, max_tokens=1800)
            if isinstance(raw2, dict) and raw2.get("action") == "storyboard":
                storyboard = raw2.get("storyboard") or storyboard
                # Re-run the consistency check on the revised draft so the
                # final payload reflects the freshest assessment.
                consistency_viols = _check_brand_consistency(storyboard, manual_text or "")

        # Step 6 — Eval (CTR estimate + brand-safety self-check).
        # Heuristic-only, runs on every storyboard turn, deterministic
        # and free of extra LLM cost.
        eval_result = _evaluate_storyboard_live(
            storyboard,
            has_violations=bool(consistency_viols),
        )

        summary = raw.get("summary") or "Here's a draft storyboard for your ad."
        storage.update_session_storyboard(session_id, storyboard)
        payload = {
            "action": "storyboard",
            "storyboard": storyboard,
            "eval": eval_result,
        }
        if consistency_viols:
            payload["brand_consistency_warnings"] = consistency_viols
        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=summary,
            payload=payload,
        )
        return {
            "action": "storyboard",
            "summary": summary,
            "storyboard": storyboard,
            "brand_consistency_warnings": consistency_viols,
            "eval": eval_result,
        }

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


def _is_sign_off_shot(session_id: str, shot_id: int) -> bool:
    """The brand logo only lands on the sign-off frame — defined as the
    final shot in the storyboard. Middle shots stay visually clean, the
    way Apple/Nike short-form ads are cut: the brand mark resolves at the
    end, not on every frame."""
    session = storage.get_session(session_id)
    if session is None:
        return False
    shots = (session.get("storyboard") or {}).get("shots") or []
    if not shots:
        return False
    return int(shot_id) == int(shots[-1]["id"])


# ---------------------------------------------------------------------------
# Eval — CTR estimate + brand-safety self-check (live-flow adapter).
#
# nodes/eval.py implements the full eval node for the legacy LangGraph
# path. The live multi-step flow doesn't run that graph, so we expose the
# same logic via this adapter — heuristic-only by default (no extra LLM
# call per chat turn), giving every storyboard a deterministic CTR
# forecast + pass/fail status without burning tokens. Result is attached
# to the assistant message payload AND returned to the API caller, so the
# UI can show it in the storyboard panel.
# ---------------------------------------------------------------------------
from .nodes.eval import _heuristic_score, PASS_CTR_THRESHOLD


def _evaluate_storyboard_live(
    storyboard: dict[str, Any],
    *,
    has_violations: bool = False,
) -> dict[str, Any]:
    """Run heuristic CTR + brand-safety self-check on a storyboard dict.

    Returns:
        {
          "ctr_estimate":     0.0-1.0 ratio,
          "ctr_estimate_pct": "3.2%" string for direct UI display,
          "eval_status":      "pass" | "fail",
          "eval_notes":       list[str] of human-readable findings.
        }

    Status flips to "fail" if CTR is below the KSA short-form floor
    (PASS_CTR_THRESHOLD = 1.5%) or any guardrail / consistency
    violation remained unresolved by the post-LLM fixup pass.
    """
    ctr, notes = _heuristic_score(storyboard)
    status = "pass"
    extra_notes: list[str] = []
    if ctr < PASS_CTR_THRESHOLD:
        status = "fail"
        extra_notes.append(f"Below KSA pass threshold of {PASS_CTR_THRESHOLD:.1%}")
    if has_violations:
        status = "fail"
        extra_notes.append("Brand-consistency / guardrail violations remain")
    return {
        "ctr_estimate": ctr,
        "ctr_estimate_pct": f"{ctr * 100:.1f}%",
        "eval_status": status,
        "eval_notes": list(notes) + extra_notes,
    }


def _logo_hint_block(session_id: str, shot_id: int) -> str:
    """Tell Seedream to reserve a placeholder area, but only on the
    sign-off shot when a logo is actually uploaded. Middle shots get the
    full canvas — no forced negative space."""
    if not _is_sign_off_shot(session_id, shot_id):
        return ""
    if storage.get_brand_logo(session_id) is None:
        return ""
    return (
        "\n\nIMPORTANT — LOGO PLACEHOLDER: Reserve a clean, empty area "
        "(~12% width, bottom-right corner) for the brand logo. Do NOT draw "
        "any logo, brand mark, watermark, sign-off text, or typography in "
        "that area. The user has uploaded the real logo separately and it "
        "will be composited into that space afterwards."
    )


def _composite_logo_onto_image(
    image_bytes: bytes, logo_path: Path, *, output_path: Path
) -> dict[str, Any]:
    """Open the Seedream JPEG, overlay the brand logo in the bottom-right
    with alpha-aware blending, write to `output_path` as JPEG."""
    from io import BytesIO

    from PIL import Image

    base = Image.open(BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")

    target_w = max(LOGO_MIN_WIDTH, int(base.width * LOGO_SCALE))
    target_h = max(1, int(logo.height * (target_w / max(1, logo.width))))
    logo_resized = logo.resize((target_w, target_h), Image.LANCZOS)

    pad = max(16, int(base.width * LOGO_PADDING_FRAC))
    x = base.width - target_w - pad
    y = base.height - target_h - pad
    base.paste(logo_resized, (x, y), logo_resized)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out = base.convert("RGB")
    out.save(output_path, format="JPEG", quality=92)
    return {
        "logo_w": target_w,
        "logo_h": target_h,
        "pad": pad,
        "base_size": list(base.size),
    }


def _save_still_locally(
    img_bytes: bytes,
    *,
    session_id: str,
    shot_id: int,
    logo: dict | None,
) -> tuple[str, dict]:
    """Persist a Seedream output (with optional logo overlay) under
    outputs/runs/<sid>/shots/<id>.jpg and return (display_url, extra_meta).

    Always saves locally — that's how the URL stays valid past Volcengine's
    24-hour signed-URL expiry. If a logo is configured, it's alpha-composited
    onto the bottom-right; otherwise we just write the original bytes."""
    out_path = RUNS_DIR / session_id / "shots" / f"{shot_id}.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    extra: dict[str, Any] = {}
    if logo and logo.get("path"):
        try:
            comp_meta = _composite_logo_onto_image(
                img_bytes, Path(logo["path"]), output_path=out_path
            )
            extra["composited"] = True
            extra["composite_meta"] = comp_meta
        except Exception as e:
            # Fallback: save the un-composited bytes so the URL is still valid
            out_path.write_bytes(img_bytes)
            extra["composited"] = False
            extra["composite_error"] = str(e)
    else:
        out_path.write_bytes(img_bytes)
        extra["composited"] = False

    display_url = f"/runs/{session_id}/shots/{shot_id}.jpg"
    return display_url, extra


def _gen_one_shot(
    session_id: str,
    shot: dict,
    *,
    prompt_override: str | None = None,
    extra_metadata: dict | None = None,
    initial_softening_level: int = 0,
) -> None:
    """Generate (or regenerate) a single shot's still image.

    Two prompt-source modes:
      * Default — rebuild prompt from the storyboard shot + logo hint.
      * `prompt_override` — use the supplied prompt verbatim. Refine and
        Retry pass the accumulated prompt this way so iterative edits
        compound (turn 1's "darker background" still applies on turn 2).

    The accumulated prompt is stashed in metadata.current_prompt so the
    next refine/retry can read it back.
    """
    from .tools import bytedance_apis as apis

    shot_id = int(shot["id"])
    storage.update_shot_image(session_id, shot_id, status="running")

    # Brand logo is reserved for the sign-off (last) shot only — see
    # _is_sign_off_shot for the rationale. Middle shots get a clean canvas.
    is_sign_off = _is_sign_off_shot(session_id, shot_id)
    logo = storage.get_brand_logo(session_id) if is_sign_off else None
    if prompt_override is not None:
        current_prompt = prompt_override
    else:
        base_prompt = shot.get("visual_prompt", "") or shot.get("scene", "")
        current_prompt = base_prompt + _logo_hint_block(session_id, shot_id)

    # Auto-soften retry loop. Doubao moderation is deterministic, so blind
    # retry won't help — but a softened prompt usually does. We try the
    # original first (so safe prompts keep their full creative specificity),
    # then up to MAX_AUTO_RETRIES progressively softer rewrites. Only
    # surface a failure to the user if every attempt is rejected — at that
    # point manual rephrasing is the right next step anyway, and they
    # see a friendly message instead of a raw Doubao error code.
    from .nodes.tool_use import _soften_prompt  # local import — avoids cycles

    MAX_AUTO_RETRIES = 2
    result: dict | None = None
    softening_history: list[dict[str, Any]] = []
    last_exc: Exception | None = None
    last_was_moderation = False

    for attempt in range(MAX_AUTO_RETRIES + 1):
        try:
            result = _run_with_config(
                apis.seedream_generate, prompt=current_prompt, aspect="9:16"
            )
            break  # success — fall through to download/persist
        except apis.ContentModerationError as e:
            last_exc = e
            last_was_moderation = True
            if attempt >= MAX_AUTO_RETRIES:
                break
            try:
                # Escalate from the caller's level + how many auto-retries we've
                # done so far. Without this, manual retry's escalation gets
                # silently undone — the caller hands us a level-3 prompt and
                # we soften it with attempt=1 (LIGHT), backing off to a
                # less-softened prompt. Compounding is the only way for
                # repeated retries to actually escalate.
                next_level = initial_softening_level + attempt + 1
                softened = _soften_prompt(
                    current_prompt,
                    stage="image",
                    reason=e.message,
                    attempt=next_level,
                )
            except Exception as soft_err:
                # Softening LLM call itself failed — give up gracefully.
                last_exc = soft_err
                break
            softening_history.append({
                "attempt": next_level,
                "moderation_code": e.code,
                "moderation_msg": (e.message or "")[:200],
                "softened_head": softened[:200],
            })
            current_prompt = softened
        except Exception as e:
            last_exc = e
            last_was_moderation = False
            break

    if result is None:
        # All attempts failed — store as failed with a user-facing
        # message that says what to do next, not the raw API error.
        if last_was_moderation:
            attempts_made = len(softening_history) + 1  # original + each retry
            error_str = (
                f"Doubao kept flagging this shot as sensitive after {attempts_made} "
                f"automatic rewrites. Use the Apply box below to rephrase manually "
                f"(e.g. swap specific cultural markers for neutral ones, drop people, "
                f"or describe just the product)."
            )
            failure_md: dict[str, Any] = {
                "category": "moderation",
                "code": getattr(last_exc, "code", "unknown"),
                "message": getattr(last_exc, "message", str(last_exc)),
                "current_prompt": current_prompt,
                "auto_soften_attempts": softening_history,
                # Cumulative level: caller's starting level + auto-retries
                # we did. Next manual retry's _soften_prompt picks up here
                # + 1, so consecutive Retry clicks keep escalating without
                # silently snapping back to LIGHT softening.
                "retry_softening_level": initial_softening_level + len(softening_history),
            }
        else:
            error_str = str(last_exc) if last_exc else "unknown error"
            failure_md = {"current_prompt": current_prompt}
            if softening_history:
                failure_md["auto_soften_attempts"] = softening_history
        if extra_metadata:
            failure_md.update(extra_metadata)
        storage.update_shot_image(
            session_id,
            shot_id,
            status="failed",
            error=error_str,
            metadata=failure_md,
        )
        return

    # Success path — `result` is set, `current_prompt` is whatever ended up
    # working (original or one of the softened rewrites).
    original_url = result["url"]
    metadata: dict[str, Any] = dict(result)
    metadata["original_url"] = original_url
    metadata["prompt_used"] = current_prompt[:1500]
    metadata["current_prompt"] = current_prompt
    if softening_history:
        metadata["auto_soften_attempts"] = softening_history
        metadata["retry_softening_level"] = initial_softening_level + len(softening_history)
    if extra_metadata:
        metadata.update(extra_metadata)

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            img_bytes = client.get(original_url).content
        display_url, extra = _save_still_locally(
            img_bytes,
            session_id=session_id,
            shot_id=shot_id,
            logo=logo,
        )
        metadata.update(extra)
    except Exception as e:
        # Local download/compose failed — keep the UI working with the
        # remote URL while it's still valid.
        display_url = original_url
        metadata["download_error"] = str(e)
        print(
            f"[shot {shot_id}] download to local failed (non-fatal): {e}",
            flush=True,
        )

    storage.update_shot_image(
        session_id,
        shot_id,
        status="succeeded",
        url=display_url,
        metadata=metadata,
    )


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
    # Seedance fetches reference images from the URL we send, so it must be
    # publicly reachable. The display URL may point to our local /runs/
    # composite (which Volcengine can't fetch), so prefer the upstream
    # Volcengine URL stashed in metadata.original_url when present.
    def _ref_url(row: dict) -> str | None:
        md_raw = row.get("metadata_json")
        if md_raw:
            try:
                md = json.loads(md_raw)
                if md.get("original_url"):
                    return md["original_url"]
            except json.JSONDecodeError:
                pass
        return row.get("url")

    image_url_by_shot: dict[int, str] = {
        r["shot_id"]: _ref_url(r) for r in images if _ref_url(r)
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

    # Auto-soften retry on Doubao video moderation. Mirrors what _gen_one_shot
    # does for Seedream, but with MAX = 1 instead of 2 because each Seedance
    # call is 3-30 minutes — auto-retrying twice could keep the user waiting
    # an hour. One retry caps total wait at ~30+30 = up to 60 min worst case.
    from .nodes.tool_use import _soften_prompt  # local import — avoids cycles

    MAX_VIDEO_AUTO_RETRIES = 1
    current_motion_prompt = motion_prompt
    softening_history: list[dict[str, Any]] = []
    last_exc: Exception | None = None
    last_was_moderation = False
    result: dict | None = None

    for attempt in range(MAX_VIDEO_AUTO_RETRIES + 1):
        try:
            result = _run_with_config(
                apis.seedance_generate,
                image_urls=selected_image_urls,
                motion_prompt=current_motion_prompt,
                duration_s=float(duration),
            )
            break  # success — fall through to download/persist
        except apis.ContentModerationError as e:
            last_exc = e
            last_was_moderation = True
            if attempt >= MAX_VIDEO_AUTO_RETRIES:
                break
            try:
                softened = _soften_prompt(
                    current_motion_prompt,
                    stage="video",
                    reason=e.message,
                    attempt=attempt + 1,
                )
            except Exception as soft_err:
                last_exc = soft_err
                break
            softening_history.append({
                "attempt": attempt + 1,
                "moderation_code": e.code,
                "moderation_msg": (e.message or "")[:200],
                "softened_head": softened[:200],
            })
            current_motion_prompt = softened
        except Exception as e:
            last_exc = e
            last_was_moderation = False
            break

    if result is None:
        # All attempts failed — surface a user-facing message that says what
        # to do next, not the raw API error code + request id.
        if last_was_moderation:
            attempts_made = len(softening_history) + 1
            error_str = (
                f"Doubao's video safety filter rejected the generated video "
                f"after {attempts_made} attempt{'s' if attempts_made > 1 else ''}. "
                f"This is harder to fix than the per-image filter: even if every "
                f"still passed the image filter, the video filter re-scans the "
                f"animated output and can flag it for the same cultural / "
                f"religious cues. Try one of:\n"
                f"  • De-select any shots showing people in culturally-coded "
                f"attire (thobe / abaya / hijab) and pick more product-focused "
                f"stills\n"
                f"  • Re-draft the storyboard with a more product-centric brief "
                f"(less people, more product close-ups)"
            )
        else:
            error_str = str(last_exc) if last_exc else "unknown error"
        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="failed",
            error=error_str,
            metadata={
                "auto_soften_attempts": softening_history,
                "current_motion_prompt": current_motion_prompt,
            } if softening_history else None,
        )
        storage.update_session_state(session_id, "images_done")
        return

    # Success path — `result` is set, motion_prompt may have been softened.
    try:
        remote_url = result["url"]

        # Download to local so the browser plays it from our origin
        local_path = RUNS_DIR / session_id / "video.mp4"
        bytes_written = _download_to(local_path, remote_url)
        local_url = f"/runs/{session_id}/video.mp4"

        meta_extra: dict[str, Any] = {
            "bytes": bytes_written,
            "task_id": result.get("task_id"),
            "duration_s": result.get("duration_s"),
            "ratio": result.get("ratio"),
            "model": result.get("model"),
        }
        if softening_history:
            meta_extra["auto_soften_attempts"] = softening_history
            meta_extra["motion_prompt_used"] = current_motion_prompt[:1500]

        storage.upsert_video(
            session_id=session_id,
            selected_shot_ids=selected_ids,
            status="succeeded",
            remote_url=remote_url,
            local_url=local_url,
            metadata=meta_extra,
        )
        storage.update_session_state(session_id, "video_done")
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


# ---------------------------------------------------------------------------
# Brand manual upload — extract text on receipt and stash in SQLite
# ---------------------------------------------------------------------------


def _shot_in_storyboard(session_id: str, shot_id: int) -> dict[str, Any]:
    session = storage.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
    storyboard = session.get("storyboard") or {}
    shot = next(
        (s for s in storyboard.get("shots") or [] if int(s["id"]) == int(shot_id)),
        None,
    )
    if shot is None:
        raise ValueError(f"Shot {shot_id} not in this session's storyboard")
    return shot


def _prev_shot_metadata(session_id: str, shot_id: int) -> dict[str, Any]:
    """Read back the metadata blob from the last image-gen attempt for
    this shot, or {} if no row exists."""
    images = storage.list_shot_images(session_id)
    prev = next((r for r in images if int(r["shot_id"]) == int(shot_id)), None)
    if not prev or not prev.get("metadata_json"):
        return {}
    try:
        return json.loads(prev["metadata_json"])
    except json.JSONDecodeError:
        return {}


def refine_shot(
    *, session_id: str, shot_id: int, instruction: str, executor: ThreadPoolExecutor
) -> None:
    """Re-generate a shot with an iterative user instruction appended.

    Each refinement builds on the prompt that **was actually used last
    time** (cumulative), not on the original storyboard prompt — so
    'make it darker' followed by 'no people' produces a darker, no-people
    image, not just a no-people image."""
    shot = _shot_in_storyboard(session_id, int(shot_id))
    prev_md = _prev_shot_metadata(session_id, int(shot_id))

    base = (
        prev_md.get("current_prompt")
        or (shot.get("visual_prompt") or "") + _logo_hint_block(session_id, int(shot_id))
    )
    new_prompt = (
        base
        + f"\n\nUser refinement #{len(prev_md.get('refinement_history') or []) + 1}: "
        + instruction.strip()
    )

    history = list(prev_md.get("refinement_history") or [])
    history.append({"instruction": instruction.strip()})

    storage.update_shot_image(session_id, int(shot_id), status="running")
    executor.submit(
        _gen_one_shot,
        session_id,
        shot,
        prompt_override=new_prompt,
        extra_metadata={
            "refinement_history": history,
            # reset retry counter — refinement is a fresh creative direction
            "retry_softening_level": 0,
        },
    )


def retry_shot(*, session_id: str, shot_id: int, executor: ThreadPoolExecutor) -> None:
    """Re-run Seedream for a shot.

    Builds on the **last actually-used prompt** (current_prompt in
    metadata) so any prior refinements survive the retry.

    If the prior attempt was rejected by Doubao's content moderation, we
    don't blindly resubmit the same prompt — Doubao is deterministic. We
    ask the LLM to soften the previous prompt and advance a
    `retry_softening_level` counter; each subsequent retry is more
    aggressive (light cultural-marker replacement → product-only).
    Non-moderation failures retry the previous prompt unchanged.
    """
    from .nodes.tool_use import _soften_prompt  # local import — avoids cycles

    shot = _shot_in_storyboard(session_id, int(shot_id))
    prev_md = _prev_shot_metadata(session_id, int(shot_id))
    prev_row = next(
        (r for r in storage.list_shot_images(session_id) if int(r["shot_id"]) == int(shot_id)),
        None,
    )

    last_prompt = (
        prev_md.get("current_prompt")
        or (shot.get("visual_prompt") or "") + _logo_hint_block(session_id, int(shot_id))
    )

    was_moderation = prev_md.get("category") == "moderation"
    softening_level = int(prev_md.get("retry_softening_level", 0))

    extra_md: dict[str, Any] = {}
    retry_prompt: str = last_prompt

    if was_moderation:
        softening_level += 1
        try:
            softened = _soften_prompt(
                last_prompt,
                stage="image",
                reason=(prev_row or {}).get("error") or "previous moderation hit",
                attempt=softening_level,
            )
            retry_prompt = softened
            extra_md["retry_softening_level"] = softening_level
            extra_md["retry_softened_prompt_head"] = softened[:240]
        except Exception as e:
            extra_md["soften_error"] = str(e)
            extra_md["retry_softening_level"] = softening_level
    # Preserve refinement history through the retry
    if prev_md.get("refinement_history"):
        extra_md["refinement_history"] = prev_md["refinement_history"]

    storage.update_shot_image(session_id, int(shot_id), status="running")
    executor.submit(
        _gen_one_shot,
        session_id,
        shot,
        prompt_override=retry_prompt,
        extra_metadata=extra_md or None,
        # Tell _gen_one_shot's auto-retry to start AFTER this softening level.
        # Without this, auto-retry calls _soften_prompt(attempt=1) (LIGHT) on
        # an already-AGGRESSIVE prompt — silently undoing the escalation that
        # retry_shot just did. With this, attempts inside _gen_one_shot keep
        # climbing past softening_level (level+1 → level+2 → ...).
        initial_softening_level=softening_level if was_moderation else 0,
    )


def save_uploaded_brand_logo(
    *, session_id: str, filename: str, image_bytes: bytes
) -> dict[str, Any]:
    """Persist an uploaded logo PNG/JPG/WEBP into outputs/logos/<sid>/ and
    record metadata. The image is also composited onto every subsequent
    Seedream still."""
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        fmt = (img.format or "PNG").upper()
    except Exception as e:
        raise ValueError(f"Could not read image: {e}") from e

    ext_map = {"PNG": ".png", "JPEG": ".jpg", "JPG": ".jpg", "WEBP": ".webp"}
    ext = ext_map.get(fmt, ".png")

    logo_dir = LOGOS_DIR / session_id
    logo_dir.mkdir(parents=True, exist_ok=True)
    out_path = logo_dir / f"logo{ext}"
    out_path.write_bytes(image_bytes)

    storage.save_brand_logo(
        session_id=session_id,
        filename=filename,
        path=str(out_path),
        byte_size=len(image_bytes),
        width=width,
        height=height,
    )
    return {
        "filename": filename,
        "bytes": len(image_bytes),
        "width": width,
        "height": height,
        "format": fmt,
    }


def save_uploaded_brand_manual(*, session_id: str, filename: str, pdf_bytes: bytes) -> dict[str, Any]:
    """Read an uploaded PDF, extract every page's text, persist to the
    brand_manuals table. Returns a dict for the API response.

    Raises ValueError on a non-PDF / unreadable file."""
    from io import BytesIO

    from pypdf import PdfReader
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as e:
        raise ValueError(f"Could not read PDF: {e}") from e

    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError(
            "PDF parsed but no text extracted — is this a scanned PDF? "
            "We don't OCR, so please supply a text-based PDF."
        )

    storage.save_brand_manual(
        session_id=session_id,
        filename=filename,
        pages=len(pages),
        byte_size=len(pdf_bytes),
        text=text,
    )
    return {
        "filename": filename,
        "pages": len(pages),
        "bytes": len(pdf_bytes),
        "chars": len(text),
    }
