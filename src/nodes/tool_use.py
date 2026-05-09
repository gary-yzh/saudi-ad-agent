"""Tool-use node — call Doubao Seedream → Seedance → TTS in order.

Sequential because Seedance accepts the still from Seedream as a reference
image, which keeps the two visually consistent. TTS is independent but runs
last so the trace reads cleanly in the UI.

Doubao's safety filter is finicky on Saudi imagery (abaya, hijab, mosque,
prayer scenes can all trigger `OutputImage/VideoSensitiveContentDetected`).
On a moderation hit we ask the LLM to rewrite the prompt with culturally
neutral substitutes and retry up to MAX_MODERATION_RETRIES times.
"""
from __future__ import annotations

from typing import Any, Callable

from ..llm import call_claude
from ..state import AgentState
from ..tools import bytedance_apis as apis
from ..tools.bytedance_apis import ContentModerationError

MAX_MODERATION_RETRIES = 2

SOFTEN_LIGHT = """You rewrite image / video generation prompts so they pass
Doubao's safety filter. The original prompt was rejected for sensitive content.

Replace anything religiously, ethnically or politically charged with neutral
equivalents while keeping the brand intent and visual style:
- "Saudi mother in abaya" → "young woman in modest dark long-sleeve top"
- "mosque", "minaret", "azaan", "iftar" → cafe / kitchen / dinner table
- specific ethnicities, religions, holidays → omit or use generic descriptors
- Ramadan / Eid → "family gathering" or "celebration"
- traditional dress → "modest casual attire" / "professional outfit"
- Arabic-language UI / typography references → drop them

Keep the lighting, framing, palette, motion direction and the product.

Return STRICT JSON: {"prompt": "<the rewritten prompt>"}. Output ONLY the JSON.
"""

SOFTEN_AGGRESSIVE = """You rewrite a video generation prompt that has been
rejected TWICE by Doubao's safety filter. Take the strongest possible action:

- REMOVE all humans entirely. The shot is product-only or environment-only.
- REMOVE every cultural, ethnic, religious or geographic marker.
- REMOVE all language / typography references.
- KEEP only: the product, the lighting, the camera move, the palette, the framing.

Output a clean, generic product/scene description that any global brand could
use. Aim for ~40 words. Do not mention people, faces, dress, festivals or any
specific country or region.

Return STRICT JSON: {"prompt": "<the rewritten prompt>"}. Output ONLY the JSON.
"""


def _soften_prompt(original: str, *, stage: str, reason: str, attempt: int) -> str:
    """Ask the LLM to rewrite a flagged prompt. More aggressive on later attempts.
    Falls back to a regex strip if the LLM call fails."""
    system = SOFTEN_AGGRESSIVE if attempt >= 2 else SOFTEN_LIGHT
    try:
        result = call_claude(
            system=system,
            user=(
                f"STAGE: {stage}\n"
                f"REJECTION REASON: {reason}\n"
                f"ATTEMPT NUMBER: {attempt}\n\n"
                f"ORIGINAL PROMPT:\n{original}"
            ),
            json_mode=True,
            max_tokens=800,
        )
        new_prompt = (result or {}).get("prompt", "").strip()
        if new_prompt:
            return new_prompt
    except Exception as e:
        print(f"[soften_prompt] LLM rewrite failed: {e}", flush=True)
    # Last-resort regex strip.
    out = original
    for term in (
        "abaya", "hijab", "thobe", "mosque", "minaret", "azaan", "iftar",
        "ramadan", "Saudi mother", "Saudi family", "Saudi woman", "Saudi man",
        "Muslim", "Islamic", "Eid", "prayer", "Arabic typography", "Arabic copy",
        "Tajawal",
    ):
        out = out.replace(term, "").replace(term.capitalize(), "")
    return out.strip() or "A clean modern product shot, neutral lighting, 9:16 vertical."


