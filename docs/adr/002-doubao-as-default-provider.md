# ADR 002: Doubao (ByteDance / Volcengine) as the default AI provider

## Status

`Accepted` (2026-05-09)

## Context

We need three classes of AI calls: an LLM planner, an image generator,
and a Text-to-Speech engine. The major credible options at build time:

| Capability | Doubao (字节/火山) | OpenAI | Anthropic | Google |
| --- | --- | --- | --- | --- |
| LLM planner | Doubao Pro 32K | GPT-4o / mini | Claude Sonnet | Gemini |
| Image gen | Seedream 5.0 | DALL-E 3 | n/a | Imagen 3 |
| Video gen | Seedance 2.0 | n/a | n/a | Veo |
| TTS (Arabic) | OpenSpeech | Whisper TTS | n/a | Cloud TTS |

For our specific use case — KSA / Gulf short-form ads — Doubao wins on
three axes:

1. **Arabic dialect quality** for TTS. OpenSpeech handles Najdi and
   Hejazi variants noticeably better than the global TTS competitors,
   which tend to produce robotic Modern Standard Arabic.
2. **Unit cost**. ByteDance pricing on the China-region endpoints
   is 2-5x cheaper than comparable OpenAI / Google usage for the same
   token / image / video output.
3. **Video model availability**. Seedance is one of the few currently
   commercially usable text-to-video / image-to-video APIs at this
   quality level (Sora is gated, Veo has limited access, Pika and
   Runway are subscription-only and don't expose APIs at our tier).

## Decision

Default to Doubao Seedream + Seedance + OpenSpeech, with the LLM
planner pluggable. The planner uses an **OpenAI-compatible** interface,
so users can swap in Qwen (via Aliyun DashScope), OpenAI, or any
OpenAI-compatible model through the Settings page **without a code
change**.

## Consequences

* **Gain:** Best Arabic TTS for our target market. Strong unit
  economics — single asset cost lands around $0.50-0.60 (see
  `src/cost.py`). Cohesive stack from one vendor for image + video.
* **Cost:**
  * Vendor lock-in for image and video specifically — switching would
    mean swapping the entire generation layer. We mitigate by keeping
    the orchestration code provider-agnostic.
  * Geopolitical risk — a customer's procurement team may reject
    Chinese vendors for regulatory or PR reasons. The pluggable LLM
    helps (they can keep their planner on OpenAI), but image/video
    stay Doubao until we add a second provider.
  * Network reliability — Volcengine's China-region endpoints have
    occasionally short keep-alive sockets, which is why
    `bytedance_apis.py` uses a fresh `httpx.Client` per call.
* **Alternatives considered:**
  * All-OpenAI — fails the Arabic TTS test; no video model.
  * Self-hosted (Stable Diffusion XL + Mistral) — quality gap is too
    large for client-facing ad output today.
