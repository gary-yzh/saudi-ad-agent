"""Structured logging for saudi-ad-agent.

Wraps `structlog` to give every log line:

* an ISO-8601 UTC timestamp
* a log level (info / warning / error)
* a per-request correlation ID (so all lines for one user request can
  be grep'd together — set by FastAPI middleware in `server.py`)
* arbitrary event-specific key/value fields

Two output modes:

* **Dev** (default) — colorised, human-readable, one line per event.
* **Production** — single-line JSON per event, easy to ship to
  Datadog / Loki / ELK / Grafana Cloud. Enable by setting
  `SAA_LOG_JSON=1` in the environment.

Usage from anywhere in the codebase:

    from src.log import logger
    logger.info("seedance_task_created", session_id=sid, task_id=tid)
    logger.warning("tts_failed_non_fatal", session_id=sid, error=str(e))

Replaces the scattered `print(f"...")` calls. structlog handles
thread-safety, level filtering, and field merging — pure win over
`print` once we're past the take-home phase.
"""
from __future__ import annotations

import contextvars
import logging
import os

import structlog


# Per-request correlation ID. Set by middleware at the start of each
# request, read on every log line. Default `"-"` for log lines emitted
# outside an HTTP request (background threads, startup, CLI scripts).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def _inject_request_id(logger, method_name, event_dict):
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_logging(*, json: bool | None = None, level: str = "INFO") -> None:
    """Initialise structlog. Idempotent — safe to call more than once.

    Reads `SAA_LOG_JSON=1` to switch to production JSON output. Reads
    `SAA_LOG_LEVEL` to override the default INFO level.
    """
    if json is None:
        json = os.getenv("SAA_LOG_JSON", "").strip() == "1"
    level_name = os.getenv("SAA_LOG_LEVEL", level).upper()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared = [
        structlog.contextvars.merge_contextvars,
        _inject_request_id,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    if json:
        processors = shared + [structlog.processors.JSONRenderer()]
    else:
        processors = shared + [structlog.dev.ConsoleRenderer(colors=True)]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level_name, logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


# Configure once at import time. Re-callable later (e.g. to switch to
# JSON mode in a test) without breaking anything.
configure_logging()

# Module-level logger; callers do `from src.log import logger`.
logger = structlog.get_logger()
