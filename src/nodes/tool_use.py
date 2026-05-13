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

import re
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
- "Saudi man in thobe"    → "man in modern casual outfit"
- "mosque", "minaret", "azaan", "iftar" → cafe / kitchen / dinner table
- specific ethnicities, religions, holidays → omit or use generic descriptors
- Ramadan / Eid → "family gathering" or "celebration"
- traditional dress → "modest casual attire" / "professional outfit"
- Arabic-language UI / typography references → drop them
- "halo" / "halos" → "mist trails" / "soft glow" (Doubao reads halos as
  religious-figure imagery — saints, deities — and rejects them)

CRITICAL — preserve subject specifics from the original:
- If the scene has a "man", the rewrite MUST say "man" (not "person",
  not "model", not "spokesperson").
- If the scene has a "woman", the rewrite MUST say "woman".
- Same for age ("30+", "elderly"), action ("spraying", "applying"),
  and product role.

Diffusion models default to category stereotypes — women for
beauty/perfume scenes, men for sport/tools — so explicit gender
markers are mandatory to override category bias. Strip the
ETHNICITY ("Saudi"), keep the GENDER ("man").

Keep the lighting, framing, palette, motion direction and the product.

Return STRICT JSON: {"prompt": "<the rewritten prompt>"}. Output ONLY the JSON.
"""

SOFTEN_AGGRESSIVE = """You rewrite a video generation prompt that has been
rejected TWICE by Doubao's safety filter. Take strong action — but
preserve the SUBJECT specifics:

- REMOVE every cultural, ethnic, religious or geographic marker.
- REMOVE specific religious dress (thobe, abaya, hijab) — replace with
  "modern casual outfit" / "modest long-sleeve top".
- REMOVE all language / typography references and "halos" / religious symbols.

KEEP these things from the original:
- The subject's gender and age. A "man" stays a "man", a "woman" stays
  a "woman". Diffusion models bias toward women in beauty/perfume scenes
  and men in sport/tools — if the original says man and you remove it,
  the model generates a woman by default. Gender markers MUST survive.
- The action they're doing (spraying, drinking, holding, walking).
- The product, lighting, camera move, palette, framing.

Aim for ~40 words. Do not mention specific countries, religions, holidays,
or religious clothing — but DO mention the subject's gender and what they
are doing.

