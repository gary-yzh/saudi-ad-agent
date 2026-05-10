# Architecture notes

This document expands on the diagrams in the README. Read the README first.

The system has **two parallel architectures** sharing the same node
implementations:

1. **Live multi-step flow** — `src/sessions.py` + the web UI. State
   machine over SQLite rows. This is what users interact with.
2. **Legacy LangGraph one-shot** — `src/graph.py` + `main.py`'s CLI
   and the preserved `POST /api/run` endpoint. Single-shot pipeline:
   brief in, finished assets out. Used for headless / scripted runs.

Both call the same `nodes/*` modules underneath (planner, rag,
guardrail, eval, tool_use). This doc focuses on the live flow because
that's the primary surface; the legacy path is documented inline in
`src/graph.py`.

---

## 1. Session state machine

```
chat ─chat_turn(brief)─→ storyboard_draft
                              │
                              ├─ chat_turn(refinement) ─→ storyboard_draft (re-emit)
                              │
                              └─ confirm_storyboard ─→ storyboard_confirmed
                                                          │
                                                          └─ start_image_gen ─→ images_running
                                                                                    │
                                                                                    └─ all shots done ─→ images_done
                                                                                                            │
                                                                                                            └─ generate_video(selected_ids) ─→ video_running
                                                                                                                                                  │
                                                                                                                                                  └─ Seedance + TTS done ─→ video_done
```

State lives on `sessions.state` in SQLite. Each transition is an
explicit API call — no implicit progression. Refresh / reload picks up
where the user left off because state + all artefacts (storyboard JSON,
shot URLs, video URL, eval result) are persisted.

| Transition | Endpoint | Side effects |
| --- | --- | --- |
| chat → storyboard_draft | `POST /api/sessions/<sid>/chat` | input guard → planner LLM → keyword guardrail → consistency-check LLM (if manual uploaded) → fixup loop once → save storyboard + assistant message + eval |
| storyboard_draft → storyboard_confirmed | `POST /api/sessions/<sid>/storyboard/confirm` | lock storyboard, queue per-shot Seedream calls in ThreadPoolExecutor |
| storyboard_confirmed → images_running → images_done | (background) | each shot independently runs through 5-attempt auto-soften loop; UI polls `GET /api/sessions/<sid>/shots` every 1.5 s |
| images_done → video_running | `POST /api/sessions/<sid>/video {selected_shot_ids}` | concatenate motion_prompts of selected shots → Seedance task → poll → on success, call TTS |
| video_running → video_done | (background) | save video.mp4 + voice.mp3 under `outputs/runs/<sid>/`; UI polls `GET /api/sessions/<sid>/video` every 3 s |

---

## 2. SQLite schema

```sql
config              -- key/value, used for API keys + model knobs
sessions            -- sid, state, created_at, updated_at, locale, target_audience, storyboard_json
messages            -- session_id, role, content, payload_json (incl. eval, brand_consistency_warnings)
shot_images         -- session_id, shot_id, status, url, error, metadata_json (auto_soften_attempts, prompt_used, refinement_history)
videos              -- session_id, status, local_url, remote_url, error, metadata_json (audio_url, duration, model, …)
brand_manuals       -- session_id, filename, sha256, raw_text (PDF → pypdf-extracted text capped at 8 KB excerpt)
brand_logos         -- session_id, filename, path on disk
```

Everything except `config` is keyed by `session_id`. The state machine
plus per-shot retry counters make sessions safely recoverable across
server restarts.

---

## 3. Per-request credentials via `contextvars`

Why this matters: the planner LLM, the three Doubao APIs, and the
brand-consistency LLM all need credentials. We do **not** want to:

- Pass credentials as parameters through every function (boilerplate +
  easy to leak via logs / errors).
- Store credentials on a global mutable object (concurrent requests
  would collide; tests would clobber each other).

Solution (`src/runtime.py`):

```python
_request_config: ContextVar[dict[str, Any]] = ContextVar("request_config", default={})

def set_request_config(cfg: dict) -> Token:
    return _request_config.set(cfg)

def cfg_get(key: str, *, env_var: str = "", default: str = "") -> str:
    cfg = _request_config.get()
    return cfg.get(key) or os.environ.get(env_var) or default
```

