"""Panel authentication — simple cookie-based auth."""
import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import settings

COOKIE_NAME = "agent_panel_session"
PUBLIC_PATHS = {"/login", "/logout", "/health"}


def _expected_token() -> str:
    """Static token derived from JWT_SECRET. Automatically invalidated when secret changes."""
    return hmac.new(
        settings.jwt_secret.encode(),
        b"panel_session_v1",
        hashlib.sha256,
    ).hexdigest()


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(token, _expected_token())


def login_response(redirect_to: str = "/") -> RedirectResponse:
    token = _expected_token()
    response = RedirectResponse(url=redirect_to, status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


def check_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username, settings.admin_user)
    pass_ok = hmac.compare_digest(password, settings.admin_password)
    return user_ok and pass_ok
