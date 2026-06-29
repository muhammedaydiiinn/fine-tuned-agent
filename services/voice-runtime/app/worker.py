import asyncio
import json
import logging

# Must run before livekit is imported so the patch is in place when the CLI
# calls setup_logging() during the `start` sub-command.
from app import logging_config as _lc
_lc._install()

from livekit import agents
from livekit.agents import AgentServer, JobProcess

from app.config import get_settings
from app.pipeline import VoicePipeline
from app.stt_shared import get_or_create_stt

settings = get_settings()
logging.getLogger("faster_whisper").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

settings.validate_runtime()


def _prewarm_whisper(proc: JobProcess) -> None:
    """Load Whisper once per idle worker process before any room job starts."""
    stt = get_or_create_stt(settings, proc)
    asyncio.run(stt.warmup())
    logger.info("Voice worker prewarm complete — whisper ready in idle process")


server = AgentServer(
    num_idle_processes=1,
    job_memory_warn_mb=settings.job_memory_warn_mb,
    shutdown_process_timeout=5.0,
    initialize_process_timeout=120.0,
)
server.setup_fnc = _prewarm_whisper


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
    stt = get_or_create_stt(settings, ctx.proc)
    pipeline = VoicePipeline(settings, session_id, stt=stt)
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
        logger.exception("Unhandled error in voice pipeline — session=%s", session_id)


if __name__ == "__main__":
    agents.cli.run_app(server)
