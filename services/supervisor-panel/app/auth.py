"""Panel authentication — cookie-based auth with per-session tokens.

Cookie format: ``{session_id}.{issued_at}.{signature}``
- ``session_id``  — random URL-safe token (secrets.token_urlsafe)
- ``issued_at``   — Unix timestamp (int) at login
- ``signature``   — HMAC-SHA256(jwt_secret, f"{session_id}:{issued_at}")

A token expires when ``now - issued_at > SESSION_TTL_SECONDS``.
Changing ``JWT_SECRET`` automatically invalidates all existing sessions.
"""
import hashlib
import hmac
import logging
import secrets
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "anruf_panel_session"
PUBLIC_PATHS = {"/login", "/logout", "/health"}
SESSION_TTL_SECONDS = 60 * 60 * 24  # 24 hours


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sign(session_id: str, issued_at: int) -> str:
    """Return HMAC-SHA256 hex for the given session components."""
    msg = f"{session_id}:{issued_at}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()


def _encode(session_id: str, issued_at: int) -> str:
    return f"{session_id}.{issued_at}.{_sign(session_id, issued_at)}"


def _decode(token: str) -> tuple[str, int] | None:
    """Parse and verify a cookie token.

    Returns ``(session_id, issued_at)`` on success, ``None`` otherwise.
    """
    try:
        session_id, issued_at_str, signature = token.split(".", 2)
        issued_at = int(issued_at_str)
    except (ValueError, AttributeError):
        return None

    expected = _sign(session_id, issued_at)
    if not hmac.compare_digest(signature, expected):
        return None  # tampered

    if time.time() - issued_at > SESSION_TTL_SECONDS:
        return None  # expired

    return session_id, issued_at


# ── Public API ────────────────────────────────────────────────────────────────

def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return _decode(token) is not None


def get_session_id(request: Request) -> str | None:
    """Return the authenticated session_id, or None if not authenticated."""
    token = request.cookies.get(COOKIE_NAME, "")
    result = _decode(token)
    return result[0] if result else None


def derive_csrf_token(request: Request) -> str:
    """Derive a stable CSRF token from the current session id.

    The token is deterministic within a session and does NOT require
    additional server-side state.  Uses a separate HMAC key material
    (``b"csrf_v1"``) so brute-forcing the CSRF token does not help
    forge auth cookies.
    """
    session_id = get_session_id(request) or ""
    return hmac.new(
        settings.jwt_secret.encode(),
        f"csrf_v1:{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def login_response(redirect_to: str = "/") -> RedirectResponse:
    session_id = secrets.token_urlsafe(32)
    issued_at = int(time.time())
    token = _encode(session_id, issued_at)
    response = RedirectResponse(url=redirect_to, status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        # secure=True,  # Uncomment once HTTPS is configured
    )
    logger.info("Login successful — new session issued")
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


def check_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username, settings.admin_user)
    pass_ok = hmac.compare_digest(password, settings.admin_password)
    return user_ok and pass_ok
