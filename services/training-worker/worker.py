"""Training worker — consumes training_pipeline jobs from Redis and runs LoRA training."""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import redis
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from jobs import build_dataset, merge_model, train_lora
from models import ModelVersion, TrainingJob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger("training-worker")

QUEUE_NAME = "anruf:training_jobs"
POLL_INTERVAL = 5


# ── DB helpers ────────────────────────────────────────────────────────────────

def _update_job(db: Session, job: TrainingJob, **fields) -> None:
    for k, v in fields.items():
        setattr(job, k, v)
    db.commit()


def _log(log_path: str, message: str) -> None:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


# ── Pipeline handler ──────────────────────────────────────────────────────────

def handle_train_pipeline(db: Session, job_db_id: int, payload: dict) -> None:
    job = db.query(TrainingJob).filter(TrainingJob.id == job_db_id).first()
    if not job:
        logger.error("TrainingJob id=%d not found in DB", job_db_id)
        return

    dataset_version = payload.get("dataset_version", f"ds-job{job_db_id}")
    short_id = str(job_db_id)
    new_version_name = f"{settings.model_active_version}-ft-{short_id}"

    dataset_path = str(Path(settings.data_dir) / "datasets" / f"{dataset_version}.jsonl")
    adapter_path = str(Path(settings.model_dir) / "adapters" / new_version_name)
    merged_path = str(Path(settings.model_dir) / "merged" / new_version_name)
    base_model_path = str(Path(settings.model_dir) / "merged" / settings.model_active_version)
    log_path = str(Path(settings.data_dir) / "training_logs" / f"{job_db_id}.log")

    _update_job(
        db, job,
        status="running",
        started_at=datetime.now(timezone.utc),
        logs_path=log_path,
        progress_current=0,
        progress_total=100,
    )
    _log(log_path, f"pipeline started — version={new_version_name} mode={settings.training_mode}")

    try:
        # Step 1: Build dataset
        _log(log_path, "step 1/3 — build_dataset")
        _update_job(db, job, progress_current=5, progress_total=100)
        ds_result = build_dataset.build(db, dataset_path, dataset_version, data_dir=settings.data_dir)
        _log(log_path, f"dataset built — {ds_result['row_count']} rows → {dataset_path}")
        _update_job(db, job, progress_current=20)

        # Step 2: Train LoRA
        _log(log_path, "step 2/3 — train_lora")

        def _progress_cb(step: int, total: int) -> None:
            pct = 20 + int(step / max(total, 1) * 60)
            _update_job(db, job, progress_current=pct)

        train_cfg = {
            "base_model_path": base_model_path,
            "lora_rank": settings.lora_rank,
            "lora_alpha": settings.lora_alpha,
            "lora_dropout": settings.lora_dropout,
            "epochs": settings.training_epochs,
            "lr": settings.training_lr,
            "batch_size": settings.training_batch_size,
            "gradient_accumulation_steps": settings.gradient_accumulation_steps,
            "max_seq_length": settings.max_seq_length,
            "warmup_ratio": settings.warmup_ratio,
            "training_mode": settings.training_mode,
        }
        # Job-level overrides from input_json
        if job.input_json:
            for k in ("lora_rank", "lora_alpha", "epochs", "lr", "batch_size"):
                if k in job.input_json:
                    train_cfg[k] = job.input_json[k]

        train_result = train_lora.train(dataset_path, adapter_path, train_cfg, _progress_cb)
        _log(log_path, f"training done — steps={train_result.get('steps')} mode={train_result.get('mode')}")
        _update_job(db, job, progress_current=80)

        # Step 3: Merge model
        _log(log_path, "step 3/3 — merge_model")
        merge_result = merge_model.merge(base_model_path, adapter_path, merged_path, settings.training_mode)
        _log(log_path, f"merge done → {merged_path}")
        _update_job(db, job, progress_current=95)

        # Register ModelVersion
        existing = db.query(ModelVersion).filter(ModelVersion.version_name == new_version_name).first()
        if not existing:
            mv = ModelVersion(
                version_name=new_version_name,
                base_model=settings.model_active_version,
                lora_path=adapter_path,
                merged_path=merged_path,
                dataset_version=dataset_version,
                eval_status="pending",
                deployment_status="inactive",
                metadata_json={
                    "job_id": job_db_id,
                    "train_steps": train_result.get("steps"),
                    "row_count": ds_result["row_count"],
                    "training_mode": settings.training_mode,
                },
            )
            db.add(mv)
            db.commit()
            db.refresh(mv)
            model_version_id = mv.id
        else:
            model_version_id = existing.id

        _log(log_path, f"ModelVersion created — id={model_version_id} name={new_version_name}")
        _update_job(
            db, job,
            status="completed",
            finished_at=datetime.now(timezone.utc),
            progress_current=100,
            output_json={
                "dataset_path": dataset_path,
                "adapter_path": adapter_path,
                "merged_path": merged_path,
                "model_version_id": model_version_id,
                "version_name": new_version_name,
                **ds_result,
                **train_result,
                **merge_result,
            },
        )
        logger.info("Pipeline completed: job_id=%d version=%s", job_db_id, new_version_name)

    except Exception as exc:
        msg = str(exc)
        logger.error("Pipeline failed: job_id=%d error=%s", job_db_id, msg)
        _log(log_path, f"ERROR — {msg}")
        _update_job(
            db, job,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message=msg[:1000],
        )


# ── Main loop ─────────────────────────────────────────────────────────────────

HANDLERS = {
    "train_pipeline": handle_train_pipeline,
}


def main():
    r = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("Training worker started. Queue: %s | mode: %s", QUEUE_NAME, settings.training_mode)

    while True:
        item = r.blpop(QUEUE_NAME, timeout=POLL_INTERVAL)
        if item is None:
            continue

        _, raw = item
        try:
            message = json.loads(raw)
            job_type = message.get("job_type", "")
            payload = message.get("payload", {})
            job_db_id = payload.get("job_id")

            if not job_db_id:
                logger.warning("Message missing job_id: %s", message)
                continue

            handler = HANDLERS.get(job_type)
            if handler is None:
                logger.warning("Unknown job type: %s", job_type)
                continue

            logger.info("Job received: db_id=%s type=%s", job_db_id, job_type)
            db = get_db()
            try:
                handler(db, job_db_id, payload)
            finally:
                db.close()

        except Exception as exc:
            logger.error("Message processing error: %s", exc)


if __name__ == "__main__":
    main()
