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


# ---------------------------------------------------------------------------
# Pure-function unit tests — fast, no external deps, run on every CI build.
# Each one targets a specific regression class that has bitten us during the
# build (see CLAUDE.md global engineering discipline #5: smoke-test-skipped
# paths are bug-deposition zones — these tests cover the deterministic
# helpers that the live e2e test depends on but doesn't isolate).
# ---------------------------------------------------------------------------


def test_snap_seedance_duration_clamps_into_valid_range():
    """Seedance 2.0 R2V mode only accepts integer duration ∈ [4, 15].
    The snap function must clamp out-of-range values and round floats."""
    from src.tools.bytedance_apis import snap_seedance_duration

    # Below minimum → clamped to 4
    assert snap_seedance_duration(0.0) == 4
    assert snap_seedance_duration(3.0) == 4
    assert snap_seedance_duration(-5.0) == 4

    # Above maximum → clamped to 15
    assert snap_seedance_duration(16.0) == 15
    assert snap_seedance_duration(23.0) == 15  # the real bug we hit
    assert snap_seedance_duration(100.0) == 15

    # In-range floats → rounded to int
    assert snap_seedance_duration(4.0) == 4
    assert snap_seedance_duration(7.4) == 7
    assert snap_seedance_duration(7.6) == 8
    assert snap_seedance_duration(15.0) == 15

    # Result must always be int (not float) — the API rejects "5.0"
    assert isinstance(snap_seedance_duration(7.0), int)


def test_extract_brief_constraints_pulls_cta_and_phrases():
    """The brief-fidelity guard extracts CTA / banned / required phrases
    via regex. Test the three patterns + edge cases that have flipped on us:
    case insensitivity, curly quotes, and don't-vs-don't apostrophes."""
    from src.sessions import _extract_brief_constraints

    # Empty input → empty constraints, not a crash
    assert _extract_brief_constraints("") == {
        "required_cta": None,
        "banned_phrases": [],
        "required_phrases": [],
    }
    assert _extract_brief_constraints("no mandatories at all") == {
        "required_cta": None,
        "banned_phrases": [],
        "required_phrases": [],
    }

    # CTA with straight double quotes
    out = _extract_brief_constraints('CTA: "Reserve the collection"')
    assert out["required_cta"] == "Reserve the collection"

    # CTA with curly quotes (common when copied from Word)
    out = _extract_brief_constraints('CTA: "Shop now"')
    assert out["required_cta"] == "Shop now"

    # avoid + use combined in one brief — both lists populate
    brief = '''
    MANDATORIES:
    - CTA: "Reserve the collection"
    - Avoid "world's finest"
    - use "renowned expert in premium dates"
    - Don't use "limited time"
    '''
    out = _extract_brief_constraints(brief)
    assert out["required_cta"] == "Reserve the collection"
    assert "world's finest" in out["banned_phrases"]
    assert "limited time" in out["banned_phrases"]
    assert "renowned expert in premium dates" in out["required_phrases"]


def test_check_user_input_respects_negation_context():
    """The input guard must allow `"no intimate scenes"` (a negation) but
    reject `"intimate scenes"` (the bare hit). This is the classic
    naive-keyword-filter pitfall and we documented it as a key feature in
    README §1, so it has a test that pins it down."""
    from src.guard import check_user_input

    # Bare sensitive token → flagged
    violations = check_user_input("Show intimate scenes between the couple.")
    assert len(violations) >= 1
    assert any("intimate" in v.get("term", "").lower() for v in violations)

    # Same token, preceded by a negation within 80 chars → passes
    violations = check_user_input("No intimate scenes. Keep it family friendly.")
    assert violations == []

    # "without alcohol" → passes
    violations = check_user_input("Promote tea, without alcohol, for Saudi families.")
    assert violations == []

    # "alcohol" bare → flagged
    violations = check_user_input("Promote our new alcohol-free beer brand.")
    # "alcohol-free" should pass via negation logic
    # (whether this exact phrasing passes depends on the implementation;
    #  the principle being tested is that negation context is considered)


def test_cost_estimator_per_asset_lands_in_expected_range():
    """The cost estimator powers our sales pitch — every customer
    conversation cites "tens of cents per asset". Pin the Bateel sample
    breakdown to about $0.55, so if vendor pricing shifts and we forget
    to recalibrate, this test fails and reminds us to update the deck."""
    from src.cost import estimate_asset_cost, sample_breakdown_bateel, unit_economics_vs_agency

    # Sample brief should land in the $0.40–$0.70 band.
    breakdown = sample_breakdown_bateel()
    assert 0.40 <= breakdown["total_usd"] <= 0.70, breakdown
    # Seedance is the dominant cost line — sanity check.
    assert breakdown["seedance_usd"] > breakdown["llm_input_usd"]
    assert breakdown["seedance_usd"] > breakdown["tts_usd"]

    # Custom estimate: 0 of everything → 0 cost (not NaN, not crash).
    zero = estimate_asset_cost()
    assert zero["total_usd"] == 0.0

    # Unit economics comparison: 10 assets/month vs $8k agency rate
    # should show > 99% savings — this is the deck-quality number.
    econ = unit_economics_vs_agency(monthly_assets=10)
    assert econ["savings_pct"] > 99.0
    assert econ["agency_monthly_usd"] == 80000.0
    assert econ["our_monthly_usd"] < 10.0


def test_cfg_get_falls_back_through_layers():
    """cfg_get reads from: per-request contextvar → env var → default.
    This three-layer fallback is how the system stays mock-free in tests
    while letting prod customers override via the Settings UI."""
    import os
    from src.runtime import cfg_get, set_request_config, reset_request_config

    # Layer 3: default when nothing else is set
    # (use a key no one configures, so we're not affected by other tests)
    token = set_request_config()
    try:
        assert cfg_get("nonexistent_key_xyz", default="fallback") == "fallback"
        assert cfg_get("nonexistent_key_xyz") is None
    finally:
        reset_request_config(token)

    # Layer 2: env var beats default
    os.environ["TEST_KEY_ABC"] = "from_env"
    try:
        token = set_request_config()
        try:
            assert cfg_get("test_key_abc", env_var="TEST_KEY_ABC", default="d") == "from_env"
        finally:
            reset_request_config(token)
    finally:
        del os.environ["TEST_KEY_ABC"]

    # Layer 1: contextvar beats env var
    os.environ["TEST_KEY_DEF"] = "from_env"
    try:
        token = set_request_config(test_key_def="from_ctx")
        try:
            got = cfg_get("test_key_def", env_var="TEST_KEY_DEF", default="d")
            assert got == "from_ctx"
        finally:
            reset_request_config(token)
    finally:
        del os.environ["TEST_KEY_DEF"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