Each FastAPI request handler does:

```python
token = set_request_config(load_full_config_from_sqlite())
try:
    # call planner / Seedream / Seedance / TTS in any order, any thread
    ...
finally:
    reset_request_config(token)
```

`contextvars` propagate through `asyncio` and `concurrent.futures` —
worker threads inside a `ThreadPoolExecutor` see the same config the
parent request set. We exploit this for per-shot parallel image gen:
the request handler sets context, fans out 5 shot generations, each
worker thread reads the same config. No locking, no parameter passing.

---

## 4. The planner (`CHAT_SYSTEM`)

The system prompt is a state machine inside the LLM:

- After every user message, the LLM returns `{"action": "ask",
  "question": "..."}` OR `{"action": "storyboard", "summary": "...",
  "storyboard": {...}}`.
- "ask" returns a **single** clarifying question; the UI shows it as
  a chat reply, the user replies, the planner runs again with the full
  conversation history.
- "storyboard" emits hook / body / cta / voiceover + 3-6 shots, each
  with `id`, `scene` (human-readable), `visual_prompt` (sent to
  Seedream), `motion_prompt` (sent to Seedance), `duration_s`.

Two hard rules in the system prompt that reflect bugs we hit:

1. **Subject specifics must repeat from scene to visual_prompt.** If
   `scene` says "Saudi man in white thobe", `visual_prompt` MUST also
   say "Saudi man in white thobe". Without this rule, the LLM would
   compress to "person in traditional Saudi attire", Seedream would
   lose the gender signal, and beauty-category bias would default to
   women.
2. **Brand-manual modesty defaults apply only to women.** The bundled
   manual says "the default for spokesperson roles is hijab"; the
   planner must not let that flip a scene's explicit `man` to `woman`.

---

## 5. Auto-soften retry — 4 tiers

When Doubao moderation rejects an image (or video), we don't blindly
resubmit — moderation is deterministic. We escalate progressively
(`src/nodes/tool_use.py:_soften_prompt`):

| Tier | Mechanism | Cost | Preserves creativity? |
| --- | --- | --- | --- |
| **1 LIGHT** | LLM rewrite — replaces cultural markers with neutral synonyms ("Saudi mother in abaya" → "young woman in modest long-sleeve top"). Explicitly preserves gender. | 1 LLM call (~1-3 s) | High |
| **2 AGGRESSIVE** | LLM rewrite — strips cultural markers, keeps gender + action + product. | 1 LLM call (~1-3 s) | Medium |
| **3 STRIP** | Regex replacement on a curated cultural-marker list (`Saudi man → man`, `thobe → modern casual outfit`, `halos → mist trails`). No LLM call. | 0 ms | Low (deterministic) |
| **4 NUCLEAR** | Product-aware safe template — extracts the product noun (perfume, oud, dates, coffee, watch, …) from the original and emits "A clean modern close-up product shot of a [product], on a neutral surface with soft daylight, …, no people, no specific cultural or religious markers." Fallback to the generic template if no product noun is detectable. | 0 ms | None — falls back to product hero shot |

`_gen_one_shot` runs all five attempts (original + 4 tiers) before
giving up. With NUCLEAR being product-aware and deterministic, the
failure rate is essentially zero. Worst-case wait per shot is ~25 s
(5 Seedream calls + 2 LLM softenings); typical is 3-5 s (first attempt
passes).

The same machinery is wired into video gen (`_gen_video`) but capped
at MAX_VIDEO_AUTO_RETRIES = 1 because each Seedance call is 3-30 min
— more retries would push the wait over an hour.

Manual user retries (per-shot Retry button) **continue** from where
auto-retries left off via a `retry_softening_level` counter persisted
on the shot's metadata. Each click escalates 3 levels (1 from
retry_shot's softening + 2 from the inner _gen_one_shot's auto-loop).

---

## 6. Three-layer guardrail

