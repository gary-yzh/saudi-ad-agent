# ADR 001: Hand-rolled state machine for the live flow, not LangGraph

## Status

`Accepted` (2026-05-09)

## Context

The Studio is a four-step user-facing flow: Brief → Storyboard → Stills →
Video. Each step takes from a few seconds (storyboard) to a few minutes
(video) and the user needs to see partial state at every step:
intermediate storyboard cells, per-shot image status, video render
progress.

LangGraph (which we already depend on for the legacy single-shot
`/api/run` endpoint) is excellent for batched agentic flows, but its
runtime treats the graph as a single black box from the caller's view —
you `.invoke()` and wait for the end state. There's no clean way to
pause between nodes, persist partial state to a UI, or let the user
intervene mid-flow ("re-draft the storyboard with a different hook")
without leaving the graph paradigm.

## Decision

For the live UI flow, hand-roll a state machine on top of FastAPI +
SQLite. Each state transition is an explicit HTTP endpoint
(`POST /api/sessions/:sid/messages`, `POST /api/sessions/:sid/storyboard/confirm`,
`POST /api/sessions/:sid/video`); the session's current state lives on
the `sessions.state` column.

LangGraph is kept for the legacy `/api/run` one-shot path — both can
coexist.

## Consequences

* **Gain:** Each step is a normal REST call. Page reload picks up where
  the user left off (state is in SQLite). The UI can poll `/api/sessions/:sid`
  to render partial state. Adding a new step is "add a route + a state
  string"; no graph rewiring.
* **Cost:** More boilerplate — every transition needs its own handler,
  validation, state-check guard. Probably 200 LoC of orchestration we
  wouldn't have needed under LangGraph.
* **Alternatives considered:**
  * LangGraph with checkpointing — possible but the abstraction fights
    the UI requirement (each step must surface its output before the
    next runs).
  * Temporal workflow — overkill for in-process state; brings ops cost
    of running a Temporal cluster.
