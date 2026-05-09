"""Eval node — predict CTR + run a final brand-safety self-check.

Why have an Eval node when we already have a Guardrail?
- Guardrail is a *gate* (binary pass/fail, runs before tool calls).
- Eval is a *forecast* (numeric CTR estimate, runs after the artefact exists).

Two scoring components, blended:

1. Heuristic score, derived from the brand manual's "performance hints" and
   widely-cited Saudi MENA short-form benchmarks. Cheap, deterministic.
2. LLM score, where Claude rates the creative on a 0-100 scale and explains
   itself. Falls back to the heuristic when running offline.

Output:
- `ctr_estimate`  ∈ [0.0, 1.0]   (interpret as predicted CTR ratio, e.g. 0.038)
- `eval_notes`    list[str]       human-readable rationale lines
- `eval_status`   "pass" | "fail" — fail if CTR < 0.015 or guardrail violations remain
"""
from __future__ import annotations

from typing import Any

from ..llm import call_claude
from ..state import AgentState

PASS_CTR_THRESHOLD = 0.015  # 1.5% — typical KSA short-form floor for an "ok" creative


# -- Heuristic ------------------------------------------------------------------


def _heuristic_score(storyboard: dict[str, Any]) -> tuple[float, list[str]]:
    """Returns (predicted_ctr_0_to_1, notes)."""
    notes: list[str] = []
    score = 0.025  # baseline 2.5% CTR

    hook = storyboard.get("hook", "") or ""
    body = storyboard.get("body", "") or ""
    cta = storyboard.get("cta", "") or ""
    voiceover = storyboard.get("voiceover", "") or ""

    # Hook length — brand manual says <8 words wins.
    hook_words = len(hook.split())
    if 1 <= hook_words <= 8:
        score += 0.010
        notes.append(f"Hook is {hook_words} words (≤8) → +1.0pp")
    else:
        score -= 0.005
        notes.append(f"Hook is {hook_words} words (>8) → -0.5pp")

    # CTA present and short
    if 1 <= len(cta.split()) <= 5:
        score += 0.005
        notes.append("Short, clear CTA → +0.5pp")

    # Bilingual: Arabic VO + English on-screen, per brand voice
    if voiceover and any("؀" <= ch <= "ۿ" for ch in voiceover):
        score += 0.004
        notes.append("Arabic voiceover detected → +0.4pp")
    else:
        notes.append("No Arabic voiceover detected — KSA audiences expect AR audio")

    # Body length sanity
    if len(body) > 180:
        score -= 0.004
        notes.append("Body copy >180 chars — risks viewer drop-off")

    score = max(0.005, min(0.10, score))
    return round(score, 4), notes


# -- LLM judge ------------------------------------------------------------------

LLM_EVAL_SYSTEM = """You are a media-buying analyst forecasting short-form
video CTR for the Saudi (KSA) market on Snap / TikTok / Meta Reels.

Given the creative below, return STRICT JSON:
{
  "ctr_pct": <number 0-10>,        // predicted CTR percentage, e.g. 3.4
  "rationale": ["short bullet", ...] // 2-4 reasons
}

Anchor your estimate near the KSA short-form benchmark of ~2.5% CTR. Adjust up
for strong cultural fit, clear single-product focus, hook under 8 words, and
Arabic audio. Adjust down for verbose copy, weak CTA, or off-brand visuals.
Output ONLY JSON."""


def _llm_score(storyboard: dict[str, Any]) -> dict[str, Any]:
    user = (
        f"hook: {storyboard.get('hook')}\n"
        f"body: {storyboard.get('body')}\n"
        f"cta: {storyboard.get('cta')}\n"
        f"visual: {storyboard.get('visual_prompt')}\n"
        f"motion: {storyboard.get('motion_prompt')}\n"
        f"voiceover (AR): {storyboard.get('voiceover')}"
    )
    return call_claude(
        system=LLM_EVAL_SYSTEM,
        user=user,
        json_mode=True,
        max_tokens=400,
        # In offline mode, return a result that mirrors the heuristic
        mock={"ctr_pct": 3.4, "rationale": [
            "Hook is concise and culturally warm",
            "Arabic voiceover matches KSA audio expectations",
            "Single-product focus (Ajwa dates) avoids mosaic underperformance",
        ]},
    )


# -- Node -----------------------------------------------------------------------


def eval_node(state: AgentState) -> dict[str, Any]:
    sb = state.get("storyboard", {})

    heur_ctr, heur_notes = _heuristic_score(sb)
    llm_result = _llm_score(sb)
    llm_ctr = float(llm_result.get("ctr_pct", heur_ctr * 100)) / 100.0

    # Blend 50/50 — keeps results stable when the LLM is offline-mocked.
    blended = round((heur_ctr + llm_ctr) / 2, 4)

    notes = (
        [f"Heuristic CTR: {heur_ctr:.2%}"]
        + heur_notes
        + [f"LLM CTR: {llm_ctr:.2%}"]
        + [f"LLM: {r}" for r in llm_result.get("rationale", [])]
        + [f"Blended CTR estimate: {blended:.2%}"]
    )

    status = "pass"
    if blended < PASS_CTR_THRESHOLD:
        status = "fail"
        notes.append(f"Below pass threshold of {PASS_CTR_THRESHOLD:.1%}")
    if state.get("guardrail_violations"):
        status = "fail"
        notes.append("Guardrail violations not fully resolved")

    return {
        "ctr_estimate": blended,
        "eval_notes": notes,
        "eval_status": status,
        "log": state.get("log", []) + [
            {"node": "eval", "ctr": blended, "status": status}
        ],
    }
