"""Process-wide snapshot of the production vLLM serving lifecycle.

The bootstrap/deploy flow used to block FastAPI startup (and the deploy/rollback
HTTP requests) while waiting up to ``vllm_start_timeout_seconds`` for vLLM to load
the model. That made ``/health`` unreachable and the panel showed an empty active
model with no way to tell whether loading was in progress — forcing operators to
SSH into the host and read ``docker logs``.

Instead, the long-running transition now runs in a background thread (see
``serving_orchestrator``) and reports progress here. ``/health`` and
``/serving-status`` read this cached snapshot, so they answer instantly and the
panel can render a live banner (loading… → ready/error).

agent-backend runs a single uvicorn process, so an in-process holder guarded by a
lock is sufficient — no Redis needed. Writers are the background thread; readers
are request handlers on the event loop. Dict updates under the lock are safe.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

# status values: idle | promoting | loading | ready | error
_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "idle",
    "detail": "",
    "active_model": None,
    "served_model_name": None,
    "models": [],
    "error": None,
    "started_at": None,
    "updated_at": None,
}

_ACTIVE = ("promoting", "loading")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set(*, status: str | None = None, **fields: Any) -> None:
    """Update the snapshot. Only known keys are applied.

    ``started_at`` is stamped when a fresh transition begins (entering
    promoting/loading from a non-active state) so the banner can show an elapsed time.
    """
    with _lock:
        if status is not None:
            if status in _ACTIVE and _state["status"] not in _ACTIVE:
                _state["started_at"] = _now()
            _state["status"] = status
        for key, value in fields.items():
            if key in _state:
                _state[key] = value
        _state["updated_at"] = _now()


def snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_state)
