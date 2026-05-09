"""Planner node — turn a customer brief + brand constraints into a storyboard.

Output is a structured JSON object (see `Storyboard` in state.py) so that
downstream nodes (Tool-use, Guardrail, Eval) don't have to re-parse free text.

Offline-mock behaviour: returns a fully-formed sample storyboard that exercises
every downstream node. This keeps the demo runnable with no API key.
"""
from __future__ import annotations

from typing import Any

from ..llm import call_claude
from ..state import AgentState

SYSTEM_PROMPT = """You are an ad creative planner for a Saudi e-commerce brand.

Given a brief, the brand constraints, and (optionally) a list of issues from
a previous attempt, produce ONE short-form vertical video concept.

Return STRICT JSON with this shape:
{
  "hook":           "<= 8 words, English",
  "body":           "1-2 sentences, English",
  "cta":            "<= 5 words, English",
  "visual_prompt":  "image-gen prompt for Seedream — describe scene, lighting, framing, palette",
  "motion_prompt":  "video-gen prompt for Seedance — describe camera move + motion in 1 sentence",
  "voiceover":      "Arabic voiceover line, RTL, max 12 words",
  "voice":          "voice ID, e.g. ar-SA-female-warm"
}

Rules:
- Respect every brand constraint passed in.
- The voiceover MUST be in Arabic. The on-screen copy is English.
- No alcohol, no pork, no gambling, no comparative claims naming competitors.
- During Ramadan briefs, evoke family/generosity, not discount urgency.
- Output ONLY the JSON object, no commentary.
"""


_CLEAN_MOCK = {
    "hook": "Dates that taste like home.",
    "body": "Hand-picked Ajwa from Madinah, delivered to your door before Iftar.",
    "cta": "Shop now",
    "visual_prompt": (
        "Warm golden-hour shot inside a modern Riyadh kitchen. A Saudi mother "
        "in an emerald abaya places a tray of glossy Ajwa dates next to a "
        "porcelain cup of Arabic coffee. Palette: deep emerald, sand, gold. "
        "Soft daylight, shallow depth of field, 9:16 vertical."
    ),
    "motion_prompt": (
        "Slow 2-second dolly-in toward the date tray, steam rising from the "
        "coffee, gentle hand reaches into frame placing a single date."
    ),
    "voiceover": "أهلاً بكم، تمر العجوة من المدينة، وصلكم قبل المغرب.",
    "voice": "ar-SA-female-warm",
}

# Used only for offline-mock when the brief itself contains banned terms.
# Lets the demo show the guardrail loop without requiring an API key.
_NON_COMPLIANT_MOCK = {
    "hook": "Party hard with us tonight.",
    "body": "Stock up on premium wine and beer for the weekend.",
    "cta": "Drink up",
    "visual_prompt": "Cocktail party with champagne flutes and neon lighting.",
    "motion_prompt": "Slow pan over a glowing bar full of wine bottles.",
    "voiceover": "أهلاً، اشتري الخمر الآن.",
    "voice": "ar-SA-female-warm",
}

_BANNED_BRIEF_MARKERS = (
    "wine", "beer", "alcohol", "whisky", "vodka", "champagne", "cocktail",
    "party hard", "drunk", "pork", "bacon", "casino", "gamble", "bet ",
    "خمر", "كحول", "خنزير", "قمار",
)


def _pick_mock(state: AgentState) -> dict:
    """Choose between clean / non-compliant offline mocks based on the brief.

    On retry (revision_count > 0) we always return the clean mock so the
    guardrail loop terminates. This mirrors what a real LLM would do when
    given the violations as feedback.
    """
    if state.get("guardrail_revision_count", 0) > 0:
        return _CLEAN_MOCK
    brief_lc = (state.get("brief", "") or "").lower()
    if any(marker in brief_lc for marker in _BANNED_BRIEF_MARKERS):
        return _NON_COMPLIANT_MOCK
    return _CLEAN_MOCK


def planner_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: produce / revise a storyboard."""
    constraints = "\n".join(f"- {c}" for c in state.get("brand_constraints", [])) or "- (none provided)"
    revisions = state.get("guardrail_revision_count", 0)
    violations = state.get("guardrail_violations", [])

    revision_block = ""
    if revisions and violations:
        revision_block = (
            f"\n\nThis is revision #{revisions}. The previous attempt was rejected "
            f"for the following reasons. Address every one of them:\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    user_msg = (
        f"BRIEF:\n{state['brief']}\n\n"
        f"LOCALE: {state.get('locale', 'ar-SA')}\n"
        f"TARGET AUDIENCE: {state.get('target_audience', 'Saudi adults 25-45')}\n\n"
        f"BRAND CONSTRAINTS:\n{constraints}"
        f"{revision_block}"
    )

    storyboard = call_claude(
        system=SYSTEM_PROMPT,
        user=user_msg,
        json_mode=True,
        mock=_pick_mock(state),
    )

    return {
        "storyboard": storyboard,
        "log": state.get("log", []) + [
            {
                "node": "planner",
                "status": "ok",
                "revision": revisions,
                "hook": storyboard.get("hook"),
            }
        ],
    }
