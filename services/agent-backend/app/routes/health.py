import logging

import redis as redis_lib
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.schemas import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)


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

    return HealthResponse(
        status="ok" if (db_ok and redis_ok) else "degraded",
        db=db_ok,
        redis=redis_ok,
        vllm_mode=settings.vllm_mode,
    )
