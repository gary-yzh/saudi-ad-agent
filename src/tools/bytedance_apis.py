"""ByteDance / Volcengine asset clients.

Three real APIs, no mocks:

| Asset | Service | Endpoint | Pattern |
| ----- | ------- | -------- | ------- |
| Image | Volcengine Ark (Doubao Seedream) | `/api/v3/images/generations` | sync (OpenAI SDK) |
| Video | Volcengine Ark (Doubao Seedance) | `/api/v3/contents/generations/tasks` | async create + poll |
| Audio | ByteDance OpenSpeech (Doubao TTS) | `/api/v3/tts/unidirectional` | HTTP chunked |

Keys come from per-request `runtime.set_request_config` (UI form) or env
vars. There is no fallback — calls require credentials.

The TTS endpoint streams base64 audio chunks; we collect, decode and write
the result under `outputs/runs/<run_id>/voice.<format>`. The server mounts
that directory at `/runs/...` so the browser can play the file.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from ..runtime import cfg_get


# ---------------------------------------------------------------------------
# Endpoints + defaults
# ---------------------------------------------------------------------------

ARK_BASE_URL_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"
TTS_URL_DEFAULT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

IMAGE_MODEL_DEFAULT = "doubao-seedream-5-0-260128"
VIDEO_MODEL_DEFAULT = "doubao-seedance-2-0-260128"
TTS_RESOURCE_ID_DEFAULT = "seed-tts-2.0"
TTS_SPEAKER_DEFAULT = "zh_female_shuangkuaisisi_moon_bigtts"
TTS_FORMAT_DEFAULT = "mp3"
TTS_SAMPLE_RATE_DEFAULT = 24000

VIDEO_POLL_INTERVAL_S = 10.0
VIDEO_POLL_TIMEOUT_S = 1800  # 30 min — Doubao Seedance can queue for a long time
VIDEO_TASK_DONE_OK = {"succeeded"}
VIDEO_TASK_DONE_FAIL = {"failed", "cancelled"}

# Doubao moderation codes that we can recover from by softening the prompt
# and retrying. Any other failure is fatal.
MODERATION_CODES = {
    "OutputImageSensitiveContentDetected",
    "OutputVideoSensitiveContentDetected",
    "InputTextSensitiveContentDetected",
    "InputImageSensitiveContentDetected",
}


class ContentModerationError(RuntimeError):
    """Doubao's safety filter rejected an input or output."""

    def __init__(self, *, stage: str, code: str, message: str, prompt: str):
        super().__init__(f"{stage} moderation: {code} — {message}")
        self.stage = stage  # "image" | "video"
        self.code = code
        self.message = message
        self.prompt = prompt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "outputs" / "runs"


def _required(key: str, env_var: str, label: str) -> str:
    val = cfg_get(key, env_var=env_var, default="")
    if not val:
        raise RuntimeError(
            f"{label} is not configured. Provide it in the UI form or set {env_var}."
        )
    return val


def _ark_api_key() -> str:
    return _required("ark_api_key", "ARK_API_KEY", "Ark API key")


def _ark_base_url() -> str:
    return cfg_get("ark_base_url", env_var="ARK_BASE_URL", default=ARK_BASE_URL_DEFAULT)


def _tts_api_key() -> str:
    return _required("tts_api_key", "TTS_API_KEY", "TTS API key")


def _tts_url() -> str:
    return cfg_get("tts_url", env_var="TTS_URL", default=TTS_URL_DEFAULT)


# ---------------------------------------------------------------------------
# 1. Image — Doubao Seedream (OpenAI-compatible)
# ---------------------------------------------------------------------------


