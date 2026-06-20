import asyncio
import json
import logging

from livekit import agents
from livekit.agents import AgentServer

from app.config import get_settings
from app.pipeline import VoicePipeline

settings = get_settings()
# LiveKit Agents installs a structured handler with job/room context. Adding a
# second root handler here makes every app and SDK event appear two or three
# times, especially in spawned job processes. Set only the level and let the
# runtime own the handler.
logging.getLogger().setLevel(
    getattr(logging, settings.log_level.upper(), logging.INFO)
)
logger = logging.getLogger(__name__)

# Validate required config before accepting any LiveKit jobs.
# Raises RuntimeError with a clear message if something is missing.
settings.validate_runtime()

server = AgentServer(
    # The M7 target is one browser session on one GPU. More warm processes would
    # add idle overhead and can create competing Whisper model allocations.
    num_idle_processes=1,
    # Faster Whisper CPU mode settles around 1.3–1.5 GB in current acceptance
    # runs. Keep the warning above normal model residency so it signals growth.
    job_memory_warn_mb=settings.job_memory_warn_mb,
    shutdown_process_timeout=5.0,
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
    except Exception:
        # Catch-all: log with full traceback so the failure is visible in logs
        # rather than silently disappearing. The job process exits normally
        # and the AgentServer starts a new idle process.
        logger.exception("Unhandled error in voice pipeline — session=%s", session_id)


if __name__ == "__main__":
    agents.cli.run_app(server)
