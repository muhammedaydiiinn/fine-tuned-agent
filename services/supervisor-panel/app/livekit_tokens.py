from __future__ import annotations

import asyncio
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _api():
    from livekit import api  # noqa: PLC0415

    return api


CONTROL_TOPIC = "voice.control"


def publish_control(room_name: str, command: dict) -> bool:
    """Deliver a supervisor control command to the voice room server-side.

    Lets a supervisor Stop/replace the agent WITHOUT joining the audio room —
    the panel publishes the "voice.control" data packet directly via the LiveKit
    server API. Best-effort: returns True on success, False on failure (logged).
    """
    api = _api()
    payload = json.dumps(command, separators=(",", ":")).encode("utf-8")

    async def _send() -> None:
        lkapi = api.LiveKitAPI(
            settings.livekit_api_url,
            settings.livekit_api_key,
            settings.livekit_api_secret,
        )
        try:
            # kind defaults to 0 (RELIABLE); omit it to avoid the enum import.
            await lkapi.room.send_data(
                api.SendDataRequest(
                    room=room_name,
                    data=payload,
                    topic=CONTROL_TOPIC,
                )
            )
        finally:
            await lkapi.aclose()

    try:
        asyncio.run(_send())
        return True
    except Exception:  # noqa: BLE001 — control delivery must not break the request
        logger.exception("Server-side voice control delivery failed — room=%s", room_name)
        return False


def build_voice_token(
    *,
    participant_identity: str,
    room_name: str,
    dispatch_agent: bool,
) -> str:
    api = _api()
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_name("Supervisor voice test")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
    )
    if dispatch_agent:
        metadata = json.dumps({"session_id": room_name}, separators=(",", ":"))
        token = token.with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=settings.livekit_agent_name,
                        metadata=metadata,
                    )
                ]
            )
        )
    return token.to_jwt()
