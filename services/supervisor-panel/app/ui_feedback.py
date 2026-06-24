"""Shared toast/snackbar helpers for supervisor-panel UX feedback."""
from __future__ import annotations

import html
import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

FLASH_COOKIE_NAME = "anruf_panel_toast"


def build_toast(
    message: str,
    *,
    kind: str = "info",
    title: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "kind": kind,
    }
    if title:
        payload["title"] = title
    if duration_ms is not None:
        payload["durationMs"] = duration_ms
    return payload


def set_toast_cookie(response: Response, payload: dict[str, Any]) -> Response:
    response.set_cookie(
        key=FLASH_COOKIE_NAME,
        value=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        httponly=False,
        max_age=30,
        samesite="lax",
        path="/",
    )
    return response


def clear_toast_cookie(response: Response) -> Response:
    response.delete_cookie(FLASH_COOKIE_NAME, path="/")
    return response


def toast_redirect(
    url: str,
    message: str,
    *,
    kind: str = "success",
    title: str | None = None,
    duration_ms: int | None = None,
    status_code: int = 303,
) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=status_code)
    return set_toast_cookie(
        response,
        build_toast(
            message,
            kind=kind,
            title=title,
            duration_ms=duration_ms,
        ),
    )


def toast_fragment(
    message: str,
    *,
    kind: str = "info",
    status_code: int = 200,
    refresh_event: str | None = None,
) -> HTMLResponse:
    kind_map = {
        "success": "alert-success",
        "error": "alert-error",
        "warning": "alert-warning",
        "info": "alert-info",
    }
    refresh_attr = f' data-refresh-on-toast="{html.escape(refresh_event, quote=True)}"' if refresh_event else ""
    body = (
        f'<div data-toast-only="true"{refresh_attr}>'
        f'<div class="alert {kind_map.get(kind, "alert-info")}">{html.escape(message)}</div>'
        "</div>"
    )
    return HTMLResponse(body, status_code=status_code)


def load_toast(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(FLASH_COOKIE_NAME)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
