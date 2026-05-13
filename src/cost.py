"""Per-asset cost estimator.

Surfaces a US-dollar figure per generated ad asset, broken down by
provider, so the team / sales / customer can see where the dollars go
and reason about unit economics. Two main use cases:

1. **Sales pitch** — "Each asset costs us ~$0.55, agencies charge
   you $5k–$15k, so the gross margin is ~99.99% at the asset level."
2. **Internal cost monitoring** — call `estimate_asset_cost(...)`
   after each generation, attach the breakdown to the video record's
   metadata, then roll up per-tenant monthly spend dashboards.

Pricing numbers below are **list prices in USD as of 2026-05**. They
shift quarterly across all three vendors (Doubao, Seedream/Seedance,
OpenSpeech). **Recalibrate against current vendor pricing pages
before quoting a customer.** This module is a sales-and-planning
estimator, not a billing source of truth.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Vendor pricing — USD list prices, 2026-05 snapshot.
# Recalibrate quarterly. Source: Volcengine Ark + ByteDance OpenSpeech
# public pricing pages.
# ---------------------------------------------------------------------------

PRICING_USD: dict[str, float] = {
    # LLM planner (Doubao Pro 32K via Volcengine Ark).
    # Qwen Plus and OpenAI gpt-4o-mini land in roughly the same band;
    # swap these two numbers if a customer uses a different provider.
    "llm_input_per_1k_tokens": 0.001,
    "llm_output_per_1k_tokens": 0.003,
    # Image — Seedream 5.0 at 2K resolution.
    "seedream_per_image": 0.04,
    # Video — Seedance 2.0; ~$0.50 for a 15-second render → $0.033/sec.
    "seedance_per_second": 0.033,
    # TTS — OpenSpeech (Doubao TTS).
    "tts_per_1k_chars": 0.015,
}


def estimate_asset_cost(
    *,
    llm_input_tokens: int = 0,
    llm_output_tokens: int = 0,
    seedream_shots: int = 0,
    seedance_seconds: int = 0,
    tts_chars: int = 0,
) -> dict[str, Any]:
    """Estimate the total USD cost of generating one ad asset.

    Returns a per-line breakdown plus the rolled-up total, all in USD,
    rounded to 4 decimal places (some lines are sub-cent — TTS and LLM
    typically are).

    The breakdown is structured so the sales team can copy-paste it into
    a customer ROI deck:

        Provider          Cost
        ---------------------------
        LLM planner       $0.005
        Seedream images   $0.16
        Seedance video    $0.40
        TTS voiceover     $0.003
        Total per asset   $0.57
    """
    llm_in = llm_input_tokens / 1000.0 * PRICING_USD["llm_input_per_1k_tokens"]
    llm_out = llm_output_tokens / 1000.0 * PRICING_USD["llm_output_per_1k_tokens"]
    seedream = seedream_shots * PRICING_USD["seedream_per_image"]
    seedance = seedance_seconds * PRICING_USD["seedance_per_second"]
    tts = tts_chars / 1000.0 * PRICING_USD["tts_per_1k_chars"]

    total = llm_in + llm_out + seedream + seedance + tts

    return {
        "llm_input_usd": round(llm_in, 4),
        "llm_output_usd": round(llm_out, 4),
        "seedream_usd": round(seedream, 4),
        "seedance_usd": round(seedance, 4),
        "tts_usd": round(tts, 4),
        "total_usd": round(total, 4),
        "_input_summary": {
            "llm_input_tokens": llm_input_tokens,
            "llm_output_tokens": llm_output_tokens,
            "seedream_shots": seedream_shots,
            "seedance_seconds": seedance_seconds,
            "tts_chars": tts_chars,
        },
    }


def sample_breakdown_bateel() -> dict[str, Any]:
    """Approximate breakdown for the bundled Bateel sample brief.

    Useful for sales decks and README customer-economics tables:
        - 1 chat turn (brief → storyboard) ~ 3.3K LLM tokens
        - 4-shot storyboard → 4 Seedream calls
        - 12s rendered video (within 4-15s Seedance window)
        - ~180-char Arabic / English voiceover
    Produces a per-asset total in the $0.50–$0.65 range — the number
    we quote when a customer asks 'what does each ad cost you'.
    """
    return estimate_asset_cost(
        llm_input_tokens=2500,
        llm_output_tokens=800,
        seedream_shots=4,
        seedance_seconds=12,
        tts_chars=180,
    )


def unit_economics_vs_agency(
    *,
    monthly_assets: int,
    agency_rate_usd_per_asset: float = 8000.0,
) -> dict[str, Any]:
    """Compare our per-asset cost to a typical agency rate.

    Used in the sales conversation: "you make N videos a month, agency
    charges $X per video, here's what you save with us."

    `agency_rate_usd_per_asset` defaults to $8k (midpoint of the
    $5k–$15k KSA market range for short-form video creative).
    """
    our_breakdown = sample_breakdown_bateel()
    our_per_asset = our_breakdown["total_usd"]
    our_monthly = our_per_asset * monthly_assets
    agency_monthly = agency_rate_usd_per_asset * monthly_assets
    savings = agency_monthly - our_monthly
    return {
        "our_per_asset_usd": round(our_per_asset, 4),
        "our_monthly_usd": round(our_monthly, 2),
        "agency_per_asset_usd": round(agency_rate_usd_per_asset, 2),
        "agency_monthly_usd": round(agency_monthly, 2),
        "monthly_savings_usd": round(savings, 2),
        "savings_pct": round(savings / agency_monthly * 100, 2) if agency_monthly else 0.0,
    }


if __name__ == "__main__":
    # Quick CLI sanity check: `python -m src.cost`
    import json as _json

    print("Bateel sample breakdown:")
    print(_json.dumps(sample_breakdown_bateel(), indent=2))
    print()
    print("10 assets/month vs agency at $8k/asset:")
    print(_json.dumps(unit_economics_vs_agency(monthly_assets=10), indent=2))
