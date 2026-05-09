# Demo video script — `saudi-ad-agent` (≤ 3:00, English)

A timed narration with screen cues for the **live web-UI build**. Total
target: **2:45**. Recommend Loom (one-click record + share) or OBS at
1080p. Cursor highlighter on. No background music.

---

## Beat sheet

| # | Time | Screen | Narration |
| --- | --- | --- | --- |
| 1 | 0:00–0:15 | GitHub README hero | **(0:15)** Hi — I built `saudi-ad-agent`, a multi-tool LangGraph agent that turns one customer brief into a finished short-form ad — script, image, video, voiceover, and a CTR forecast — by orchestrating Doubao Seedream, Seedance and TTS. |
| 2 | 0:15–0:45 | Mermaid architecture diagram in README | **(0:30)** Five nodes wired in LangGraph. RAG loads the brand manual. The planner — driven by an OpenAI-compatible LLM — turns the brief into a storyboard. A two-layer guardrail does a keyword scan plus an LLM judge for KSA cultural compliance and loops back up to twice if it fails. On pass we hit the three real Volcengine APIs in sequence. Eval blends a heuristic with an LLM judge to predict CTR. |
| 3 | 0:45–1:00 | Web UI homepage | **(0:15)** The user-facing layer is a FastAPI server with a single-page web UI. Three collapsible sections cover the LLM, the Ark image+video keys, and the OpenSpeech TTS keys — all stored in localStorage, never on the server, and the Generate button is gated until all three are set. |
| 4 | 1:00–1:15 | Paste sample brief into textarea, click Generate | **(0:15)** Let's run it on a Ramadan dates brief. (paste, click Generate) The pipeline animates through each stage. Image lands in about thirty seconds, video typically takes three to five minutes. |
| 5 | 1:15–2:00 | **CUT FORWARD** in the recording — show the result panel after the video succeeded | **(0:45)** All three live assets rendered: the still from Seedream, the video from Seedance with playback controls, and the voiceover from Doubao TTS, served straight off our FastAPI host under `/runs/<id>/voice.mp3`. Predicted CTR comes out at three-point-six percent. Look at the moderation log: Doubao's safety filter rejected the first video output — Saudi-specific imagery is touchy — so the agent automatically asked the LLM to rewrite the prompt with neutral substitutes, retried, and the second softened version passed. |
| 6 | 2:00–2:30 | Scroll through the storyboard table + eval notes | **(0:30)** The storyboard, the three live asset URLs, and the eval rationale all show in the same panel. The Arabic-aware fields use right-to-left rendering with Tajawal. Each run also writes a JSON trace and the saved voice file under `outputs/runs/<id>/`. |
| 7 | 2:30–2:45 | Back to the architecture diagram | **(0:15)** Three design choices worth flagging: per-request contextvar so concurrent users don't leak each other's keys; auto-retry with progressive prompt softening for safety-filter rejections; and graceful degradation if any one stage hard-fails. Thanks for watching. |

---

## Pre-recording checklist

- Three API keys already saved in browser localStorage so you don't show
  them on screen: LLM (`api_key` + `base_url` + `model`), Ark
  (`ark_api_key`), TTS (`tts_api_key` + `tts_speaker`).
- Server running: `python -m uvicorn server:app --host 127.0.0.1 --port 8000`.
- Browser at 1920×1080, zoom 110-125%, **bookmarks bar hidden**.
- Two tabs only: the running app (`http://127.0.0.1:8000/`) and the
  rendered README on GitHub.
- Close Slack / Discord / WeChat / anything that can pop up.
- Mic level checked.
- `outputs/runs/` cleared if you want a clean directory shot.
- **Trim the video-gen wait in post.** Recording the full 3-5 min wait
  on Seedance is a waste — pause / cut.

## Recording tips

- Speak slightly slower than feels natural — the script targets ~150 wpm.
- Don't read the bullet list verbatim — paraphrase. The bullets are
  timing anchors, not lines.
- Record audio + screen in one pass; if you flub, re-do that beat in
  isolation and splice.
- Loom: hit "Trim" to clip out the long video-gen wait without re-recording.

## Hosting + submission

- **Loom** (recommended): record → click "Share link" → done. Set
  privacy to "Anyone with the link".
- **YouTube unlisted**: upload, set Visibility = Unlisted, copy the URL.
- **Direct file**: export 1080p MP4 (≤ 200 MB), attach to email.

Submit:
1. GitHub repo URL
2. Demo video URL (Loom / YouTube unlisted / file attachment)
