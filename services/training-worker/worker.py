"""Training worker — Milestone 4'te Redis'ten iş alıp LoRA eğitimi yapacak.

Şu an: kuyruktan mesaj okur, job tipine göre stub handler çağırır.
"""
import json
import logging
import os
import time

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger("training-worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_NAME = "agent:training_jobs"
POLL_INTERVAL = 5  # saniye


def handle_build_dataset(payload: dict) -> dict:
    # TODO: Milestone 4 — approved candidates → JSONL dataset
    logger.info("build_dataset stub: %s", payload)
    return {"status": "dry_run_ok"}


def handle_train_lora_dry_run(payload: dict) -> dict:
    # TODO: Milestone 4 — gerçek Unsloth LoRA eğitimi
    logger.info("train_lora_dry_run stub: %s", payload)
    return {"status": "dry_run_ok"}


def handle_merge_model_dry_run(payload: dict) -> dict:
    # TODO: Milestone 4 — adapter merge + merged_16bit export
    logger.info("merge_model_dry_run stub: %s", payload)
    return {"status": "dry_run_ok"}


HANDLERS = {
    "build_dataset":         handle_build_dataset,
    "train_lora_dry_run":    handle_train_lora_dry_run,
    "merge_model_dry_run":   handle_merge_model_dry_run,
}


def main():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    logger.info("Training worker başladı. Kuyruk: %s", QUEUE_NAME)

    while True:
        item = r.blpop(QUEUE_NAME, timeout=POLL_INTERVAL)
        if item is None:
            continue

        _, raw = item
        try:
            message = json.loads(raw)
            job_id = message.get("job_id", "?")
            job_type = message.get("job_type", "")
            payload = message.get("payload", {})

            logger.info("İş alındı: %s (%s)", job_id, job_type)
            handler = HANDLERS.get(job_type)
            if handler:
                result = handler(payload)
                logger.info("İş tamamlandı: %s → %s", job_id, result)
            else:
                logger.warning("Bilinmeyen job tipi: %s", job_type)

        except Exception as exc:
            logger.error("İş işleme hatası: %s", exc)


if __name__ == "__main__":
    main()
