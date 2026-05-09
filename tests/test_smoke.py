"""End-to-end smoke test.

The pure-function tests (keyword guardrail) always run. The full graph test
requires an OpenAI-compatible LLM key, an Ark key for image+video, and a
ByteDance OpenSpeech key for TTS — without all three, it's auto-skipped so
CI stays green without secrets.

Run with: `pytest -q tests/`
Run live: set OPENAI_API_KEY (+ OPENAI_BASE_URL / OPENAI_MODEL),
          ARK_API_KEY, and TTS_API_KEY (+ TTS_SPEAKER) before invoking.
"""
from __future__ import annotations

import os

import pytest

from src.nodes.guardrail import _keyword_check


def _live_keys_present() -> bool:
    return all(os.getenv(k) for k in ("OPENAI_API_KEY", "ARK_API_KEY", "TTS_API_KEY"))


@pytest.mark.skipif(
    not _live_keys_present(),
    reason="set OPENAI_API_KEY + ARK_API_KEY + TTS_API_KEY to run the live e2e",
)
def test_happy_path_runs_end_to_end():
    from src.graph import build_graph

    initial: dict = {
        "brief": (
            "Promote our new wireless noise-cancelling headphones. "
            "Highlight all-day comfort and crystal-clear calls. "
            "Target young professionals working in cafes."
        ),
        "locale": "en-US",
        "target_audience": "Urban professionals 25-35",
        "errors": [],
        "log": [],
        "guardrail_revision_count": 0,
        "run_id": "pytest-smoke",
    }
    final = build_graph().invoke(initial)
    assert final["storyboard"]["hook"]
    assert final["image_url"], "Seedream returned no image url"
    assert final["video_url"], "Seedance returned no video url"
    assert final["audio_url"], "TTS returned no audio path"
    assert 0 < final["ctr_estimate"] < 1
    # rag + planner + guardrail + tool_use + eval ≥ 5 entries
    assert len(final["log"]) >= 5


def test_guardrail_catches_alcohol_in_storyboard():
    bad = {
        "hook": "Party hard tonight",
        "body": "Stock up on wine and beer for the weekend.",
        "cta": "Drink up",
        "visual_prompt": "A cocktail party scene with champagne flutes.",
        "motion_prompt": "Slow pan over a bar.",
        "voiceover": "أهلاً، اشتري الخمر الآن.",
    }
    violations = _keyword_check(bad, ramadan=False)
    joined = " ".join(violations).lower()
    assert "wine" in joined
    assert "beer" in joined
    assert "champagne" in joined
    assert any("خمر" in v for v in violations)


def test_guardrail_passes_clean_storyboard():
    good = {
        "hook": "Dates that taste like home.",
        "body": "Hand-picked Ajwa from Madinah, delivered before iftar.",
        "cta": "Shop now",
        "visual_prompt": "Warm kitchen scene, emerald palette, sand light.",
        "motion_prompt": "Slow dolly-in toward a tray of dates.",
        "voiceover": "أهلاً بكم، تمر العجوة من المدينة.",
    }
    assert _keyword_check(good, ramadan=True) == []


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
