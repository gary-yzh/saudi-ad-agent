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
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import guard, sessions, storage
from src.graph import build_graph
from src.runtime import reset_request_config, set_request_config

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
RUNS_DIR = ROOT / "outputs" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Saudi Ad Agent")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
app.mount("/runs", StaticFiles(directory=str(RUNS_DIR)), name="runs")


# Force the browser to revalidate every static asset on every request.
# Default StaticFiles sends no Cache-Control header → browsers cache
# /static/app.js for hours, which means a code edit on the server
# silently fails to reach the user until they Ctrl+Shift+R. Once bit
# users into thinking buttons were broken after a refactor that
# actually shipped fine. `no-cache` (not `no-store`) keeps ETag /
# Last-Modified validation, so unchanged files still 304 fast — but
# changed files always 200 with fresh bytes.
@app.middleware("http")
async def _no_cache_for_static(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/settings"):
        response.headers["Cache-Control"] = "no-cache"
    return response

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
    # Locale is derived from the saved TTS speaker so the planner writes
    # voiceover in a language the speaker can actually pronounce.
    # Clients may still send a value (advanced override), but the server
    # ignores it unless the speaker is unset.
    locale: Optional[str] = None
    target_audience: str = "Saudi adults 25-45, parents, urban"


# Map a Doubao speaker ID to a sensible IETF locale based on its prefix.
# Keep this list aligned with the speakers users actually paste into Settings.
def _locale_from_speaker(speaker: str | None) -> str:
    if not speaker:
        return "en-US"
    s = speaker.lower()
    if s.startswith(("zh_", "zh-")):
        return "zh-CN"
    if s.startswith(("ja_", "ja-")):
        return "ja-JP"
    if s.startswith(("ko_", "ko-")):
        return "ko-KR"
    if s.startswith(("ar_", "ar-")):
        return "ar-SA"
    if s.startswith(("es_", "es-")):
        return "es-MX"
    if s.startswith(("pt_", "pt-")):
        return "pt-BR"
    if s.startswith(("id_", "id-")):
        return "id-ID"
    # en_*, multilingual speakers, or unknown prefix → English (US)
    return "en-US"


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
    logo = storage.get_brand_logo(session_id)
    if logo:
        logo = {k: v for k, v in logo.items() if k != "path"}
    return {
        "session": s,
        "messages": storage.list_messages(session_id),
        "shot_images": storage.list_shot_images(session_id),
        "video": storage.get_video(session_id),
        "brand_manual": storage.get_brand_manual(session_id),
        "brand_logo": logo,
    }


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest) -> JSONResponse:
    sid = sessions.new_session_id()
    cfg = storage.load_config()
    auto_locale = _locale_from_speaker(cfg.get("tts_speaker"))
    locale = req.locale or auto_locale  # client override only used if no speaker set
    storage.create_session(
        session_id=sid, locale=locale, target_audience=req.target_audience
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
    except guard.UserInputViolation as viol:
        # 422 — the user can fix this by rephrasing. Return the structured
        # violations so the UI can render a specific, actionable error.
        raise HTTPException(
            status_code=422,
            detail={
                "kind": "guard_rejected",
                "message": (
                    "Your message contains content we can't use in a Saudi "
                    "ad. Please rephrase and try again."
                ),
                "violations": viol.violations,
            },
        ) from viol
    except Exception as exc:
        import traceback
        print(f"\n=== chat_turn failed ===\n{traceback.format_exc()}", flush=True)
        raise HTTPException(500, f"chat_turn failed: {type(exc).__name__}: {exc}") from exc
    finally:
        reset_request_config(token)
    return JSONResponse({"reply": reply, **_session_view(sid)})


# ---------------------------------------------------------------------------
# Brand manual (RAG source) — per-session PDF upload
# ---------------------------------------------------------------------------

MAX_BRAND_MANUAL_BYTES = 20 * 1024 * 1024  # 20 MB


@app.post("/api/sessions/{sid}/brand-manual")
async def upload_brand_manual(sid: str, file: UploadFile = File(...)) -> JSONResponse:
    if storage.get_session(sid) is None:
        raise HTTPException(404, "Session not found")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted (.pdf).")
    contents = await file.read()
    if len(contents) > MAX_BRAND_MANUAL_BYTES:
        raise HTTPException(
            413,
            f"PDF is {len(contents) // (1024 * 1024)} MB, max {MAX_BRAND_MANUAL_BYTES // (1024 * 1024)} MB.",
        )
    try:
        info = sessions.save_uploaded_brand_manual(
            session_id=sid, filename=file.filename, pdf_bytes=contents
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse({"ok": True, "manual": info})


@app.get("/api/sessions/{sid}/brand-manual")
def get_brand_manual(sid: str) -> JSONResponse:
    if storage.get_session(sid) is None:
        raise HTTPException(404, "Session not found")
    manual = storage.get_brand_manual(sid)
    return JSONResponse(manual or {})


@app.delete("/api/sessions/{sid}/brand-manual")
def remove_brand_manual(sid: str) -> JSONResponse:
    storage.delete_brand_manual(sid)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Brand logo (composited onto every still as a deterministic overlay)
# ---------------------------------------------------------------------------

MAX_BRAND_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB


@app.post("/api/sessions/{sid}/brand-logo")
async def upload_brand_logo(sid: str, file: UploadFile = File(...)) -> JSONResponse:
    if storage.get_session(sid) is None:
        raise HTTPException(404, "Session not found")
    name_lower = (file.filename or "").lower()
    if not any(name_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
        raise HTTPException(400, "Only PNG / JPG / WEBP images are accepted.")
    contents = await file.read()
    if len(contents) > MAX_BRAND_LOGO_BYTES:
        raise HTTPException(
            413,
            f"Logo is {len(contents) // 1024} KB, max {MAX_BRAND_LOGO_BYTES // 1024} KB.",
        )
    try:
        info = sessions.save_uploaded_brand_logo(
            session_id=sid, filename=file.filename, image_bytes=contents
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse({"ok": True, "logo": info})


@app.get("/api/sessions/{sid}/brand-logo")
def get_brand_logo(sid: str) -> JSONResponse:
    if storage.get_session(sid) is None:
        raise HTTPException(404, "Session not found")
    logo = storage.get_brand_logo(sid)
    if not logo:
        return JSONResponse({})
    # Don't expose the on-disk path in the API
    public = {k: v for k, v in logo.items() if k != "path"}
    return JSONResponse(public)


@app.delete("/api/sessions/{sid}/brand-logo")
def remove_brand_logo(sid: str) -> JSONResponse:
    storage.delete_brand_logo(sid)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Shot-level operations: refine (iterative) + retry (after moderation)
# ---------------------------------------------------------------------------


class RefineShotRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)


@app.post("/api/sessions/{sid}/shots/{shot_id}/refine")
def refine_shot(sid: str, shot_id: int, req: RefineShotRequest) -> JSONResponse:
    _require_keys()
    try:
        sessions.refine_shot(
            session_id=sid,
            shot_id=shot_id,
            instruction=req.instruction,
            executor=_executor,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return JSONResponse({"ok": True})


@app.post("/api/sessions/{sid}/shots/{shot_id}/retry")
def retry_shot(sid: str, shot_id: int) -> JSONResponse:
    _require_keys()
    try:
        sessions.retry_shot(session_id=sid, shot_id=shot_id, executor=_executor)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return JSONResponse({"ok": True})


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
