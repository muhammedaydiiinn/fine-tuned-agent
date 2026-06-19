"""Eval worker — Milestone 5'te sabit senaryo değerlendirmesi yapacak.

Şu an: kuyruktan iş alır, stub handler çağırır.
"""
import json
import logging
import os

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger("eval-worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_NAME = "anruf:eval_jobs"
POLL_INTERVAL = 5


def handle_eval(model_version_id: int) -> dict:
    # TODO: Milestone 5 — scenarios.jsonl'i çalıştır, metrikleri hesapla
    logger.info("eval stub: model_version_id=%d", model_version_id)
    return {"status": "dry_run_ok", "model_version_id": model_version_id}


def main():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    logger.info("Eval worker başladı. Kuyruk: %s", QUEUE_NAME)

    while True:
        item = r.blpop(QUEUE_NAME, timeout=POLL_INTERVAL)
        if item is None:
            continue

        _, raw = item
        try:
            message = json.loads(raw)
            job_id = message.get("job_id", "?")
            model_version_id = message.get("model_version_id", 0)
            logger.info("Eval iş alındı: %s (model=%d)", job_id, model_version_id)
            result = handle_eval(model_version_id)
            logger.info("Eval tamamlandı: %s → %s", job_id, result)
        except Exception as exc:
            logger.error("Eval iş hatası: %s", exc)


if __name__ == "__main__":
    main()
