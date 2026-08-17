"""Pure, dependency-free logic for the AgentSession engine (task B5).

These helpers are kept out of ``app.agent_session_engine`` — which imports
``livekit`` at module load — so they can be unit-tested on a host that does not
have the livekit stack installed. The engine imports and re-uses them.
"""
from __future__ import annotations

from typing import Any


def latest_user_text(chat_ctx: Any) -> str:
    """Return the text of the most recent user message in a chat context.

    Duck-typed against ``livekit.agents.llm.ChatContext``: it only touches
    ``chat_ctx.items`` and each item's ``type`` / ``role`` / ``text_content``.
    The backend owns dialogue state per ``session_id``, so only the latest
    customer utterance is forwarded; the rest of the history is ignored.
    """
    for item in reversed(list(getattr(chat_ctx, "items", []))):
        if getattr(item, "type", None) != "message":
            continue
        if getattr(item, "role", None) != "user":
            continue
        text = getattr(item, "text_content", None)
        if text:
            return text.strip()
    return ""


def backend_response_chunks(turn: dict[str, Any] | None) -> list[str]:
    """Map a backend ``agent_turn`` payload to the text chunks to stream.

    The backend already applies guardrails/state and returns the final text in
    ``agent_response``. We currently emit it as a single chunk; splitting is
    isolated here so streaming granularity can evolve without touching the
    LLM-stream plumbing.
    """
    text = (turn or {}).get("agent_response") or ""
    text = text.strip()
    if not text:
        return []
    return [text]


def backend_request_id(turn: dict[str, Any] | None, fallback: str) -> str:
    """Derive a stable request id for a backend turn (its ``turn_id`` if any)."""
    turn_id = (turn or {}).get("turn_id")
    if turn_id is not None:
        return str(turn_id)
    return fallback
