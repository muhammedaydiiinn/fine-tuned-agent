from __future__ import annotations

import json

from app.config import settings


def _api():
    from livekit import api  # noqa: PLC0415

    return api


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
