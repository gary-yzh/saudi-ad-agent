"""Guardrail node — Arabic / Islamic cultural compliance check.

Two layers, run in this order so the cheap one short-circuits the expensive one:

1. Deterministic keyword filter (English + Arabic). Catches the obvious stuff —
   alcohol/pork/gambling lexicon, Ramadan-sensitive terms, comparative claims.
2. LLM judge (Claude). Catches cultural nuance the keyword list misses
   (e.g. an image prompt that implies eating during fasting hours without using
   any banned word). Falls back to "pass with note" when run offline so demos
   stay deterministic.

Output:
- guardrail_status: "pass" | "fail"
- guardrail_violations: list[str] explaining each hit
- The graph routes back to the Planner on "fail" up to MAX_REVISIONS times.
"""
from __future__ import annotations

import re
from typing import Any

from ..llm import call_claude
from ..state import AgentState

MAX_REVISIONS = 2

# --- Layer 1: keyword filters --------------------------------------------------

# Word-boundary lookups; case-insensitive.
EN_BANNED = [
    r"\balcohol(ic)?\b", r"\bbeer\b", r"\bwine\b", r"\bwhisky\b",
    r"\bvodka\b", r"\bchampagne\b", r"\bcocktail\b", r"\bdrunk\b",
    r"\bpork\b", r"\bbacon\b", r"\bham\b(?!burger)",  # allow "hamburger"
    r"\bcasino\b", r"\bgambl(e|ing)\b", r"\bbet\b", r"\blottery\b",
    r"\bsin\b", r"\bcheap\b", r"\bguaranteed\b",
]
# Arabic banned terms (alcohol, pork, gambling).
AR_BANNED = [
    "خمر", "كحول", "نبيذ", "بيرة", "ويسكي",
    "خنزير", "لحم خنزير",
    "قمار", "كازينو", "رهان", "يانصيب",
]
# Ramadan-sensitive words to flag *only* when the brief mentions Ramadan.
RAMADAN_FLAGS_EN = [r"\bdiscount frenzy\b", r"\beat now\b", r"\bdrink up\b"]


def _scan(text: str, patterns: list[str], *, regex: bool) -> list[str]:
    hits: list[str] = []
    for p in patterns:
        if regex:
            if re.search(p, text, re.IGNORECASE):
                hits.append(p)
        else:
            if p in text:
                hits.append(p)
    return hits


def _keyword_check(storyboard: dict[str, Any], ramadan: bool) -> list[str]:
    """Return a list of human-readable violations."""
    violations: list[str] = []
    haystack_en = " ".join(
        str(storyboard.get(k, "")) for k in ("hook", "body", "cta", "visual_prompt", "motion_prompt")
    )
    haystack_ar = str(storyboard.get("voiceover", ""))

    for hit in _scan(haystack_en, EN_BANNED, regex=True):
        violations.append(f"English copy contains banned term: {hit}")
    for hit in _scan(haystack_ar, AR_BANNED, regex=False):
        violations.append(f"Arabic voiceover contains banned term: {hit}")
    if ramadan:
        for hit in _scan(haystack_en, RAMADAN_FLAGS_EN, regex=True):
            violations.append(f"Ramadan-sensitive phrasing: {hit}")
    return violations


# --- Layer 2: LLM judge --------------------------------------------------------

JUDGE_SYSTEM = """You are a Saudi advertising compliance reviewer.
Decide if the supplied ad creative would pass review for a KSA campaign,
considering Islamic cultural norms, GAMR advertising rules, and the brand's
own constraints. Be strict but not paranoid — natural family scenes, modest
fashion, and food shots that aren't haram are fine.

Return STRICT JSON:
{ "ok": true|false, "violations": ["short reason", ...] }

Output ONLY the JSON object."""


def _llm_judge(storyboard: dict[str, Any], constraints: list[str]) -> dict[str, Any]:
    user = (
        "AD CREATIVE:\n"
        f"hook: {storyboard.get('hook')}\n"
        f"body: {storyboard.get('body')}\n"
        f"cta:  {storyboard.get('cta')}\n"
        f"visual: {storyboard.get('visual_prompt')}\n"
        f"motion: {storyboard.get('motion_prompt')}\n"
        f"voiceover (AR): {storyboard.get('voiceover')}\n\n"
        "BRAND CONSTRAINTS:\n" + "\n".join(f"- {c}" for c in constraints[:30])
    )
    return call_claude(
        system=JUDGE_SYSTEM,
        user=user,
        json_mode=True,
        max_tokens=400,
    )


# --- Node ---------------------------------------------------------------------


def guardrail_node(state: AgentState) -> dict[str, Any]:
    storyboard = state.get("storyboard", {})
    brief = state.get("brief", "")
    is_ramadan = "ramadan" in brief.lower() or "رمضان" in brief

    violations = _keyword_check(storyboard, ramadan=is_ramadan)

    # Only burn an LLM call if the cheap layer is happy.
    if not violations:
        verdict = _llm_judge(storyboard, state.get("brand_constraints", []))
        if not verdict.get("ok", True):
            violations.extend(verdict.get("violations", []) or ["LLM judge flagged the creative"])

    revisions = state.get("guardrail_revision_count", 0)
    status = "pass" if not violations else "fail"

    return {
        "guardrail_status": status,
        "guardrail_violations": violations,
        # Increment revision counter only when we *fail* — the planner uses this
        # to know it's been asked to retry.
        "guardrail_revision_count": revisions + (1 if status == "fail" else 0),
        "log": state.get("log", []) + [
            {
                "node": "guardrail",
                "status": status,
                "violations": violations,
                "revision": revisions,
            }
        ],
    }


def guardrail_router(state: AgentState) -> str:
    """Edge function: 'replan' loops back to the planner, 'continue' moves on."""
    if state.get("guardrail_status") == "pass":
        return "continue"
    if state.get("guardrail_revision_count", 0) >= MAX_REVISIONS:
        # Out of retries — give up on revising and continue with violations
        # logged. The Eval node will downgrade the score.
        return "continue"
    return "replan"
