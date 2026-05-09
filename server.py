"""FastAPI server for the Saudi Ad Agent.

Live-only — no mock fallback. Three keys are required for a successful run:

| Capability | Provider                   | Field             |
| ---------- | -------------------------- | ----------------- |
| LLM        | OpenAI-compatible          | `api_key`         |
| Image+Video| Volcengine Ark (Doubao)    | `ark_api_key`     |
| TTS        | ByteDance OpenSpeech       | `tts_api_key`     |

The server mounts `outputs/runs/` at `/runs` so the TTS audio file written
by the tool-use node is reachable from the browser.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.graph import build_graph
from src.llm import active_provider
from src.runtime import reset_request_config, set_request_config

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
RUNS_DIR = ROOT / "outputs" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Saudi Ad Agent")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
app.mount("/runs", StaticFiles(directory=str(RUNS_DIR)), name="runs")

_graph = build_graph()


class RunRequest(BaseModel):
    brief: str = Field(..., min_length=10)
    locale: str = "ar-SA"
    target_audience: str = "Saudi adults 25-45, parents, urban"

    # --- LLM (OpenAI-compatible) ---------------------------------------------
    api_key: str = Field(..., min_length=1, description="OpenAI-compatible API key.")
    base_url: Optional[str] = Field(default=None, description="OpenAI-compatible base URL.")
    model: Optional[str] = Field(default=None, description="LLM model name.")

    # --- Volcengine Ark (Doubao Seedream + Seedance) -------------------------
    ark_api_key: str = Field(..., min_length=1, description="Volcengine Ark API key.")
    ark_base_url: Optional[str] = None
    image_model: Optional[str] = None
    image_size: Optional[str] = None
    image_watermark: bool = False
    video_model: Optional[str] = None
    video_ratio: Optional[str] = None
    video_duration: Optional[int] = None
    video_generate_audio: bool = False
    video_watermark: bool = False

    # --- ByteDance OpenSpeech (Doubao TTS) -----------------------------------
    tts_api_key: str = Field(..., min_length=1, description="ByteDance TTS API key.")
    tts_url: Optional[str] = None
    tts_resource_id: Optional[str] = None
    tts_speaker: Optional[str] = None
    tts_format: Optional[str] = None
    tts_sample_rate: Optional[int] = None
    tts_speech_rate: Optional[int] = None
    tts_loudness_rate: Optional[int] = None
    tts_emotion: Optional[str] = None
    tts_emotion_scale: Optional[int] = None
    tts_silence_duration: Optional[int] = None
    tts_explicit_language: Optional[str] = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/mode")
def mode() -> dict[str, str]:
    return {"provider": active_provider()}


@app.post("/api/run")
def run(req: RunRequest) -> JSONResponse:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    initial: dict[str, Any] = {
        "brief": req.brief,
        "locale": req.locale,
        "target_audience": req.target_audience,
        "brand_doc_path": None,
        "run_id": run_id,
        "errors": [],
        "log": [],
        "guardrail_revision_count": 0,
    }

    token = set_request_config(
        # LLM
        openai_api_key=req.api_key,
        openai_base_url=req.base_url,
        openai_model=req.model,
        # Ark — image
        ark_api_key=req.ark_api_key,
        ark_base_url=req.ark_base_url,
        image_model=req.image_model,
        image_size=req.image_size,
        image_watermark=req.image_watermark,
        # Ark — video
        video_model=req.video_model,
        video_ratio=req.video_ratio,
        video_duration=req.video_duration,
        video_generate_audio=req.video_generate_audio,
        video_watermark=req.video_watermark,
        # TTS
        tts_api_key=req.tts_api_key,
        tts_url=req.tts_url,
        tts_resource_id=req.tts_resource_id,
        tts_speaker=req.tts_speaker,
        tts_format=req.tts_format,
        tts_sample_rate=req.tts_sample_rate,
        tts_speech_rate=req.tts_speech_rate,
        tts_loudness_rate=req.tts_loudness_rate,
        tts_emotion=req.tts_emotion,
        tts_emotion_scale=req.tts_emotion_scale,
        tts_silence_duration=req.tts_silence_duration,
        tts_explicit_language=req.tts_explicit_language,
    )
    try:
        try:
            final = _graph.invoke(initial)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"\n=== graph failed ({type(exc).__name__}) ===\n{tb}", flush=True)
            raise HTTPException(
                status_code=500,
                detail=f"graph failed: {type(exc).__name__}: {exc}",
            ) from exc
    finally:
        reset_request_config(token)

    payload = _serialize(final)
    payload["_provider"] = active_provider()
    payload["_model"] = req.model or os.getenv("OPENAI_MODEL", "")
    payload["_image_model"] = req.image_model or "doubao-seedream-5-0-260128"
    payload["_video_model"] = req.video_model or "doubao-seedance-2-0-260128"
    payload["_tts_resource_id"] = req.tts_resource_id or "seed-tts-2.0"
    _save(run_id, payload)
    return JSONResponse(payload)


def _serialize(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state, ensure_ascii=False, default=str))


_SECRET_FIELDS = {"api_key", "ark_api_key", "tts_api_key"}


def _save(run_id: str, payload: dict[str, Any]) -> None:
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in payload.items() if k not in _SECRET_FIELDS}
    (out_dir / "run.json").write_text(
        json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
