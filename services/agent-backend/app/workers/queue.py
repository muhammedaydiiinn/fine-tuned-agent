"""Redis job kuyruğu yardımcıları — Milestone 4'te training-worker tarafından kullanılacak."""
import json
import logging

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

TRAINING_QUEUE = "agent:training_jobs"
EVAL_QUEUE = "agent:eval_jobs"


def _get_client() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def enqueue_training_job(job_type: str, payload: dict) -> str:
    """Training job'ı kuyruğa ekler, job_id döndürür."""
    import uuid
    job_id = str(uuid.uuid4())
    message = json.dumps({"job_id": job_id, "job_type": job_type, "payload": payload})
    try:
        r = _get_client()
        r.rpush(TRAINING_QUEUE, message)
        logger.info("Training job kuyruğa eklendi: %s (%s)", job_id, job_type)
    except redis_lib.RedisError as exc:
        logger.error("Redis enqueue hatası: %s", exc)
        raise
    return job_id


def enqueue_eval_job(eval_run_id: int, model_version_id: int) -> str:
    """Enqueue an eval run and return the queue message ID."""
    import uuid
    job_id = str(uuid.uuid4())
    message = json.dumps({
        "job_id": job_id,
        "job_type": "run_eval",
        "payload": {
            "eval_run_id": eval_run_id,
            "model_version_id": model_version_id,
        },
    })
    try:
        r = _get_client()
        r.rpush(EVAL_QUEUE, message)
        logger.info(
            "Eval job enqueued: %s (run=%d model=%d)",
            job_id,
            eval_run_id,
            model_version_id,
        )
    except redis_lib.RedisError as exc:
        logger.error("Redis enqueue hatası: %s", exc)
        raise
    return job_id


def peek_queue(queue_name: str, count: int = 10) -> list[dict]:
    """Kuyruktan öğeleri silmeden gösterir (supervisor panel için)."""
    try:
        r = _get_client()
        raw_items = r.lrange(queue_name, 0, count - 1)
        return [json.loads(item) for item in raw_items]
    except redis_lib.RedisError as exc:
        logger.error("Redis peek hatası: %s", exc)
        return []