def seedream_generate(prompt: str, *, aspect: str = "9:16") -> dict[str, Any]:
    """Generate a still image and return the URL plus call metadata.

    Raises `ContentModerationError` if Doubao's safety filter rejects the
    prompt or output — caller can soften the prompt and retry.
    """
    from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAI

    model = cfg_get("image_model", env_var="ARK_IMAGE_MODEL", default=IMAGE_MODEL_DEFAULT)
    size = cfg_get("image_size", env_var="ARK_IMAGE_SIZE", default="2K")
    watermark = bool(cfg_get("image_watermark", default=False))

    t0 = time.time()
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            client = OpenAI(
                api_key=_ark_api_key(),
                base_url=_ark_base_url(),
                timeout=120.0,
                max_retries=0,
            )
            resp = client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                response_format="url",
                extra_body={"watermark": watermark},
            )
            if not resp.data:
                raise RuntimeError(f"Seedream returned no data: {resp}")
            return {
                "url": resp.data[0].url,
                "model": model,
                "size": size,
                "watermark": watermark,
                "latency_ms": int((time.time() - t0) * 1000),
                "attempts": attempt + 1,
            }
        except BadRequestError as e:
            err_code, err_msg = _extract_bad_request(e)
            if err_code in MODERATION_CODES:
                raise ContentModerationError(
                    stage="image", code=err_code, message=err_msg, prompt=prompt
                ) from e
            raise
        except (APIConnectionError, APITimeoutError) as e:
            last_exc = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"Seedream failed after 3 attempts: {last_exc}") from last_exc


def _extract_bad_request(exc: Any) -> tuple[str, str]:
    """Pull the Volcengine error code + message out of an openai.BadRequestError.

    Tries several shapes because the openai SDK across versions exposes the
    parsed body differently and Volcengine's wire format isn't always parsed
    successfully (we've seen e.body=None in the wild).
    """
    code = ""
    msg = str(exc)

    # 1. .body if it's already a dict
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code") or ""
            msg = err.get("message") or msg
            if code:
                return code, msg

    # 2. .response.json() / .response.text
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    code = err.get("code") or code
                    msg = err.get("message") or msg
                    if code:
                        return code, msg
        except Exception:
            pass

    # 3. Last resort — regex out of the stringified exception (which contains
    #    the body when openai prints "Error code: 400 - {...}").
    import re as _re
    m = _re.search(r"'code'\s*:\s*'([^']+)'", str(exc))
    if m:
        code = m.group(1)
    m2 = _re.search(r"'message'\s*:\s*'([^']+)'", str(exc))
    if m2:
        msg = m2.group(1)
    return code, msg


# ---------------------------------------------------------------------------
# 2. Video — Doubao Seedance (async task + poll)
# ---------------------------------------------------------------------------


def _build_video_content(
    *, prompt: str, image_url: Optional[str]
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if image_url:
        # Use the still as a reference image. The user-provided sample uses
        # role="reference_image"; the field is accepted by Seedance and gives
        # the video a consistent visual anchor.
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "reference_image",
            }
        )
    return content


def _http_post_json(url: str, *, headers: dict, json_body: dict, timeout: float = 60) -> dict:
    """One-shot POST with a fresh httpx.Client. Retries once on transient
    network errors (Windows + Volcengine's short keep-alive can drop sockets
    under our feet during long-running jobs)."""
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
                resp = client.post(url, headers=headers, json=json_body)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"POST {url} → HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                return resp.json()
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as e:
            last_exc = e
            time.sleep(1.5)
    raise RuntimeError(f"POST {url} failed after retry: {last_exc}") from last_exc


def _http_get_json(url: str, *, headers: dict, timeout: float = 30) -> dict:
    """One-shot GET with a fresh httpx.Client + retries."""
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"GET {url} → HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                return resp.json()
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
            last_exc = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"GET {url} failed after retry: {last_exc}") from last_exc


