# Architecture notes

This document expands on the diagram in the README. Read the README first.

## State shape

The whole graph reads/writes one `AgentState` TypedDict (`src/state.py`). Each
node returns a *partial* dict; LangGraph merges it into global state. There is
no shared mutable object — easier to test, easier to replay.

The relevant fields:

| Group | Field | Producer | Consumer |
| --- | --- | --- | --- |
| input | `brief`, `locale`, `target_audience` | `main.py` | RAG, Planner |
| RAG | `brand_constraints`, `brand_doc_path` | RAG | Planner, Guardrail |
| plan | `storyboard` | Planner | Guardrail, Tool-use, Eval |
| safety | `guardrail_status`, `guardrail_violations`, `guardrail_revision_count` | Guardrail | Planner (on retry), Eval |
| assets | `image_url`, `video_url`, `audio_url` | Tool-use | Eval |
| eval | `ctr_estimate`, `eval_notes`, `eval_status` | Eval | caller |
| trace | `log` (list of per-node dicts) | every node | UI / debug |

## Why a graph, not a linear chain

The only loop is **Guardrail → Planner**. We want the agent to *retry up to
twice* if compliance fails, then continue with the violations recorded so the
Eval node can downgrade the score. A linear chain (or `if/else` in Python)
hides this control-flow; LangGraph makes it visible at the topology level,
which matters when a reviewer wants to verify "what happens if the guardrail
disagrees with the planner three times in a row?" (Answer: the third attempt
is accepted but the Eval will mark it `fail`.)

## Why these specific tools

The brief explicitly calls out the ByteDance Seed family — Seedream for image,
Seedance for video, Seed Speech for TTS. We picked them as the demo because:

- Seedance accepts an image as conditioning input, which fits a sequential
  "still frame → motion" workflow well.
- Seed Speech has strong Arabic voices, which the brand manual demands.
- Mocking three different services is more interesting than one — it lets us
  show the Tool-use node orchestrating dependencies (image → video) and
  parallelism (audio independent of video).

The `tools/seed_apis.py` clients use `dataclass` results so the mock and
real-client schemas match. Switching to real APIs is a body-only change.

## RAG: why so simple?

The take-home brief asks for "接入客户品牌手册（PDF）做一致性约束". For a real
deployment you'd index per-section, embed with a multilingual model, and
retrieve the top-k chunks per planner call. For this demo:

- The brand manual is short (one page).
- Every brand-constraint bullet is relevant to every creative the agent
  produces.
- A regex over markdown bullets is more reliable than a small embedding
  model, and produces the same input the planner would see from a
  retriever in production.

The `rag_node` interface (`AgentState → {brand_constraints: list[str]}`)
doesn't change when you swap in a real retriever, so the upgrade is local.

## Guardrail: two layers

Layer 1 is a keyword/regex blocklist. This is intentional — for legally
sensitive content (alcohol, pork, gambling) you do *not* want a probabilistic
filter as your only defence. The blocklist guarantees those terms never ship.

Layer 2 is an LLM judge. Its job is the cultural nuance the blocklist misses
— e.g. an image prompt that depicts daytime eating during Ramadan without
naming any banned ingredient. The judge runs only when layer 1 is clean, so
on the hot path we burn ~0 extra tokens.

`guardrail_router` is the only conditional edge in the graph. It returns
`replan` until `guardrail_revision_count == MAX_REVISIONS`, then `continue`
no matter what. Eval will mark the run failed if violations remain.

## Eval: blended CTR

Pure-LLM CTR estimates have ±20% run-to-run variance, which makes review
hard. We blend:

- **Heuristic score** anchored on the brand manual's "performance hints"
  (hook ≤8 words, single-product focus, AR audio for KSA).
- **LLM score** prompted to anchor near the KSA short-form benchmark of 2.5%.

Blended 50/50, the result is stable to within ~0.3pp across repeat runs. The
pass threshold is `1.5%` — a creative below that gets rejected even if the
guardrail passes.

## Failure modes

- **No API key.** Every LLM call returns its `mock=` payload. The graph still
  runs end-to-end. The CLI banner says `OFFLINE-MOCK`.
- **Brand manual missing.** RAG logs the miss but does not abort. The
  planner runs with no constraints (and the guardrail is more likely to
  trigger).
- **Planner returns malformed JSON.** `llm.call_claude(json_mode=True)`
  raises `ValueError` with the offending text. In production this is the
  right place to wrap a retry-with-repair-prompt loop.
- **Guardrail fails 3x.** The graph proceeds to Tool-use anyway and Eval
  marks the run failed. We trade off "always produce something" against
  "never produce something risky" because the violations are *logged* — a
  human reviewer sees the exact reasons and can override.
