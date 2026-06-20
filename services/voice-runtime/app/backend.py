"""Agent-backend HTTP client with structured error handling."""
import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Raised when the agent-backend returns an error or is unreachable.

    Callers (VoicePipeline) catch this at the turn boundary so a single
    failing turn does not kill the session.
    """


class AgentBackend:
    def __init__(self, settings: Settings):
        self.base_url = settings.agent_backend_url.rstrip("/")
        self.headers = {"X-API-Key": settings.api_key} if settings.api_key else {}
        self._timeout = httpx.Timeout(
            settings.backend_timeout_seconds,
            connect=settings.backend_connect_timeout_seconds,
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Execute an HTTP request and surface failures as BackendError."""
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, headers=self.headers, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Backend HTTP error — %s %s status=%d body=%.200s",
                method,
                path,
                exc.response.status_code,
                exc.response.text,
            )
            raise BackendError(
                f"Backend returned {exc.response.status_code} for {method} {path}"
            ) from exc
        except httpx.RequestError as exc:
            logger.exception("Backend request failed — %s %s: %s", method, path, exc)
            raise BackendError(f"Backend unreachable: {exc}") from exc

    async def create_session(self, session_id: str) -> dict:
        return await self._request(
            "POST",
            "/sessions",
            json={"external_session_id": session_id},
        )

    async def agent_turn(self, session_id: str, transcript: str) -> dict:
        return await self._request(
            "POST",
            "/agent-turn",
            json={"session_id": session_id, "customer_text": transcript},
        )

    async def save_voice_metrics(self, turn_id: int, payload: dict) -> None:
        """Save voice metrics — best-effort; BackendError is logged but not re-raised."""
        try:
            await self._request(
                "POST",
                f"/voice/turns/{turn_id}/metrics",
                json=payload,
            )
        except BackendError:
            logger.warning(
                "Voice metrics not saved for turn_id=%d — continuing without metrics",
                turn_id,
            )

    async def record_voice_event(self, payload: dict) -> None:
        """Persist an M8 event best-effort without taking down the live session."""
        try:
            await self._request("POST", "/voice/events", json=payload)
        except BackendError:
            logger.warning(
                "Voice event not persisted — event_id=%s type=%s",
                payload.get("event_id"),
                payload.get("event_type"),
            )