def seedance_generate(
    *, image_url: Optional[str], motion_prompt: str, duration_s: float = 5.0
) -> dict[str, Any]:
    model = cfg_get("video_model", env_var="ARK_VIDEO_MODEL", default=VIDEO_MODEL_DEFAULT)
    ratio = cfg_get("video_ratio", env_var="ARK_VIDEO_RATIO", default="9:16")
    duration = int(cfg_get("video_duration", default=int(round(duration_s))))
    generate_audio = bool(cfg_get("video_generate_audio", default=False))
    watermark = bool(cfg_get("video_watermark", default=False))

    body: dict[str, Any] = {
        "model": model,
        "content": _build_video_content(prompt=motion_prompt, image_url=image_url),
        "ratio": ratio,
        "duration": duration,
        "generate_audio": generate_audio,
        "watermark": watermark,
    }
    headers = {
        "Authorization": f"Bearer {_ark_api_key()}",
        "Content-Type": "application/json",
    }
    create_url = f"{_ark_base_url()}/contents/generations/tasks"

    t0 = time.time()
    data = _http_post_json(create_url, headers=headers, json_body=body, timeout=60)
    task_id = data.get("id") or (data.get("data") or {}).get("id")
    if not task_id:
        raise RuntimeError(f"Seedance task create returned no id: {data}")

    poll_url = f"{_ark_base_url()}/contents/generations/tasks/{task_id}"
    poll_headers = {"Authorization": f"Bearer {_ark_api_key()}"}
    deadline = time.time() + VIDEO_POLL_TIMEOUT_S
    last_status = None
    consecutive_poll_errors = 0
    MAX_CONSECUTIVE_POLL_ERRORS = 6  # ≈ 6 polls × 6s interval = ~36s tolerance
    while time.time() < deadline:
        time.sleep(VIDEO_POLL_INTERVAL_S)
        try:
            d = _http_get_json(poll_url, headers=poll_headers, timeout=30)
        except RuntimeError as e:
            # Transient network blip — task is still running server-side, keep polling.
            consecutive_poll_errors += 1
            if consecutive_poll_errors > MAX_CONSECUTIVE_POLL_ERRORS:
                raise RuntimeError(
                    f"Seedance task {task_id} polling lost connectivity for "
                    f"{consecutive_poll_errors} consecutive attempts: {e}"
                ) from e
            print(
                f"[seedance poll {task_id}] transient error "
                f"({consecutive_poll_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}): {e}",
                flush=True,
            )
            continue
        consecutive_poll_errors = 0
        status = (d.get("status") or "").lower()
        last_status = status or last_status
        if status in VIDEO_TASK_DONE_OK:
            content = d.get("content") or {}
            video_url = content.get("video_url") or d.get("video_url")
            if not video_url:
                raise RuntimeError(
                    f"Seedance task {task_id} succeeded but no video_url: {d}"
                )
            return {
                "url": video_url,
                "duration_s": float(duration),
                "ratio": ratio,
                "model": model,
                "task_id": task_id,
                "generate_audio": generate_audio,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        if status in VIDEO_TASK_DONE_FAIL:
            err = d.get("error") or {}
            err_code = err.get("code") or ""
            err_msg = err.get("message") or str(d)
            if err_code in MODERATION_CODES:
                raise ContentModerationError(
                    stage="video", code=err_code, message=err_msg, prompt=motion_prompt
                )
            raise RuntimeError(
                f"Seedance task {task_id} {status}: {err_code} {err_msg}"
            )
    raise RuntimeError(
        f"Seedance task {task_id} timed out after {VIDEO_POLL_TIMEOUT_S}s "
        f"(last status: {last_status})"
    )


# ---------------------------------------------------------------------------
# 3. Audio — Doubao TTS (HTTP chunked, base64 streamed)
# ---------------------------------------------------------------------------


def _audio_params() -> dict[str, Any]:
    params: dict[str, Any] = {
        "format": cfg_get("tts_format", env_var="TTS_FORMAT", default=TTS_FORMAT_DEFAULT),
        "sample_rate": int(cfg_get("tts_sample_rate", default=TTS_SAMPLE_RATE_DEFAULT)),
        "speech_rate": int(cfg_get("tts_speech_rate", default=0)),
        "loudness_rate": int(cfg_get("tts_loudness_rate", default=0)),
    }
    emotion = cfg_get("tts_emotion", default="")
    if emotion:
        params["emotion"] = emotion
    emotion_scale = cfg_get("tts_emotion_scale", default=None)
    if emotion_scale is not None and emotion_scale != "":
        params["emotion_scale"] = int(emotion_scale)
    return params


def _additions(locale: Optional[str]) -> dict[str, Any]:
    additions: dict[str, Any] = {"enable_language_detector": True}
    silence = cfg_get("tts_silence_duration", default=0)
    if silence and int(silence) > 0:
        additions["silence_duration"] = int(silence)
    explicit = cfg_get("tts_explicit_language", default="")
    if not explicit and locale:
        # Best-effort default so Doubao TTS doesn't silently skip foreign text.
        # Map common IETF locales to the explicit_language values Doubao accepts.
        code = (locale or "").split("-", 1)[0].lower()
        explicit = {
            "zh": "zh-cn",
            "en": "en",
            "ja": "ja",
            "es": "es-mx",
            "id": "id",
            "pt": "pt-br",
            "de": "de",
            "fr": "fr",
        }.get(code, "")
    if explicit:
        additions["explicit_language"] = explicit
    return additions


def seed_speech_generate(
    *,
    text: str,
    voice: str = "",
    locale: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Synthesize speech and write the result to disk.

    Returns a relative URL (`/runs/<run_id>/voice.<format>`) that the FastAPI
    server serves via its static mount.
    """
    if not text:
        raise RuntimeError("TTS got empty text")

    speaker = cfg_get("tts_speaker", env_var="TTS_SPEAKER", default=voice or TTS_SPEAKER_DEFAULT)
    resource_id = cfg_get(
        "tts_resource_id", env_var="TTS_RESOURCE_ID", default=TTS_RESOURCE_ID_DEFAULT
    )
    audio_params = _audio_params()
    additions = _additions(locale)

    body: dict[str, Any] = {
        "user": {"uid": run_id or "saudi-ad-agent"},
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": audio_params,
            "additions": json.dumps(additions),
        },
    }

    headers = {
        "X-Api-Key": _tts_api_key(),
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    t0 = time.time()
    audio = bytearray()
    sentence_frames: list[dict] = []
    with httpx.Client(timeout=180) as client:
        with client.stream("POST", _tts_url(), headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                # Need to read the body before closing the stream
                body_text = resp.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"TTS HTTP {resp.status_code}: {body_text[:500]}"
                )
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                line_str = line_str.strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue
                code = msg.get("code")
                if code in (None, 0):
                    chunk = msg.get("data")
                    if isinstance(chunk, str) and chunk:
                        audio.extend(base64.b64decode(chunk))
                    elif msg.get("sentence") is not None:
                        sentence_frames.append(msg.get("sentence") or {})
                elif code == 20000000:
                    break  # success completion sentinel
                else:
                    raise RuntimeError(
                        f"TTS error code={code} message={msg.get('message')}"
                    )

    if not audio:
        # Server returned 200/OK but no audio chunks — almost always a
        # speaker/language mismatch (e.g. an English-only voice asked to
        # render Arabic text). Surface enough detail to debug.
        sample = (text[:60] + "…") if len(text) > 60 else text
        raise RuntimeError(
            "TTS streamed no audio data. The speaker likely doesn't support "
            "this language. "
            f"speaker={speaker!r}, resource_id={resource_id!r}, "
            f"locale={locale!r}, text={sample!r}, "
            f"sentence_frames={sentence_frames}. "
            "Either pick a multilingual / matching-language speaker (see "
            "https://www.volcengine.com/docs/6561/1257544), or set the brief "
            "locale to one the speaker handles."
        )

    fmt = audio_params["format"]
    if not run_id:
        run_id = "ad-hoc-" + uuid.uuid4().hex[:8]
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"voice.{fmt}"
    out_path = out_dir / fname
    out_path.write_bytes(bytes(audio))

    return {
        "url": f"/runs/{run_id}/{fname}",
        "voice": speaker,
        "resource_id": resource_id,
        "format": fmt,
        "sample_rate": audio_params["sample_rate"],
        "duration_s": max(2.0, round(len(text) / 12, 1)),
        "latency_ms": int((time.time() - t0) * 1000),
        "bytes": len(audio),
    }
