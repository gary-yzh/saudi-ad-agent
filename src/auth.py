"""HTTP Basic Auth with two roles: admin and demo.

Why two roles
-------------
The single-password model leaks: if you give the password to a demo
visitor (e.g. an interviewer who wants to try the Studio), they also
get to read your API keys via Settings. Two roles fix that:

  ADMIN — you (the owner). Full access: Settings (API keys), audit log,
    all generation endpoints. Configured via:
        SAA_ADMIN_USERNAME   (optional, default 'admin')
        SAA_ADMIN_PASSWORD   (REQUIRED in any public deploy)

  DEMO — invited viewer. Can use Studio generation features but
    cannot see Settings or audit. Configured via:
        SAA_DEMO_USERNAME    (optional, default 'demo')
        SAA_DEMO_PASSWORD    (optional — if unset, demo role doesn't exist
                              and only the admin can use the app)

Both roles share one HTTP realm so the browser only prompts once per
session. Once logged in, the browser sends the same credentials on
every subsequent same-origin fetch automatically.

Dev bypass
----------
If SAA_ADMIN_PASSWORD is unset and the caller is on localhost, they
are treated as admin (dev convenience). Remote callers in that state
get a clear "this deploy is not configured" 401 rather than open access.

Usage in server.py
------------------
    from src.auth import require_admin, require_user

    # Admin-only — Settings page, config endpoints, audit log:
    @app.get("/settings")
    def page_settings(_=Depends(require_admin)):
        ...

    # Any logged-in user — generation endpoints that cost money:
    @app.post("/api/sessions", dependencies=[Depends(require_user)])
    def create_session(...):
        ...
"""
from __future__ import annotations

import os
import secrets
from enum import Enum

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


class Role(str, Enum):
    ADMIN = "admin"
    DEMO = "demo"


# auto_error=False so we control the 401 response (FastAPI's default
# raises immediately without letting us allow localhost in dev mode).
basic_security = HTTPBasic(auto_error=False)

# Backwards-compat alias — earlier code imported the private name.
_security = basic_security

# Shared realm. Both roles share it so the browser caches creds once
# and reuses them across endpoints.
_REALM = 'Basic realm="saudi-ad-agent"'


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _is_localhost(request: Request) -> bool:
    return bool(
        request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    )


def _match(creds: HTTPBasicCredentials, expected_user: str, expected_pw: str) -> bool:
    """Constant-time username + password match."""
    u_ok = secrets.compare_digest(
        creds.username.encode("utf-8"), expected_user.encode("utf-8")
    )
    p_ok = secrets.compare_digest(
        creds.password.encode("utf-8"), expected_pw.encode("utf-8")
    )
    return u_ok and p_ok


def authenticate(
    request: Request,
    creds: HTTPBasicCredentials | None,
) -> Role | None:
    """Identify the role of the request, if any.

    Returns Role.ADMIN, Role.DEMO, or None. Does NOT raise — callers
    (require_admin / require_user) decide what to do with None.
    """
    admin_pw = _env("SAA_ADMIN_PASSWORD")

    # Dev bypass: no admin password configured AND caller is on localhost
    # → treat as admin so local development isn't blocked.
    if not admin_pw and _is_localhost(request):
        return Role.ADMIN

    if creds and admin_pw:
        admin_user = _env("SAA_ADMIN_USERNAME", "admin") or "admin"
        if _match(creds, admin_user, admin_pw):
            return Role.ADMIN

    demo_pw = _env("SAA_DEMO_PASSWORD")
    if creds and demo_pw:
        demo_user = _env("SAA_DEMO_USERNAME", "demo") or "demo"
        if _match(creds, demo_user, demo_pw):
            return Role.DEMO

    return None


def _unconfigured_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "This deploy is not configured. The operator must set "
            "SAA_ADMIN_PASSWORD before exposing this app on a public URL."
        ),
    )


def _need_creds_error(detail: str = "Sign-in required.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": _REALM},
    )


def _bad_creds_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials.",
        headers={"WWW-Authenticate": _REALM},
    )


def _forbidden_admin_error() -> HTTPException:
    # 403 (not 401) — the user IS authenticated, just with the wrong
    # role for this endpoint. Don't send WWW-Authenticate because we
    # don't want the browser to keep prompting; the demo user simply
    # can't access this resource no matter what creds they retype.
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required. The demo account cannot view Settings.",
    )


def require_admin(
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    """Gate dependency for admin-only endpoints (Settings, /api/config,
    /api/audit). Returns the authenticated username or raises 401/403.

    Status code semantics:
      401 = no creds or invalid creds → browser shows login dialog
      403 = valid creds but wrong role (demo trying admin) → no retry
    """
    admin_pw = _env("SAA_ADMIN_PASSWORD")
    if not admin_pw:
        if _is_localhost(request):
            return "anonymous-local"
        raise _unconfigured_error()

    role = authenticate(request, creds)
    if role == Role.ADMIN:
        return creds.username if creds else "anonymous-local"
    if role == Role.DEMO:
        raise _forbidden_admin_error()
    if not creds:
        raise _need_creds_error("Admin credentials required.")
    raise _bad_creds_error()


def require_user(
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    """Gate dependency for any-authenticated-user endpoints (the
    generation endpoints). Both admin and demo roles are accepted.
    Returns the authenticated username or raises 401.
    """
    admin_pw = _env("SAA_ADMIN_PASSWORD")
    if not admin_pw:
        if _is_localhost(request):
            return "anonymous-local"
        raise _unconfigured_error()

    role = authenticate(request, creds)
    if role is not None:
        return creds.username if creds else "anonymous-local"
    if not creds:
        raise _need_creds_error()
    raise _bad_creds_error()
