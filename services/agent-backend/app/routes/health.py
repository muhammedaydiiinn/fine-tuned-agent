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


def _active_deployment(db: DBSession) -> Deployment | None:
    return (
        db.query(Deployment)
        .filter(Deployment.environment == "production", Deployment.status == "active")
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .first()
    )


def _active_model_name(db: DBSession) -> str | None:
    deployment = _active_deployment(db)
    return deployment.model_version.version_name if deployment else None


def production_verification(deployment: Deployment | None) -> tuple[bool | None, str | None]:
    """(verified, warning) for the serving model — the anti-bypass signal.

    Unverified when the active deployment came from the bootstrap path without
    a passing gate, or when a later gate run failed for the serving model.
    """
    if deployment is None:
        return None, None
    model = deployment.model_version
    metadata = dict(deployment.metadata_json or {})
    model_meta = dict(model.metadata_json or {})
    if model.eval_status == "failed":
        alert = model_meta.get("gate_alert") or {}
        return False, (
            "Yayındaki model son eval kapısından KALDI"
            + (f" (run {alert.get('eval_run_id')})" if alert.get("eval_run_id") else "")
            + " — rollback veya yeni model önerilir."
        )
    if metadata.get("action") == "bootstrap" and model.eval_status != "passed":
        return False, (
            "Yayındaki model eval kapısından geçmeden (bootstrap) yüklendi — "
            "doğrulamak için eval çalıştırın."
        )
    return True, None


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

    verified, warning = production_verification(_active_deployment(db))

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
        production_verified=verified,
        production_warning=warning,
    )


@router.get("/serving-status")
def serving_status(db: DBSession = Depends(get_db)):
    """Live production model serving state for the supervisor panel banner."""
    snap = serving_state.snapshot()
    snap["active_model"] = _active_model_name(db) or snap.get("active_model")
    snap["vllm_mode"] = settings.vllm_mode
    return snap