| Layer | Where | When | Mechanism |
| --- | --- | --- | --- |
| 1 | `src/guard.py` | Before planner LLM | EN+AR keyword regex with **negation context** detection (no / not / never / without / avoid / exclude / shouldn't / isn't / ... within 80 chars before hit → skip). Two categories: `hard_ban` (alcohol, pork, gambling, drugs, weapons) and `muslim_sensitive` (intimate, dating, romantic, blasphemy). |
| 2 | `src/nodes/guardrail.py:_keyword_check` | After planner LLM, on storyboard text | Same blocklist applied to hook + cta + voiceover + visual_prompt + motion_prompt joined into a single string. Ramadan-aware (extra rules if "ramadan" in user msg). |
| 3 | Doubao Seedream / Seedance | At image / video generation | Doubao's own moderation, applied to both the input prompt and the generated output. We catch `ContentModerationError` and feed it into the 4-tier auto-soften loop. |

Layer 1 prevents wasting LLM tokens on inputs that will never produce
acceptable output. Layer 2 catches LLM "creative leaks" (the LLM
sometimes emits a banned word that the user didn't). Layer 3 is the
final safety net at the model — Doubao has stricter policies than
ours (e.g. "Saudi man + thobe + halos" looks like religious-figure
imagery to a Chinese vision model), so we react with progressive
softening.

---

## 7. Brand consistency — separate LLM judge

If the user uploaded a brand manual PDF, after the planner emits a
storyboard we run a **second LLM pass** that judges the draft against
the full manual text:

```
INPUT:
  BRAND MANUAL EXCERPT: <up to 8 KB of the user's PDF>
  PROPOSED STORYBOARD: <JSON>

OUTPUT (strict JSON):
  [{"rule": "...", "issue": "..."}, ...]
```

Returned violations trigger the planner's **fixup loop** — one extra
LLM call with the violations as context, asking the planner to re-emit
a clean storyboard. Then `_check_brand_consistency` re-runs on the
revised draft so the final payload reflects the freshest assessment.

If the LLM call itself fails, the function returns `[]` non-fatally —
storyboard ships, no fixup attempted.

The bundled demo manual (`data/brand_manual.md`, "Noor Souq") is used
as a low-pressure default when the user hasn't uploaded one — its
`_default_brand_constraints` extracts bullet-point rules and surfaces
them to the planner, but skips the consistency LLM judge to save
tokens.

---

## 8. Eval — heuristic-first by default

`src/sessions.py:_evaluate_storyboard_live` (live flow):

```python
ctr, notes = _heuristic_score(storyboard)   # deterministic
status = "pass"
if ctr < PASS_CTR_THRESHOLD:                # 1.5%
    status = "fail"
if has_violations:                          # post-fixup consistency state
    status = "fail"
return {"ctr_estimate": ctr, "eval_status": status, "eval_notes": [...]}
```

`_heuristic_score` (in `src/nodes/eval.py`) anchors on the bundled
brand manual's performance hints + KSA short-form benchmarks:

- Baseline 2.5%
- Hook ≤ 8 words: +1.0pp
- Hook > 8 words: −0.5pp
- CTA 1-5 words: +0.5pp
- Arabic voiceover detected (any char in U+0600-U+06FF): +0.4pp
- Body > 180 chars: −0.4pp
- Clamp to [0.5%, 10%]

Heuristic-only because eval runs on **every chat turn** that produces
a storyboard, including re-drafts and refinements. The legacy
LangGraph path (`src/nodes/eval.py:eval_node`) blends in an LLM judge
50/50 — useful for headless batch runs, too expensive for the live
flow.

---

## 9. Voiceover sync — browser-level, not ffmpeg

Seedance returns silent video. To get audio playing alongside, two
options:

1. **Server-side merge** — call ffmpeg to mux audio + video into a
   single mp4. Requires installing ffmpeg as a dependency, handling
   different audio codec / sample-rate combinations.
2. **Client-side sync** — separate `<video>` and `<audio>` elements,
   sync via HTML5 events. No server dependency, no codec handling.

We picked (2) (`web/app.js:wireVoiceoverSync`):

```js
videoEl.addEventListener("play",        () => audioEl.play());
videoEl.addEventListener("pause",       () => audioEl.pause());
videoEl.addEventListener("seeked",      () => audioEl.currentTime = videoEl.currentTime);
videoEl.addEventListener("ratechange",  () => audioEl.playbackRate = videoEl.playbackRate);
videoEl.addEventListener("ended",       () => audioEl.pause());
videoEl.addEventListener("volumechange",() => audioEl.muted = videoEl.muted);
```

Video is the master, audio follows. If TTS fails (e.g. speaker /
language mismatch), we serve silent video with a small "voiceover
unavailable" note instead of failing the whole step.

The TTS call itself (`src/sessions.py:_gen_video` after Seedance
success) auto-derives locale from the configured `tts_speaker` prefix
(`en_*` → `en-US`, `zh_*` → `zh-CN`, `ja_*` → `ja-JP`, ...). Brief
content drives the storyboard's voiceover language; the speaker drives
TTS rendering — keeping these in sync is the user's responsibility,
but the locale auto-derivation helps.

---

## 10. Why a separate live flow when LangGraph already exists

We could have driven the multi-step UI through the LangGraph path
with checkpoints (LangGraph supports persistent state via
`MemorySaver` / `SqliteSaver`). We didn't, because:

- LangGraph's strength is **branching control flow** (the
  guardrail-replan loop). The user-facing flow is mostly linear with
  user-gated transitions, not branching — a state machine over SQLite
  rows is simpler and more debuggable.
