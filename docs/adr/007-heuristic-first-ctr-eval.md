# ADR 007: Heuristic-first CTR prediction, no learned model in v1

## Status

`Accepted` (2026-05-09)

## Context

The take-home requires a CTR + brand-safety "eval" on every storyboard.
There are three credible architectures:

1. **Heuristic scoring** — a rule-based function with hand-picked
   features (hook word count, CTA clarity, voiceover language, body
   length) and benchmark baselines.
2. **LLM-as-judge** — ask the planner LLM to rate the storyboard 0-1
   each turn.
3. **Learned model** — train an XGBoost or similar on real campaign
   data, predict CTR.

We have zero real-campaign CTR data at v1, ruling out option 3 directly.

## Decision

Use heuristic scoring. `src/sessions.py:_evaluate_storyboard_live` +
`src/nodes/eval.py:_heuristic_score` apply rules:

* Hook ≤ 8 words: +1.0pp
* Short CTA (≤ 5 words): +0.5pp
* Arabic VO detected: +0.4pp
* Body > 180 chars: −0.4pp
* Baseline: 2.5% (KSA short-form benchmark, 2026-05)
* Clamped to [0.5%, 10%]

`eval_status` then rolls up CTR threshold + residual brand-safety
violations into one pass/fail pill the UI surfaces in the Storyboard
panel.

The legacy LangGraph path also blends in an LLM-as-judge for comparison
but the live UI flow is heuristic-only.

## Consequences

* **Gain:**
  * **Free** per chat turn — no extra LLM call, no extra latency.
  * **Deterministic** — the same storyboard always gets the same score,
    which is what brand-safety auditors want.
  * **Explainable** — the UI tooltip shows which rules fired ("Hook
    is 6 words: +1pp"). Marketers can act on this; an LLM-judge score
    is opaque.
* **Cost:**
  * The CTR isn't a real prediction. It's a scoring rule, calibrated to
    a published benchmark. Customer-facing, this is fine if we call it
    "predicted CTR" rather than implying it's a learned model.
  * Rules need recalibration as the KSA short-form benchmark moves.
* **Productionisation path:** When a customer goes live, their first
  50–100 assets feed a cold-start dataset. After that, train a
  lightweight regression model on their actual CTR data from Meta /
  TikTok. Heuristic stays as the cold-start fallback.
* **Alternatives considered:**
  * LLM-as-judge only — opaque + adds 1-2s per turn + costs $0.005
    per chat turn at scale. Rejected for the live flow; kept for the
    legacy graph as a complement.
