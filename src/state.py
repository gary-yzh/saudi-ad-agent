"""Shared state for the LangGraph agent.

The whole pipeline reads/writes a single TypedDict so each node remains pure
(input → input + new fields). Nothing is mutated in place; LangGraph merges
the partial dict each node returns into the global state.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class Storyboard(TypedDict, total=False):
    hook: str            # opening line
    body: str            # core message
    cta: str             # call to action
    visual_prompt: str   # passed to Seedream
    motion_prompt: str   # passed to Seedance
    voiceover: str       # passed to Seed Speech
    voice: str           # voice ID (e.g. "ar-SA-female-warm")


class AgentState(TypedDict, total=False):
    # --- input ---
    brief: str
    locale: str           # e.g. "ar-SA"
    target_audience: str

    # --- RAG ---
    brand_constraints: list[str]      # bullet rules pulled from PDF
    brand_doc_path: Optional[str]

    # --- Planner ---
    storyboard: Storyboard

    # --- Guardrail ---
    guardrail_status: str             # "pass" | "fail"
    guardrail_violations: list[str]
    guardrail_revision_count: int     # how many times the planner has retried

    # --- Tool-use ---
    image_url: Optional[str]
    video_url: Optional[str]
    audio_url: Optional[str]

    # --- Eval ---
    ctr_estimate: Optional[float]     # 0.0 – 1.0
    eval_notes: list[str]
    eval_status: str                  # "pass" | "fail"

    # --- Misc ---
    run_id: str
    errors: list[str]
    log: list[dict[str, Any]]         # ordered trace for the UI / video demo
