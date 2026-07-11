"""Auto-train scheduler — a lightweight background thread in agent-backend.

When AUTO_TRAIN_ENABLED and enough approved candidates have accumulated, it
enqueues a training pipeline via the SAME core the /training-jobs route uses.
The resulting model still stops at the eval gate and requires a human
approve-and-deploy — the scheduler never promotes to production.
"""
import logging
import threading
import time

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.models import TrainingCandidate, TrainingJob

logger = logging.getLogger(__name__)

_TICK_LOCK_KEY = "autotrain-tick"


def _count_active_batch(db) -> int:
    return (
        db.query(TrainingCandidate)
        .filter(
            TrainingCandidate.approved == True,  # noqa: E712
            TrainingCandidate.training_job_id.is_(None),
            TrainingCandidate.model_version_id.is_(None),
        )
        .count()
    )


def _pipeline_busy(db) -> bool:
    return (
        db.query(TrainingJob.id)
        .filter(TrainingJob.status.in_(("pending", "running")))
        .first()
        is not None
    )


def run_tick() -> bool:
    """One scheduler tick. Returns True if a training job was triggered."""
    if not settings.auto_train_enabled:
        return False
    db = SessionLocal()
    try:
        got = db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": _TICK_LOCK_KEY}
        ).scalar()
        if not got:
            return False
        try:
            if _pipeline_busy(db):
                logger.debug("autotrain: pipeline busy — skipping tick")
                return False
            count = _count_active_batch(db)
            if count < settings.auto_train_threshold:
                return False
            # Lazy import avoids a circular import at module load.
            from app.routes.training import create_training_job_core
            from app.schemas import CreateTrainingJobRequest

            job = create_training_job_core(db, CreateTrainingJobRequest(), auto_training=True)
            logger.info("autotrain: triggered training job id=%s (batch=%d)", job.id, count)
            return True
        finally:
            db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": _TICK_LOCK_KEY})
    except Exception:
        logger.exception("autotrain tick failed")
        return False
    finally:
        db.close()


def _loop() -> None:
    interval = max(30, int(settings.auto_train_check_interval_seconds))
    logger.info(
        "autotrain loop started (enabled=%s interval=%ds threshold=%d)",
        settings.auto_train_enabled,
        interval,
        settings.auto_train_threshold,
    )
    while True:
        try:
            run_tick()
        except Exception:
            logger.exception("autotrain loop iteration error")
        time.sleep(interval)


def start_autotrain_loop() -> None:
    """Start the daemon scheduler thread (no-op cost when auto_train_enabled=False)."""
    threading.Thread(target=_loop, name="autotrain-loop", daemon=True).start()
