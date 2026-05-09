"""FastAPI server for the Saudi Ad Agent.

Two pages:
  GET /          -> run.html      (brief input + result + pipeline timeline)
  GET /settings  -> settings.html (model / API config form)

API:
  GET  /api/config         -> dict of saved settings
  POST /api/config         -> upsert / replace settings (whole-form submit)
  GET  /api/config/status  -> {configured, missing, set_keys} for the run page
  POST /api/run            -> drive the LangGraph; loads keys from SQLite
  GET  /runs/<id>/<file>   -> static mount for run artefacts (TTS audio etc.)

Persistence:
  All settings live in `data/app.db` (sqlite). The Run page never sees keys.
  Per request, the FastAPI handler loads the saved config into a contextvar
  that the LangGraph nodes read; contextvar is reset on response.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import storage
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
# Config endpoints (Settings page)
# ---------------------------------------------------------------------------


@app.get("/api/config")
def get_config() -> JSONResponse:
    """Return the saved config dict. Localhost-only deployment, so we return
    full values — the user provided them, they can re-read them."""
    return JSONResponse(storage.load_config())


@app.post("/api/config")
def post_config(payload: dict[str, Any]) -> JSONResponse:
    """Upsert the whole settings form. Empty values delete; missing keys
    delete (full-form submit semantics)."""
    storage.save_config(payload, replace_missing=True)
    return JSONResponse({"ok": True, "status": storage.status()})


@app.get("/api/config/status")
def get_config_status() -> JSONResponse:
    return JSONResponse(storage.status())


# ---------------------------------------------------------------------------
# Run endpoint
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    brief: str = Field(..., min_length=10)
    locale: str = "ar-SA"
    target_audience: str = "Saudi adults 25-45, parents, urban"


@app.post("/api/run")
def run(req: RunRequest) -> JSONResponse:
    if not storage.has_required_keys():
        missing = storage.status()["missing"]
        raise HTTPException(
            status_code=400,
            detail=(
                f"Required API keys not configured: {', '.join(missing)}. "
                "Open /settings, fill in the LLM / Ark / TTS keys, save, and try again."
            ),
        )

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
            tb = traceback.format_exc()
            print(f"\n=== graph failed ({type(exc).__name__}) ===\n{tb}", flush=True)
            raise HTTPException(
                status_code=500,
                detail=f"graph failed: {type(exc).__name__}: {exc}",
            ) from exc
    finally:
        reset_request_config(token)

    payload = _serialize(final)
    # Surface the model identifiers used so the UI can label asset cards.
    payload["_image_model"] = cfg.get("image_model") or "doubao-seedream-5-0-260128"
    payload["_video_model"] = cfg.get("video_model") or "doubao-seedance-2-0-260128"
    payload["_tts_resource_id"] = cfg.get("tts_resource_id") or "seed-tts-2.0"
    payload["_llm_model"] = cfg.get("openai_model") or os.getenv("OPENAI_MODEL", "")
    _save(run_id, payload)
    return JSONResponse(payload)


def _serialize(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state, ensure_ascii=False, default=str))


def _save(run_id: str, payload: dict[str, Any]) -> None:
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