- Per-shot parallelism with retry is awkward to express as graph
  nodes — it's much more naturally a `ThreadPoolExecutor.submit` per
  shot with a shared retry counter on the SQLite row.
- The legacy LangGraph path is preserved for batch use cases; sharing
  the underlying nodes (`nodes/planner.py`, `nodes/eval.py`, etc.)
  means we don't duplicate logic.

The trade-off: two architectures to maintain. But each has a clear
purpose — LangGraph for headless one-shot, sessions.py for the
guided UI — and they share the heavy lifting in `nodes/*` and
`tools/*`.

---

## 11. Failure modes

| Failure | Behaviour |
| --- | --- |
| No API key | `call_claude` raises `RuntimeError("No LLM key configured.")`. Settings page red-dot indicator surfaces this; `Send` button gated. |
| Planner returns malformed JSON | `llm.call_claude(json_mode=True)` raises `ValueError`. The chat handler catches and shows a friendly retry message. |
| Brand manual upload PDF unparseable | `pypdf` failure logged; `_session_brand_excerpt` returns `None`; flow continues with the bundled default constraints. |
| All 5 Seedream attempts fail | Shot stored as `failed` with friendly error message ("Doubao kept flagging this shot... Use the Apply box to rephrase"). User can Retry (escalates further) or Refine (custom edit). |
| Seedance moderation fail | Same auto-soften pattern at video level (1 retry max). On exhaustion: "Doubao's video safety filter rejected the generated video... Re-draft the storyboard with a more product-centric brief." |
| TTS fails (speaker/language mismatch) | Video still served, with a small "⚠ Voiceover couldn't be generated" note pointing at the Settings speaker field. |
| Volcengine signed URL expires | `img.onerror` flips `.image-broken` class on; CSS collapses the broken card to a clickable strip ("↻ Image expired — click to regenerate") that re-runs Seedream for that shot. |

---

## 12. Threading + concurrency notes

- FastAPI is async; image / video gen is offloaded to
  `ThreadPoolExecutor` so the request handler returns immediately and
  the UI polls.
- All shot generation for one session runs in parallel — typical 5-shot
  storyboard renders in 5-15 s wall-clock instead of 25-75 s
  sequential.
- Sessions across users / tabs are independent — `sessionStorage`
  per-tab session id + per-request `contextvar` config means two
  concurrent users with different Doubao keys never see each other's
  credentials.
- SQLite writes are short and serialized — no read/write contention
  observed in practice. For high concurrency we'd swap to Postgres,
  but the same `storage.py` interface should hold.
