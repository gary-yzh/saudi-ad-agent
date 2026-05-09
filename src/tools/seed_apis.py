"""Mock clients for ByteDance Seed APIs.

The real APIs (Seedream image-gen, Seedance video-gen, Seed Speech TTS) require
private credentials. For this demo we ship deterministic stubs that:

- Accept the same arguments the real client would.
- Return URLs / IDs that look real and are content-addressable (sha1 of input
  → suffix), so reruns are reproducible and easy to diff in logs.
- Honour env vars `SEEDREAM_MOCK`, `SEEDANCE_MOCK`, `SEED_SPEECH_MOCK`. If set
  to "0", the function raises so a developer wiring real credentials gets a
  loud error instead of silent mocking.

To swap in a real client: replace the body of each function and keep the
signature. Nothing else in the agent has to change.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, asdict
from typing import Any


def _mock_enabled(var: str) -> bool:
    return os.getenv(var, "1") == "1"


def _hash(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return h


# -- Seedream (image) ----------------------------------------------------------


@dataclass
class SeedreamResult:
    url: str
    width: int
    height: int
    seed: int
    latency_ms: int


def seedream_generate(prompt: str, *, aspect: str = "9:16") -> dict[str, Any]:
    if not _mock_enabled("SEEDREAM_MOCK"):
        raise NotImplementedError("Real Seedream client is not wired up.")
    h = _hash("seedream", prompt, aspect)
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    res = SeedreamResult(
        url=f"https://mock.seedream.bytedance.com/img/{h}.png",
        width=width,
        height=height,
        seed=int(h[:6], 16),
        latency_ms=820,
    )
    time.sleep(0.05)  # tiny pause so the demo log feels real
    return asdict(res)


# -- Seedance (video) ----------------------------------------------------------


@dataclass
class SeedanceResult:
    url: str
    duration_s: float
    fps: int
    resolution: str
    latency_ms: int


def seedance_generate(
    *, image_url: str, motion_prompt: str, duration_s: float = 6.0
) -> dict[str, Any]:
    if not _mock_enabled("SEEDANCE_MOCK"):
        raise NotImplementedError("Real Seedance client is not wired up.")
    h = _hash("seedance", image_url, motion_prompt, str(duration_s))
    res = SeedanceResult(
        url=f"https://mock.seedance.bytedance.com/vid/{h}.mp4",
        duration_s=duration_s,
        fps=24,
        resolution="1080x1920",
        latency_ms=4200,
    )
    time.sleep(0.1)
    return asdict(res)


# -- Seed Speech (TTS) ---------------------------------------------------------


@dataclass
class SeedSpeechResult:
    url: str
    voice: str
    duration_s: float
    latency_ms: int


def seed_speech_generate(
    *, text: str, voice: str = "ar-SA-female-warm"
) -> dict[str, Any]:
    if not _mock_enabled("SEED_SPEECH_MOCK"):
        raise NotImplementedError("Real Seed Speech client is not wired up.")
    h = _hash("seedspeech", text, voice)
    # ~2 chars per syllable, ~5 syllables/sec → rough duration estimate
    dur = max(2.0, round(len(text) / 12, 1))
    res = SeedSpeechResult(
        url=f"https://mock.seedspeech.bytedance.com/tts/{h}.mp3",
        voice=voice,
        duration_s=dur,
        latency_ms=380,
    )
    time.sleep(0.03)
    return asdict(res)
