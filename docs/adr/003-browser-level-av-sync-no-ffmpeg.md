# ADR 003: Sync voiceover with video in the browser, not via server-side ffmpeg

## Status

`Accepted` (2026-05-09)

## Context

Seedance returns a silent MP4. The voiceover from OpenSpeech is a
separate MP3. We need them played together, synchronised, in the user's
browser.

Two obvious approaches:

1. **Server-side mux** — run `ffmpeg` after both finish, combine the
   tracks into one MP4 with embedded audio, serve that.
2. **Client-side sync** — emit the MP4 and MP3 as separate files, let
   the browser play them with two HTML5 elements (`<video>` + `<audio>`)
   and synchronise their `play` / `pause` / `seek` / `volume` events
   in JavaScript.

## Decision

Client-side sync. `web/app.js:wireVoiceoverSync()` listens to the
`<video>` element's events and mirrors them on a hidden `<audio>`
element holding the voiceover.

## Consequences

* **Gain:**
  * No ffmpeg dependency on the server. The Docker image and the
    Fly.io deploy stay small.
  * Each artifact is independently downloadable — a customer can grab
    just the silent video to overlay their own VO, or just the audio
    to drop into a different cut.
  * Server-side rendering is one less thing that can fail. Both
    Doubao calls already have their own retry / error paths.
* **Cost:**
  * If voiceover ever desyncs, debugging is a JS event-listener
    problem. So far it hasn't, but a particularly aggressive browser
    sleep policy (mobile Safari background tab) could in theory cause
    drift.
  * Slightly more complex playback UI — we need a hidden `<audio>` tag
    and an explicit "voiceover unavailable" fallback when TTS fails.
* **Alternatives considered:**
  * Server-side ffmpeg — at scale, that's a CPU-heavy task per video,
    plus an ops dependency (apt install ffmpeg, container size,
    binary version drift). Not worth it for a problem the browser can
    solve in 30 lines of JS.
