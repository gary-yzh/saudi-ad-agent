# Architecture Decision Records

This directory documents non-obvious architectural choices made during
the build. Each ADR is a short markdown file explaining:

* **Context** — what problem we faced and what constraints applied
* **Decision** — what we picked
* **Consequences** — what we gained, what we gave up, what we'd
  reconsider if facts change

The format is loosely based on Michael Nygard's
[original ADR proposal](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
Short over long — 100-300 words per ADR. If a decision needs more,
it probably needs more thinking first.

## Index

| # | Title | Status |
| --- | --- | --- |
| [001](001-handrolled-state-machine-vs-langgraph.md) | Hand-rolled state machine for the live flow, not LangGraph | Accepted |
| [002](002-doubao-as-default-provider.md) | Doubao as the default AI provider | Accepted |
| [003](003-browser-level-av-sync-no-ffmpeg.md) | Sync voiceover with video in the browser, not server-side ffmpeg | Accepted |
| [004](004-duration-in-storyboard-not-settings.md) | Video duration lives in the storyboard, not Settings | Accepted |
| [005](005-four-tier-auto-soften.md) | Four-tier auto-soften retry on Doubao moderation rejection | Accepted |
| [006](006-per-request-contextvar-credentials.md) | Per-request contextvar for AI provider credentials | Accepted |
| [007](007-heuristic-first-ctr-eval.md) | Heuristic-first CTR prediction, no learned model in v1 | Accepted |
| [008](008-fernet-encryption-at-rest.md) | Fernet symmetric encryption for API keys at rest | Accepted |

## Writing a new ADR

1. Copy `000-template.md` to `<NNN>-<short-slug>.md` (next sequential
   number).
2. Fill in Context / Decision / Consequences.
3. Update this README's index table.
4. Commit with a message like `docs(adr): NNN - title`.

If a later decision supersedes an earlier one, set the old ADR's
Status to `Superseded by ADR-XXX` rather than deleting it. The trail
of why we changed our mind is often more valuable than the new
decision itself.