Return STRICT JSON: {"prompt": "<the rewritten prompt>"}. Output ONLY the JSON.
"""


# Cultural-marker replacements — pairs of (regex, replacement). Crucially,
# gender markers are PRESERVED while ethnicity / religion / specific dress
# is stripped. "Saudi man" → "man" (not → ""). Without this, diffusion
# models default to category stereotypes (women for beauty/perfume, men
# for tools/sport) and silently flip the subject.
#
# Order matters: multi-word phrases ("Saudi man") match BEFORE single-word
# adjectives ("Saudi") so gender survives the rewrite.
_CULTURAL_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # Gender-bearing multi-word phrases — keep gender, drop ethnicity
    (r"\bSaudi\s+man\b",       "man"),
    (r"\bSaudi\s+woman\b",     "woman"),
    (r"\bSaudi\s+mother\b",    "woman"),
    (r"\bSaudi\s+father\b",    "man"),
    (r"\bSaudi\s+family\b",    "family"),
    (r"\bMuslim\s+man\b",      "man"),
    (r"\bMuslim\s+woman\b",    "woman"),
    (r"\bMuslim\s+family\b",   "family"),
    (r"\bIslamic\s+prayer\b",  "quiet moment"),
    (r"\bArabic\s+typography\b", "elegant typography"),
    (r"\bArabic\s+copy\b",     "elegant text"),
    # Cultural attire — replace, don't strip (so subject still has clothes)
    (r"\bthobes?\b",           "modern casual outfit"),
    (r"\babayas?\b",           "modest long-sleeve outfit"),
    (r"\bhijabs?\b",           "headscarf"),
    # Religious settings
    (r"\bmosques?\b",          "modern building"),
    (r"\bminarets?\b",         "tower"),
    # Religious symbol bias triggers (Doubao reads "halos" as saint imagery)
    (r"\bhalos?\b",            "mist trails"),
    # Times / events
    (r"\bazaan\b",             "evening atmosphere"),
    (r"\biftars?\b",           "evening meal"),
    (r"\bramadans?\b",         "evening gathering"),
    (r"\bEid\b",               "celebration"),
    # Single-word ethnic / religious adjectives — strip
    (r"\bMuslim\b",            ""),
    (r"\bIslamic\b",           ""),
    (r"\bSaudi\b",             ""),
    (r"\bArabic\b",            ""),
    (r"\breligious\b",         ""),
    (r"\bprayer\b",            ""),
    (r"\bTajawal\b",           "modern font"),
)


def _strip_cultural_markers(original: str) -> str:
    """Replace culturally-loaded terms with gender-preserving neutrals.

    Used as fallback when LLM softening fails AND as the level-3 tier.

    Crucial: phrases like "Saudi man" become "man", NOT "" — diffusion
    models default to women in beauty/perfume scenes when no gender is
    specified, so dropping gender along with "Saudi" silently flips the
    subject. Same for "Saudi woman" → "woman".

    Uses word-boundary regex so "halo" doesn't half-eat "halos"; also
    collapses runs of whitespace and stray punctuation left behind so
    the output reads cleanly."""
    out = original
    for pattern, replacement in _CULTURAL_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    # Tidy up: collapse double spaces, kill orphaned " ()" or " ," that
    # the strip leaves behind (e.g. "wearing crisp white thobe)" → "wearing
    # crisp white modern casual outfit)" → fine; or "Saudi (30+,..." →
    # "(30+,..." with empty leading content cleaned by the regexes below).
    out = re.sub(r"\(\s*[,;]?\s*\)", "", out)   # empty parens
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)  # space before punctuation
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out or _GENERIC_PRODUCT_PROMPT


_GENERIC_PRODUCT_PROMPT = (
    "A clean modern product shot of the advertised item, soft daylight, "
    "neutral background, 9:16 vertical aspect ratio, no people, no specific "
    "cultural or religious markers."
)

# Product-keyword → noun phrase used in the nuclear template. When all
# softening fails, we want to keep the PRODUCT TYPE (perfume, dates, coffee...)
# in the prompt so Seedream doesn't hallucinate a random white smart-speaker
# in place of, say, a herdsman scene that was originally about fresh dates.
# Order matters: more specific phrases first so "oud perfume" beats "perfume".
_PRODUCT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("oud perfume",  "luxury oud perfume bottle"),
    ("perfume",      "perfume bottle"),
    ("cologne",      "cologne bottle"),
    ("fragrance",    "fragrance bottle"),
    ("ajwa",         "premium Ajwa dates package"),
    ("date palm",    "date palm tree"),
    ("dates",        "premium dates package"),
    ("coffee",       "specialty coffee package"),
    ("tea",          "premium tea package"),
    ("watch",        "luxury watch"),
    ("phone",        "smartphone"),
    ("laptop",       "laptop"),
    ("shoe",         "pair of shoes"),
    ("sneaker",      "pair of sneakers"),
    ("bag",          "leather handbag"),
    ("jewelry",      "jewelry piece"),
    ("necklace",     "necklace"),
    ("bracelet",     "bracelet"),
    ("car",          "car"),
    ("dress",        "designer dress"),
    ("skincare",     "skincare bottle"),
    ("cream",        "skincare cream jar"),
    ("milk",         "carton of milk"),
    ("honey",        "jar of honey"),
    ("oil",          "bottle of oil"),
    ("chocolate",    "box of chocolates"),
    ("cookie",       "package of cookies"),
)


def _detect_product_phrase(text: str) -> str | None:
    """Look for a product keyword in `text` and return a clean noun phrase
    suitable for inclusion in a product-only prompt. Returns None if no
    known keyword matches."""
    lower = text.lower()
    for keyword, phrase in _PRODUCT_KEYWORDS:
        if keyword in lower:
            return phrase
    return None


def _build_nuclear_template(original: str) -> str:
    """Compose a guaranteed-safe product-only prompt that PRESERVES the
    product type from the original. Without this, the fixed _GENERIC_PRODUCT_PROMPT
    would cause Seedream to render a random "advertised item" — typically a
    white smart-speaker-looking object — in place of the actual product.

    Example:
        in:  "An Arab male herdsman in his 30s, wearing a white thobe, gently
              collecting fresh Ajwa dates from a date palm at dawn"
        out: "A clean modern close-up product shot of premium Ajwa dates
              package, on a neutral surface with soft daylight, 9:16 vertical
              aspect ratio, no people, no specific cultural or religious
              markers."
    """
    phrase = _detect_product_phrase(original)
    if not phrase:
        return _GENERIC_PRODUCT_PROMPT
    return (
        f"A clean modern close-up product shot of a {phrase}, on a neutral "
        f"surface with soft daylight, 9:16 vertical aspect ratio, no people, "
        f"no specific cultural or religious markers."
    )


def _soften_prompt(original: str, *, stage: str, reason: str, attempt: int) -> str:
    """Rewrite a flagged prompt. Four escalation tiers:

      attempt 1 → LIGHT LLM softening (replace cultural markers with neutrals)
      attempt 2 → AGGRESSIVE LLM softening (drop people, product-only)
      attempt 3 → Deterministic regex strip of cultural markers (no LLM —
                  the LLM-based softening has had two passes already and
                  re-running it produces the same output)
      attempt 4+→ Hard fallback: generic product-only template, guaranteed
                  to clear any moderation. Loses creative specificity but
                  always produces a result.

    Falls back through the tiers if the LLM call itself errors.
    """
    if attempt >= 4:
        # Product-aware nuclear template — preserves the product type
        # (perfume / dates / coffee / ...) so Seedream doesn't substitute
        # a random "advertised item" for the user's actual product.
        return _build_nuclear_template(original)
    if attempt >= 3:
        return _strip_cultural_markers(original)

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
        from ..log import logger
        logger.warning("soften_prompt_llm_rewrite_failed", error=str(e))
    return _strip_cultural_markers(original)


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
