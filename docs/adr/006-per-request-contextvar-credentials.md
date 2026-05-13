# ADR 006: Per-request contextvar for AI provider credentials

## Status

`Accepted` (2026-05-09)

## Context

Three vendor API keys (Doubao Ark, OpenAI/Qwen, OpenSpeech) are
needed by code spread across `src/llm.py`, `src/tools/bytedance_apis.py`,
`src/nodes/*`. The naïve approach — global module variables — fails
multi-tenant: customer A's request would pick up customer B's key
whenever they hit the same Python worker.

The verbose approach — pass keys as parameters through every function
in the call stack — adds noise to every signature and is forgotten by
half the call sites within a sprint.

## Decision

Use Python's `contextvars.ContextVar` to carry the per-request config
through any depth of function call stack within the same request /
thread. `src/runtime.py` exposes:

* `set_request_config(**fields)` — call at request entry, returns a token
* `reset_request_config(token)` — call at request exit (in a `finally`)
* `cfg_get(key, env_var=None, default=None)` — three-layer fallback:
  contextvar → env var → default

ContextVars are coroutine-safe and (with `copy_context()`) thread-safe
when we spawn worker threads from the FastAPI handler.

## Consequences

* **Gain:** Function signatures stay clean — `seedance_generate(...)`
  doesn't have to thread `ark_api_key` through. Multi-tenant isolation
  is guaranteed by Python's contextvars semantics.
* **Cost:**
  * Code that runs in a freshly spawned thread (e.g. ThreadPoolExecutor
    workers in `start_image_generation`) must re-establish the
    contextvar — see `_run_with_config` in `src/sessions.py`. Easy to
    forget, hard to debug when forgotten (silent fallback to default
    config).
  * Implicit data dependencies — harder to grep "where does this key
    come from".
* **Alternatives considered:**
  * `threading.local()` — same idea, less idiomatic, doesn't work with
    asyncio.
  * Pass `cfg` dict as a parameter everywhere — verbose, error-prone.
  * Global singleton — fails multi-tenant outright.
