"""End-to-end smoke test.

Runs the full LangGraph pipeline with no API key and asserts that:
- every node executes (the log has 5+ entries),
- the storyboard, asset URLs, and CTR estimate are populated,
- the deterministic guardrail catches an obviously non-compliant brief.

Run with: `pytest -q tests/`
"""
from __future__ import annotations

import os

import pytest

# Make sure offline-mock mode is active for the test.
os.environ.pop("ANTHROPIC_API_KEY", None)

from src.graph import build_graph  # noqa: E402
from src.nodes.guardrail import _keyword_check  # noqa: E402


def _initial(brief: str) -> dict:
    return {
        "brief": brief,
        "locale": "ar-SA",
        "target_audience": "Saudi adults 25-45",
        "errors": [],
        "log": [],
        "guardrail_revision_count": 0,
        "run_id": "test",
    }


def test_happy_path_runs_end_to_end():
    graph = build_graph()
    final = graph.invoke(_initial(
        "Promote our premium Ajwa dates collection for Ramadan iftar gifting."
    ))
    assert final["storyboard"]["hook"]
    assert final["image_url"].startswith("https://mock.seedream.")
    assert final["video_url"].startswith("https://mock.seedance.")
    assert final["audio_url"].startswith("https://mock.seedspeech.")
    assert 0 < final["ctr_estimate"] < 1
    # 5 nodes + tool_use logs once + every other node once = at least 5 entries
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
    # Should catch wine, beer, champagne, and the Arabic word for alcohol.
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
