# Demo video script — `saudi-ad-agent` (≤ 3:00, English)

A timed narration with screen cues for the **live multi-step web UI**.
Total target: **2:55** (5-second cushion under the 3:00 cap).

Recommended tools (pick whichever is fastest for you):

| Tool | Pros | Link |
| --- | --- | --- |
| **Loom** | One-click record, auto-share URL, free 5-min cap | <https://loom.com> |
| OBS Studio | Free, full control, local mp4 | <https://obsproject.com> |
| Windows Game Bar | Built-in (Win+G), zero install | (system) |
| ScreenPal | Free with watermark | <https://screenpal.com> |

Cursor highlighter on. No background music. 1080p / 1920×1080.

---

## Pre-recording checklist

- [ ] Three API keys saved in Settings (LLM + Ark + TTS) so they don't
      appear on screen. Settings link should have **no red dot** —
      means everything's configured.
- [ ] `python -m uvicorn server:app --host 127.0.0.1 --port 8000`
      running.
- [ ] Browser at <http://127.0.0.1:8000/>, fullscreen, **bookmarks
      bar hidden**, browser zoom 100%.
- [ ] Two tabs only: the running app and the GitHub README (open in
      a separate window so you can switch quickly without flashing).
- [ ] Mic test — speak into the recorder for 5 s, confirm levels.
- [ ] **Dry run twice** before the final take. The flow has 3-30 min
      of waiting (Seedance) — record the storyboard + stills phase
      live, then **CUT** and resume after the video has rendered.

---

## Beat sheet

| # | Time | Screen | Narration |
| --- | --- | --- | --- |
| 1 | 0:00–0:20 | GitHub README hero + Mermaid architecture diagram | **(0:20)** "Hi — I built `saudi-ad-agent`, a multi-step web agent that turns one ad brief into a finished 9:16 short-form video — storyboard, stills, voiceover, all in one guided flow. Live calls to Doubao Seedream for image, Seedance for video, OpenSpeech for TTS, with an OpenAI-compatible LLM as the planner." |
| 2 | 0:20–0:40 | Scroll the README capability matrix | **(0:20)** "Five capabilities are wired in. Planner decomposes the brief. Tool-use orchestrates three real Doubao APIs. RAG injects an uploaded brand manual into the planner prompt. Eval forecasts CTR with a deterministic heuristic. And three layers of guardrail — input keyword guard with negation context, post-storyboard scan, and Doubao's own moderation with progressive auto-soften retry." |
| 3 | 0:40–0:55 | Settings page (briefly, to show config is server-side) | **(0:15)** "Settings are server-side in SQLite. The Console page never sees keys. Quick-fill chips configure Qwen, Doubao, or OpenAI in one click. The red-dot indicator on the Settings link only appears when something needs attention — Apple-style, silent in the steady state." |
| 4 | 0:55–1:15 | Console page; click Load 'Bateel dates' sample | **(0:20)** "I'll load the sample brief. Industry creative-brief format — campaign, objective, audience, key message, deliverable, visual direction, mandatories. The brand is Bateel — a real Saudi premium-dates retailer — but the visual direction is product-only: glossy date close-ups, gold gift box reveals, no people. That keeps the demo fast against Doubao's image and video moderation. Click Send." |
| 5 | 1:15–1:35 | Storyboard panel renders (~3-5 s) | **(0:20)** "Three seconds — Storyboard ready. The agent picked five shots, each with a scene description, a visual prompt for Seedream, a motion prompt for Seedance. Below, predicted CTR is 3.6%, brand-safety pass — heuristic-only, computed on every chat turn for free. The chat input collapses to compact mode now since most refines are short." |
| 6 | 1:35–1:50 | Click Confirm; wait for stills | **(0:15)** "Confirm. Per-shot Seedream calls fan out in parallel. Five shots in roughly 12 seconds. The last shot — the sign-off frame — has a brand-logo upload slot; we only composite the logo there, not on every still, the way Apple or Nike short-form ads resolve their brand mark once at the end." |
| 7 | 1:50–2:05 | Hover one shot's scene line to show visual_prompt tooltip | **(0:15)** "Hovering the scene shows the actual prompt sent to Seedream — useful for debugging when a shot drifts from the script. If Doubao moderation rejects, the agent runs four progressive softening tiers automatically, all the way to a product-aware safe template. Users almost never see a 'rejected as sensitive' state." |
| 8 | 2:05–2:20 | Click Generate video; **CUT** in recording, resume after video renders | **(0:15)** "Pick the shots, click Generate. Seedance is async — typically 3 to 5 minutes. I'll cut forward in the recording." |
| 9 | 2:20–2:40 | Final video panel, press play | **(0:20)** "Final 9:16 video, with the voiceover synced. Doubao TTS rendered the script's voiceover line; the browser plays them in sync via two HTML elements — no server-side ffmpeg required." (let voiceover play 3-5 s on screen) |
| 10 | 2:40–2:55 | Back to architecture diagram | **(0:15)** "Three engineering decisions worth flagging: progressive auto-soften with four tiers from LLM rewrite to deterministic product-only template; per-request contextvar so concurrent users don't leak credentials; and heuristic-first eval so every storyboard turn is free, deterministic, and explainable. Thanks for watching." |

**Total: 2:55** — under the 3:00 cap with margin for natural pauses.

---

## What to NOT show (saves time)

- Don't walk through every Settings field — too dense for 3 minutes.
  Show the page existing and the Quick-fill chips, that's enough.
- Don't read the full storyboard aloud — say "five shots", show the
  panel scrolling, move on.
- Skip the brand-manual upload flow unless time permits — mention it
  exists ("RAG injects the manual into the planner prompt").
- Don't demonstrate Apply / Retry on a shot — just point them out as
  affordances ("each shot has refine and retry — covered in the
  README").

---

## Common edits if you go over

- Cut beat 3 (Settings) by 5 s — just a 5-second flash of the page,
  no narration over it.
- Replace beat 7 (hover tooltip + softening explanation) with a
  shorter 8-second version: "Each shot has hover-to-see-prompt for
  debugging."
- If the video render took longer than expected and you can't trim
  the cut clean — record the final beat 9 + 10 against the finished
  video as a still frame, narrate over it.

---

## Backup beats (if you have time to spare under 2:55)

- **Re-draft button** — show clicking ↻ Re-draft on the storyboard
  panel; the agent emits a fresh storyboard variant. Demonstrates that
  the planner is conversational.
- **Brand manual upload** — drop a PDF, watch the chip flip to "✓
  filename"; mention that the planner will read it before the next
  storyboard.
- **Multi-tab** — `+ New session` opens a clean tab without disturbing
  the original; per-tab `sessionStorage` keeps them independent.

---

## Submission

Upload the recording somewhere with a public URL:

- **Loom** — automatically gives you a `loom.com/share/...` URL after
  recording finishes. Set "Anyone with the link" can view.
- **Drive / Dropbox** — share link with view permissions.
- **YouTube** — set to "Unlisted" if you don't want it indexed.

Submit two URLs to the interviewer:

- GitHub repo: `https://github.com/<you>/saudi-ad-agent`
- Demo video: `https://loom.com/share/...`
