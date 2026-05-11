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
| 1 | 0:00–0:20 | GitHub README hero + Mermaid architecture diagram | **(0:20)** "Hi. I built saudi-ad-agent. It turns one ad brief into a finished 9:16 short video — storyboard, images, voiceover, all step by step. It uses Doubao Seedream for images, Doubao Seedance for video, Doubao OpenSpeech for voiceover, and a regular LLM as the planner." |
| 2 | 0:20–0:40 | Scroll the README capability matrix (10 rows) | **(0:20)** "Here's what it does. The planner reads the brief. Three Doubao APIs are called for real — no mocks. You can upload a brand manual, the agent reads it first. CTR is predicted with a simple scoring rule. Three safety layers: a keyword check, a storyboard check, and auto-retry if Doubao rejects an image." |
| 3 | 0:40–0:55 | Settings page (briefly, to show config is server-side) | **(0:15)** "Settings are saved on the server. The Studio page never shows your keys. One-click chips set up Qwen, Doubao, or OpenAI. A red dot warns you only when something's missing — Apple-style, quiet when fine." |
| 4 | 0:55–1:15 | Studio page; click Load 'Bateel dates' sample | **(0:20)** "I'll load the sample brief. It has the usual sections — campaign, audience, key message, output, visuals, tone, must-haves. The brand is Bateel — a real Saudi dates company. Visuals are hand-only, no faces — date close-ups and gift-box reveals. That keeps the demo fast against Doubao's safety filters. Click Send." |
| 5 | 1:15–1:35 | Storyboard panel renders (~20-40 s) | **(0:20)** "Storyboard ready. The agent picked a few shots. Each one has a scene, a prompt for the image, and a prompt for the video. The grid below shows hook, call-to-action, voiceover on top; duration, predicted CTR, brand-safety on the bottom. All scored automatically. The chat box shrinks since most edits are short." |
| 6 | 1:35–1:50 | Click Confirm; wait for stills | **(0:15)** "Confirm. The images generate in parallel — three to five of them in about fifteen seconds. The last shot has a logo upload slot. We only put the brand logo on that last frame, the way Apple or Nike ads do — once, at the end." |
| 7 | 1:50–2:05 | Hover one shot's scene line to show visual_prompt tooltip | **(0:15)** "Hover a scene to see the exact prompt sent to Seedream. If Doubao rejects an image, the agent rewrites the prompt up to four times until it passes. Users almost never see a 'rejected' error." |
| 8 | 2:05–2:20 | Click Generate video; **CUT** in recording, resume after video renders | **(0:15)** "Pick the shots, click Generate. Seedance takes three to five minutes. I'll skip ahead in the recording." |
| 9 | 2:20–2:40 | Final video panel, press play | **(0:20)** "The final 9:16 video, with synced voiceover. Doubao TTS made the audio from the voiceover line in the storyboard. The browser plays the video and audio together — no extra server work needed." (let voiceover play 3-5 s on screen) |
| 10 | 2:40–2:55 | Back to architecture diagram | **(0:15)** "Three things to highlight. One — when Doubao rejects an image, the agent retries four times automatically. Two — each request has its own keys, so users don't see each other's credentials. Three — the CTR check is rule-based, so every reply is free. Thanks for watching." |

**Total: 2:55** — under the 3:00 cap with margin for natural pauses.

---

## What to NOT show (saves time)

- Don't walk through every Settings field — too dense for 3 minutes.
  Show the page existing and the Quick-fill chips, that's enough.
- Don't read the full storyboard aloud — say "a few shots", show the
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
