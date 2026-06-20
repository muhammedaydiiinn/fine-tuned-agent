import httpx

from app.config import Settings


class AgentBackend:
    def __init__(self, settings: Settings):
        self.base_url = settings.agent_backend_url.rstrip("/")
        self.headers = {"X-API-Key": settings.api_key} if settings.api_key else {}

    async def create_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self.base_url}/sessions",
                headers=self.headers,
                json={"external_session_id": session_id},
            )
            response.raise_for_status()
            return response.json()

    async def agent_turn(self, session_id: str, transcript: str) -> dict:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/agent-turn",
                headers=self.headers,
                json={"session_id": session_id, "customer_text": transcript},
            )
            response.raise_for_status()
            return response.json()

    async def save_voice_metrics(self, turn_id: int, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self.base_url}/voice/turns/{turn_id}/metrics",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
