import asyncio
import json
import logging

from livekit import agents
from livekit.agents import AgentServer

from app.config import get_settings
from app.pipeline import VoicePipeline

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)
server = AgentServer(
    # The M7 target is one browser session on one GPU. More warm processes would
    # add idle overhead and can create competing Whisper model allocations.
    num_idle_processes=1,
)


@server.rtc_session(agent_name=settings.livekit_agent_name)
async def fine_tuned_agent_voice(ctx: agents.JobContext):
    metadata = json.loads(ctx.job.metadata or "{}")
    session_id = metadata.get("session_id") or ctx.room.name
    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)
    try:
        participant = await ctx.wait_for_participant()
    except RuntimeError:
        logger.info("Voice room closed before the browser participant was ready")
        return
    logger.info(
        "Voice participant connected — session=%s participant=%s",
        session_id,
        participant.identity,
    )
    pipeline = VoicePipeline(settings, session_id)
    try:
        await pipeline.run(ctx.room, participant)
    except asyncio.TimeoutError:
        logger.warning(
            "No microphone track received within the M7 startup timeout — session=%s",
            session_id,
        )
    except RuntimeError as exc:
        if "disconnected" not in str(exc).lower():
            raise
        logger.info("Voice room disconnected — session=%s", session_id)


if __name__ == "__main__":
    agents.cli.run_app(server)
