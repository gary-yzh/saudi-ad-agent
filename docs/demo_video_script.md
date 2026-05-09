# Demo video script — `saudi-ad-agent` (≤ 3:00, English)

A timed narration with screen cues. Total target: **2:50**. Recommend recording
in OBS or QuickTime at 1080p. Cursor highlighter on. No background music.

---

## Beat sheet

| # | Time | Screen | Narration |
| --- | --- | --- | --- |
| 1 | 0:00–0:15 | README hero section | **(0:15)** Hi — I built `saudi-ad-agent`, a multi-tool LangGraph agent for a Saudi e-commerce client who wants to automate ad creative production end-to-end. In the next three minutes I'll walk through the architecture, run it live, and show how it handles a Ramadan brief. |
| 2 | 0:15–0:40 | Mermaid architecture diagram in README | **(0:25)** Five nodes in a single graph: a RAG node loads the brand manual; the planner turns the brief into a storyboard; a guardrail does a two-layer compliance check; if it fails, we loop back to the planner up to twice. On pass we hit the three Seed APIs — Seedream for the still, Seedance for the motion, Seed Speech for Arabic voiceover — then an eval node forecasts CTR and runs a final brand-safety check. |
| 3 | 0:40–1:00 | Open `data/brand_manual.md` | **(0:20)** This is the demo brand manual. Notice the constraints: no alcohol, no pork, modesty defaults, hijab as the spokesperson default, Ramadan-specific rules around fasting hours. The RAG node turns the bullet rules here into structured constraints the planner and guardrail both see. |
| 4 | 1:00–1:15 | Terminal — `python main.py` | **(0:15)** Let's run it. No API key set, so every LLM call falls through to a deterministic offline mock — the graph still runs end-to-end. (hit return) |
| 5 | 1:15–1:45 | Terminal output streams | **(0:30)** The trace shows: RAG loaded 14 brand rules, the planner produced a hook — *"Dates that taste like home"* — body copy in English, an Arabic voiceover, and visual + motion prompts for Seedream and Seedance. Guardrail passes on the first try because nothing in the storyboard hits the blocklist. Then we see three mock asset URLs: image, video, audio. |
| 6 | 1:45–2:05 | Eval panel | **(0:20)** The eval node blends a heuristic score — anchored on the brand manual's performance hints — with an LLM judge. Predicted CTR comes out at 3.2%, above our 1.5% pass threshold. The notes break down *why* — hook is six words, Arabic VO present, single-product focus. |
| 7 | 2:05–2:30 | Re-run with `--brief "Party hard with us — buy our wine selection"` | **(0:25)** Now let's stress-test the guardrail. Same agent, deliberately non-compliant brief. The keyword filter catches *wine* in the planner's first attempt; the graph loops back. Second attempt is clean. Notice the revision counter in the trace. |
| 8 | 2:30–2:50 | `outputs/runs/<latest>/storyboard.md` | **(0:20)** Each run drops a JSON trace and a human-readable storyboard markdown into `outputs/runs/`. That's it — full source, README, architecture diagram, and a smoke test in the repo. Thanks for watching. |

---

## Pre-recording checklist

- `.env` removed (or `ANTHROPIC_API_KEY` blanked) so the demo runs in
  offline-mock mode and is reproducible.
- Terminal: monospace font ≥ 16pt, dark theme, window 110 cols wide.
- Run `rm -rf outputs/runs/` before starting to keep the directory listing
  clean.
- Have these tabs/windows pre-arranged:
  1. README.md (Mermaid rendered — GitHub or a Markdown preview).
  2. `data/brand_manual.md`.
  3. Terminal in the project root.
  4. File explorer pointed at `outputs/runs/`.

## Stretch shots (only if you finish under 2:50)

- Show `src/graph.py` — six lines wire the whole topology.
- Show `tests/test_smoke.py` and run `pytest -q` to prove it passes in CI.

## Recording tips

- Speak slightly slower than feels natural — the script targets ~150 wpm.
- Don't read the bullet list verbatim — paraphrase. The bullets are timing
  anchors, not lines.
- If the second run takes more than ~5 seconds, trim it in post.
