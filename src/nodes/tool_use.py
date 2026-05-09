"""Tool-use node — call Seedream → Seedance → Seed Speech in order.

Why sequential and not parallel? Seedance takes the still frame produced by
Seedream as its conditioning input, so we *need* the image before the video.
TTS is independent of the image, but launching it after keeps the trace easy
to read in the demo. (Switching to async + asyncio.gather is a 10-line change.)
"""
from __future__ import annotations

from typing import Any

from ..state import AgentState
from ..tools.seed_apis import (
    seed_speech_generate,
    seedance_generate,
    seedream_generate,
)


def tool_use_node(state: AgentState) -> dict[str, Any]:
    sb = state.get("storyboard", {})
    log_entries: list[dict[str, Any]] = []

    # 1. Image
    img = seedream_generate(prompt=sb.get("visual_prompt", ""), aspect="9:16")
    log_entries.append({"tool": "seedream", **img})

    # 2. Video (image-conditioned)
    vid = seedance_generate(
        image_url=img["url"],
        motion_prompt=sb.get("motion_prompt", ""),
        duration_s=6.0,
    )
    log_entries.append({"tool": "seedance", **vid})

    # 3. Voiceover
    audio = seed_speech_generate(
        text=sb.get("voiceover", ""),
        voice=sb.get("voice", "ar-SA-female-warm"),
    )
    log_entries.append({"tool": "seed_speech", **audio})

    return {
        "image_url": img["url"],
        "video_url": vid["url"],
        "audio_url": audio["url"],
        "log": state.get("log", []) + [{"node": "tool_use", "calls": log_entries}],
    }
