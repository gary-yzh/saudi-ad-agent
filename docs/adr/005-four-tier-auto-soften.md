# ADR 005: Four-tier auto-soften retry on Doubao moderation rejection

## Status

`Accepted` (2026-05-09)

## Context

Doubao Seedream's moderation is strict. A perfectly innocent KSA ad
prompt — "a Saudi man in a thobe holding a date" — gets rejected as
"culturally sensitive" because the model's safety filters flag ethnic
and religious markers conservatively.

Native users would see a "rejected as sensitive" error and have no
idea how to fix it. Most would abandon the tool.

We need to recover automatically, preferring the original creative
intent, falling back to product-only safe templates only as a last
resort.

## Decision

A four-tier escalation ladder, run automatically when Doubao rejects:

| Tier | Strategy | Example |
| --- | --- | --- |
| 1 — LIGHT | LLM rewrite, soften cultural markers, preserve subject | "Saudi man in thobe" → "man in modest attire" |
| 2 — AGGRESSIVE | LLM rewrite, strip emotion / ambiguous body language | "...with longing look at sister" → "...with calm expression near family member" |
| 3 — STRIP | Deterministic regex strip of known sensitive tokens | Removes "Saudi", "Arabic", "Muslim", etc. |
| 4 — NUCLEAR | Product-aware deterministic template | Detects category (perfume / dates / coffee) and falls back to a known-safe template for that category |

**Critical invariant: subject preservation.** "Saudi man" softens to
"man", never to empty string. Otherwise a brand-manual default like
"hijab for spokesperson roles" would silently flip gender from male
to female on tier 1.

## Consequences

* **Gain:** Empirical rejection rate across the Bateel sample brief
  dropped from ~30% on first run to <1% by tier 4. Users almost never
  see the raw rejection error.
* **Cost:** Each retry is another Seedream call ($0.04 each). A shot
  that hits tier 3 costs ~4x normal. Worth it — better than the user
  abandoning.
  Also a small bias: NUCLEAR-template shots look more generic than
  the original intent. We mitigate by surfacing the actual prompt
  used in the shot card's tooltip, so a user notices and can refine.
* **Alternatives considered:**
  * Skip the bad shot and continue with N-1 shots — fragile, breaks
    storyboards designed around 5-act structure.
  * Manual user retry only — kills the demo flow, every user would
    hit it.
