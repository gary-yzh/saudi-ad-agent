# ADR 004: Video duration lives in the storyboard, not in Settings

## Status

`Accepted` (2026-05-11), superseded the brief earlier design where
`video_duration` was a Settings field.

## Context

Originally `video_duration` was in Settings alongside other "global"
configs like `image_size` and `video_ratio`. Users could set a hard
duration that would override whatever the storyboard implied.

Problem: ad length is **content-driven**, not config-driven. The same
brand can run a 6-second urgency ad and a 12-second reflective ad
on the same day. Forcing 6s onto a storyboard whose shots sum to 18s
makes Seedance compress the content uncomfortably; setting 12s on a
4s storyboard pads with idle frames. Either way the customer sees a
mismatch between what the storyboard says and what the video does.

Same category error applied to `tts_emotion` — emotion is per-brief
tone, not per-brand global. A brand selling premium dates would have
`neutral` for refined product shots and `happy` for festive packaging
shots, both in the same campaign.

## Decision

Move duration into the storyboard tier. The total length comes from
`sum(shot.duration_s)` across the storyboard's shots, clamped to
Doubao Seedance 2.0's accepted [4, 15] integer range.

Likewise, remove `tts_emotion` and `tts_emotion_scale` from Settings.
TTS always renders `neutral`; tone variation is expressed through the
voiceover *text* the planner writes from the brief's TONE field.

Settings now contains **only** what's truly global: API keys / model
IDs / brand voice / ratio defaults / loudness. No per-brief creative
decision leaks into it.

## Consequences

* **Gain:** Brief ↔ storyboard ↔ rendered video are always consistent.
  Settings page got 3 fields shorter (a UX win). The Storyboard panel
  surfaces total duration with sweet-spot / capped annotations.
* **Cost:** Users lose the ability to override duration globally —
  they have to change the brief or the storyboard instead. So far no
  customer has asked for the override back.
* **Alternatives considered:** Keep Settings duration as an
  "advanced override". Rejected because it creates the same content
  mismatch we just fixed — same mistake, hidden one click deeper.