def _call_with_moderation_retry(
    fn: Callable[[str], dict[str, Any]],
    *,
    initial_prompt: str,
    stage: str,
    log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run `fn(prompt)`. On moderation error, soften and retry."""
    prompt = initial_prompt
    last_exc: ContentModerationError | None = None
    for attempt in range(MAX_MODERATION_RETRIES + 1):
        try:
            result = fn(prompt)
            if attempt > 0:
                result["moderation_retries"] = attempt
                result["softened_prompt"] = prompt
            return result
        except ContentModerationError as e:
            last_exc = e
            log.append(
                {
                    "event": "moderation_hit",
                    "stage": stage,
                    "code": e.code,
                    "attempt": attempt,
                    "message": e.message,
                }
            )
            if attempt >= MAX_MODERATION_RETRIES:
                break
            prompt = _soften_prompt(prompt, stage=stage, reason=e.message, attempt=attempt + 1)
            log.append(
                {
                    "event": "prompt_softened",
                    "stage": stage,
                    "attempt": attempt + 1,
                    "new_prompt_head": prompt[:160],
                }
            )
    raise RuntimeError(
        f"{stage} rejected by moderation after {MAX_MODERATION_RETRIES + 1} "
        f"attempts. Last error: {last_exc}. Edit the brief to remove imagery "
        f"the filter is rejecting (last code: {last_exc.code if last_exc else '?'})."
    )


def tool_use_node(state: AgentState) -> dict[str, Any]:
    sb = state.get("storyboard", {})
    log_entries: list[dict[str, Any]] = []
    errors: list[str] = list(state.get("errors", []))
    run_id = state.get("run_id")

    # 1. Image — with moderation retry. If image fails too, the whole run can't
    # produce a still and we surface the error.
    try:
        img = _call_with_moderation_retry(
            lambda p: apis.seedream_generate(prompt=p, aspect="9:16"),
            initial_prompt=sb.get("visual_prompt", ""),
            stage="image",
            log=log_entries,
        )
        log_entries.append({"tool": "seedream", **img})
        image_url = img["url"]
    except RuntimeError as e:
        errors.append(f"image gen failed: {e}")
        log_entries.append({"tool": "seedream", "status": "failed", "error": str(e)})
        image_url = None

    # 2. Video — feed visual + motion prompts together; reference the still.
    # If moderation rejects all retries, log it and continue without video.
    visual_prompt = sb.get("visual_prompt", "") or ""
    motion_prompt = sb.get("motion_prompt", "") or ""
    combined = (
        f"{visual_prompt}\n\nCamera and motion: {motion_prompt}".strip()
        if visual_prompt
        else motion_prompt
    )
    video_url = None
    if image_url:  # only attempt video if image succeeded
        try:
            vid = _call_with_moderation_retry(
                lambda p: apis.seedance_generate(image_url=image_url, motion_prompt=p, duration_s=5.0),
                initial_prompt=combined,
                stage="video",
                log=log_entries,
            )
            log_entries.append({"tool": "seedance", **vid})
            video_url = vid["url"]
        except RuntimeError as e:
            errors.append(f"video gen blocked: {e}")
            log_entries.append({"tool": "seedance", "status": "failed", "error": str(e)})

    # 3. Voiceover (no moderation retry — TTS is text-only)
    audio_url = None
    try:
        audio = apis.seed_speech_generate(
            text=sb.get("voiceover", ""),
            voice=sb.get("voice", ""),
            locale=state.get("locale", "ar-SA"),
            run_id=run_id,
        )
        log_entries.append({"tool": "tts", **audio})
        audio_url = audio["url"]
    except Exception as e:
        errors.append(f"tts failed: {e}")
        log_entries.append({"tool": "tts", "status": "failed", "error": str(e)})

    return {
        "image_url": image_url,
        "video_url": video_url,
        "audio_url": audio_url,
        "errors": errors,
        "log": state.get("log", []) + [{"node": "tool_use", "calls": log_entries}],
    }
