"""Planner node — turn a customer brief + brand constraints into a storyboard.

Output is a structured JSON object (see `Storyboard` in state.py) so that
downstream nodes (Tool-use, Guardrail, Eval) don't have to re-parse free text.
"""
from __future__ import annotations

from typing import Any

from ..llm import call_claude
from ..state import AgentState

SYSTEM_PROMPT_TEMPLATE = """You are an ad creative planner for a Saudi e-commerce brand.

Given a brief, the brand constraints, and (optionally) a list of issues from
a previous attempt, produce ONE short-form vertical video concept.

Return STRICT JSON with this shape:
{{
  "hook":           "<= 8 words, English",
  "body":           "1-2 sentences, English",
  "cta":            "<= 5 words, English",
  "visual_prompt":  "image-gen prompt — describe scene, lighting, framing, palette",
  "motion_prompt":  "video-gen prompt — describe camera move + motion in 1 sentence",
  "voiceover":      "{voiceover_instruction}",
  "voice":          "voice ID, e.g. {voice_example}"
}}

Rules:
- Respect every brand constraint passed in.
- {voiceover_rule}
- No alcohol, no pork, no gambling, no comparative claims naming competitors.
- During Ramadan briefs, evoke family/generosity, not discount urgency.
- Output ONLY the JSON object, no commentary.
"""


# Map a locale prefix to the voiceover language we ask the planner to use.
# Keep this list in sync with what the chosen TTS speaker can actually pronounce.
_VOICEOVER_LANGS = {
    "ar": ("Arabic line, RTL, max 12 words", "Arabic only", "ar-SA-female-warm"),
    "en": ("English line, max 12 words", "Voiceover MUST be in English", "en-US-male-warm"),
    "zh": ("Chinese line, max 24 characters", "Voiceover MUST be in Chinese", "zh-CN-female-warm"),
    "ja": ("Japanese line, max 24 characters", "Voiceover MUST be in Japanese", "ja-JP-female-warm"),
    "es": ("Spanish line, max 12 words", "Voiceover MUST be in Spanish", "es-ES-female-warm"),
    "fr": ("French line, max 12 words", "Voiceover MUST be in French", "fr-FR-female-warm"),
}


def _system_prompt_for_locale(locale: str) -> str:
    code = (locale or "ar-SA").split("-", 1)[0].lower()
    instr, rule, example = _VOICEOVER_LANGS.get(code, _VOICEOVER_LANGS["ar"])
    return SYSTEM_PROMPT_TEMPLATE.format(
        voiceover_instruction=instr,
        voiceover_rule=rule,
        voice_example=example,
    )


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
        system=_system_prompt_for_locale(state.get("locale", "ar-SA")),
        user=user_msg,
        json_mode=True,
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
