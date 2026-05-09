"""FastAPI server for the Saudi Ad Agent.

Two pages, two flows:
  GET /          -> run.html      (chat-driven multi-step ad creation)
  GET /settings  -> settings.html (model / API config form)

Settings API:
  GET  /api/config           -> dict of saved settings
  POST /api/config           -> upsert / replace
  GET  /api/config/status    -> {configured, missing, set_keys}

Multi-step flow API (preferred — drives the new UI):
  POST /api/sessions                              create session
  GET  /api/sessions/<sid>                        full state
  POST /api/sessions/<sid>/messages               chat turn
  POST /api/sessions/<sid>/storyboard/confirm     lock + kick off image gen
  GET  /api/sessions/<sid>/images                 image gen status (poll)
  POST /api/sessions/<sid>/video                  kick off video gen with selection
  GET  /api/sessions/<sid>/video                  video status (poll)

Legacy single-shot:
  POST /api/run              one-shot; kept for the CLI (main.py)

Static:
  /static/<file>             web/ asset
  /runs/<sid>/<file>         per-session artefacts (images, video, voice)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import sessions, storage
from src.graph import build_graph
from src.runtime import reset_request_config, set_request_config

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
RUNS_DIR = ROOT / "outputs" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Saudi Ad Agent")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
app.mount("/runs", StaticFiles(directory=str(RUNS_DIR)), name="runs")

_graph = build_graph()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="saa-job")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
def page_run() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/settings")
def page_settings() -> FileResponse:
    return FileResponse(WEB / "settings.html")


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


@app.get("/api/config")
def get_config() -> JSONResponse:
    return JSONResponse(storage.load_config())


@app.post("/api/config")
def post_config(payload: dict[str, Any]) -> JSONResponse:
    storage.save_config(payload, replace_missing=True)
    return JSONResponse({"ok": True, "status": storage.status()})


@app.get("/api/config/status")
def get_config_status() -> JSONResponse:
    return JSONResponse(storage.status())


# ---------------------------------------------------------------------------
# Session-based multi-step flow
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    locale: str = "en-SA"
    target_audience: str = "Saudi adults 25-45, parents, urban"


class ChatRequest(BaseModel):
    content: str = Field(..., min_length=1)


class VideoRequest(BaseModel):
    selected_shot_ids: list[int] = Field(..., min_length=1)


def _require_keys() -> None:
    if not storage.has_required_keys():
        missing = storage.status()["missing"]
        raise HTTPException(
            status_code=400,
            detail=(
                f"Required API keys not configured: {', '.join(missing)}. "
                "Open /settings, fill in the LLM / Ark / TTS keys, save, and try again."
            ),
        )


def _session_view(session_id: str) -> dict[str, Any]:
    """Aggregate the full session state into one JSON-serializable dict."""
    s = storage.get_session(session_id)
    if s is None:
        raise HTTPException(404, f"Session {session_id} not found")
    return {
        "session": s,
        "messages": storage.list_messages(session_id),
        "shot_images": storage.list_shot_images(session_id),
        "video": storage.get_video(session_id),
    }


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest) -> JSONResponse:
    sid = sessions.new_session_id()
    storage.create_session(
        session_id=sid, locale=req.locale, target_audience=req.target_audience
    )
    return JSONResponse({"id": sid, **_session_view(sid)})


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> JSONResponse:
    return JSONResponse(_session_view(sid))


@app.post("/api/sessions/{sid}/messages")
def post_message(sid: str, req: ChatRequest) -> JSONResponse:
    _require_keys()
    s = storage.get_session(sid)
    if s is None:
        raise HTTPException(404, "Session not found")
    cfg = storage.load_config()
    token = set_request_config(**cfg)
    try:
        reply = sessions.chat_turn(sid, req.content)
    except Exception as exc:
        import traceback
        print(f"\n=== chat_turn failed ===\n{traceback.format_exc()}", flush=True)
        raise HTTPException(500, f"chat_turn failed: {type(exc).__name__}: {exc}") from exc
    finally:
        reset_request_config(token)
    return JSONResponse({"reply": reply, **_session_view(sid)})


@app.post("/api/sessions/{sid}/storyboard/confirm")
def confirm_storyboard(sid: str) -> JSONResponse:
    _require_keys()
    try:
        result = sessions.confirm_storyboard(sid)
        sessions.start_image_generation(sid, _executor)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True, **result, **_session_view(sid)})


@app.get("/api/sessions/{sid}/images")
def get_images(sid: str) -> JSONResponse:
    return JSONResponse(sessions.list_shot_statuses(sid))


@app.post("/api/sessions/{sid}/video")
def post_video(sid: str, req: VideoRequest) -> JSONResponse:
    _require_keys()
    sessions.start_video_generation(sid, req.selected_shot_ids, _executor)
    return JSONResponse({"ok": True, **_session_view(sid)})


@app.get("/api/sessions/{sid}/video")
def get_video(sid: str) -> JSONResponse:
    v = storage.get_video(sid)
    if v is None:
        return JSONResponse({"status": "none"})
    return JSONResponse(v)


# ---------------------------------------------------------------------------
# Legacy one-shot (CLI / main.py)
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    brief: str = Field(..., min_length=10)
    locale: str = "ar-SA"
    target_audience: str = "Saudi adults 25-45, parents, urban"


@app.post("/api/run")
def run(req: RunRequest) -> JSONResponse:
    _require_keys()
    cfg = storage.load_config()

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

    token = set_request_config(**cfg)
    try:
        try:
            final = _graph.invoke(initial)
        except Exception as exc:
            import traceback
            print(f"\n=== graph failed ({type(exc).__name__}) ===\n{traceback.format_exc()}", flush=True)
            raise HTTPException(500, f"graph failed: {type(exc).__name__}: {exc}") from exc
    finally:
        reset_request_config(token)

    payload = json.loads(json.dumps(final, ensure_ascii=False, default=str))
    payload["_image_model"] = cfg.get("image_model") or "doubao-seedream-5-0-260128"
    payload["_video_model"] = cfg.get("video_model") or "doubao-seedance-2-0-260128"
    payload["_tts_resource_id"] = cfg.get("tts_resource_id") or "seed-tts-2.0"
    payload["_llm_model"] = cfg.get("openai_model") or os.getenv("OPENAI_MODEL", "")

    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return JSONResponse(payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
