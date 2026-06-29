import logging

import redis as redis_lib
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core import serving_state
from app.models import Deployment
from app.db import get_db
from app.schemas import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _active_model_name(db: DBSession) -> str | None:
    deployment = (
        db.query(Deployment)
        .filter(Deployment.environment == "production", Deployment.status == "active")
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .first()
    )
    return deployment.model_version.version_name if deployment else None


@router.get("/health", response_model=HealthResponse)
def health_check(db: DBSession = Depends(get_db)):
    # Veritabanı ping
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("DB health check başarısız: %s", exc)

    # Redis ping
    redis_ok = False
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        redis_ok = True
    except Exception as exc:
        logger.error("Redis health check başarısız: %s", exc)

    # vLLM readiness is reported by the background serving transition (cached),
    # so this endpoint answers instantly instead of probing vLLM live for 15s.
    snap = serving_state.snapshot()
    active_model = None
    vllm = None
    if settings.vllm_mode == "real":
        vllm = {
            "healthy": snap["status"] == "ready",
            "serving_status": snap["status"],
            "detail": snap["detail"],
            "models": snap["models"],
            "error": snap["error"],
        }
        active_model = _active_model_name(db) or snap["active_model"]

    if settings.vllm_mode == "mock" or snap["status"] == "ready":
        vllm_status = "ok"
    elif snap["status"] in ("idle", "promoting", "loading"):
        vllm_status = "starting"
    else:
        vllm_status = "degraded"

    if not (db_ok and redis_ok):
        status = "degraded"
    else:
        status = vllm_status

    return HealthResponse(
        status=status,
        db=db_ok,
        redis=redis_ok,
        vllm_mode=settings.vllm_mode,
        vllm=vllm,
        active_model=active_model,
    )


@router.get("/serving-status")
def serving_status(db: DBSession = Depends(get_db)):
    """Live production model serving state for the supervisor panel banner."""
    snap = serving_state.snapshot()
    snap["active_model"] = _active_model_name(db) or snap.get("active_model")
    snap["vllm_mode"] = settings.vllm_mode
    return snap
