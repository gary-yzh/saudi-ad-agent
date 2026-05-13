"""HTTP Basic Auth protection for sensitive endpoints.

Protects Settings page + config-management endpoints + audit log from
public access. The Studio itself (the demo flow) stays open — visitors
need to see the product to want it. Only the credential-bearing
surfaces need a gate.

Threat model: the app is deployed on a public URL (Fly.io / similar).
Anyone with the URL can probe every endpoint. Without auth, a visitor
opening `/settings` sees the API key form, can click the "show password"
eye, can swap in their own keys, and can burn the owner's Doubao /
OpenAI quota. This module closes that surface.

Configuration: set environment variables
    SAA_ADMIN_USERNAME (optional, defaults to 'admin')
    SAA_ADMIN_PASSWORD (required for protection to activate)

If SAA_ADMIN_PASSWORD is unset, the protection is bypassed for
localhost-only callers (dev convenience). In production deployment the
operator MUST set the password.

Usage in server.py:

    from src.auth import require_admin

    @app.get("/settings")
    def page_settings(_=Depends(require_admin)):
        ...
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


# Set auto_error=False so we control the 401 response (FastAPI's default
# raises immediately without giving us a chance to allow localhost in
# dev mode).
_security = HTTPBasic(auto_error=False)


def _expected_password() -> str:
    return os.getenv("SAA_ADMIN_PASSWORD", "").strip()


def _expected_username() -> str:
    return os.getenv("SAA_ADMIN_USERNAME", "admin").strip() or "admin"


def _is_localhost(request: Request) -> bool:
    return bool(
        request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    )


def require_admin(
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    """Gate dependency for admin endpoints. Returns the authenticated
    username (for audit logging) or raises 401.

    Behaviour:
    * `SAA_ADMIN_PASSWORD` unset + localhost caller → allowed, returns "anonymous-local".
    * `SAA_ADMIN_PASSWORD` unset + non-localhost caller → 401 with a
       message telling the operator to set the env var. This is a
       fail-safe: better to lock the operator out of their own deploy
       and force them to fix the misconfig than to silently expose
       Settings publicly.
    * `SAA_ADMIN_PASSWORD` set, no creds supplied → 401 with WWW-Authenticate
       (triggers the browser's native login dialog).
    * `SAA_ADMIN_PASSWORD` set, wrong creds → 401, same dialog.
    * `SAA_ADMIN_PASSWORD` set, correct creds → allowed, returns username.
    """
    expected_pw = _expected_password()
    expected_user = _expected_username()

    if not expected_pw:
        # Dev / unconfigured deploy. Localhost is allowed; remote callers
        # get a clear error pointing at the misconfiguration.
        if _is_localhost(request):
            return "anonymous-local"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Admin endpoints are not configured on this deploy. The "
                "operator must set SAA_ADMIN_PASSWORD before exposing "
                "this app on a public URL."
            ),
        )

    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin credentials required.",
            headers={"WWW-Authenticate": 'Basic realm="saudi-ad-agent admin"'},
        )

    # Constant-time comparison prevents timing attacks that could leak the
    # username / password character-by-character.
    user_ok = secrets.compare_digest(
        creds.username.encode("utf-8"), expected_user.encode("utf-8")
    )
    pw_ok = secrets.compare_digest(
        creds.password.encode("utf-8"), expected_pw.encode("utf-8")
    )
    if not (user_ok and pw_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad admin credentials.",
            headers={"WWW-Authenticate": 'Basic realm="saudi-ad-agent admin"'},
        )

    return creds.username
