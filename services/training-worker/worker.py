"""Training worker — consumes training_pipeline jobs from Redis and runs LoRA training."""
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import redis
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from job_lifecycle import (
    PROCESSING_QUEUE_NAME,
    QUEUE_NAME,
    is_terminal_status,
    next_version_name,
    requeue_interrupted_jobs,
)
from jobs import artifacts, build_dataset, merge_model, model_registration, train_lora
from models import Deployment, EvalRun, ModelVersion, TrainingCandidate, TrainingJob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("redis").setLevel(logging.WARNING)
logger = logging.getLogger("training-worker")

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

def _active_model(db: Session) -> ModelVersion | None:
    """Return the currently deployed production ModelVersion, or None."""
    deployment = (
        db.query(Deployment)
        .filter(Deployment.environment == "production", Deployment.status == "active")
        .order_by(Deployment.id.desc())
        .first()
    )
    if not deployment:
        return None
    return db.query(ModelVersion).filter(ModelVersion.id == deployment.model_version_id).first()


def _queue_quality_eval(
    db: Session,
    *,
    job: TrainingJob,
    model: ModelVersion,
    artifact_sha256: str,
    artifact_root: str,
    log_path: str,
) -> int:
    """Create and enqueue one eval run, releasing the batch on enqueue failure."""
    existing_run = (
        db.query(EvalRun)
        .filter(EvalRun.model_version_id == model.id)
        .order_by(EvalRun.id.desc())
        .first()
    )
    if existing_run:
        return existing_run.id

    serving = (model.metadata_json or {}).get("serving") or {}
    deployment_evidence = {
        "artifact_sha256": artifact_sha256,
        "artifact_root": artifact_root,
        "serving_target": serving,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    eval_run = EvalRun(
        model_version_id=model.id,
        status="pending",
        metrics_json={"deployment_evidence": deployment_evidence},
        progress_current=0,
        progress_total=15,
    )
    db.add(eval_run)
    db.commit()
    db.refresh(eval_run)
    try:
        import uuid as _uuid
        queue = redis.from_url(settings.redis_url, decode_responses=True)
        queue.rpush(
            "anruf:eval_jobs",
            json.dumps({
                "job_id": str(_uuid.uuid4()),
                "job_type": "run_eval",
                "payload": {
                    "eval_run_id": eval_run.id,
                    "model_version_id": model.id,
                },
            }),
        )
        _log(log_path, f"quality check queued — eval_run_id={eval_run.id}")
    except Exception as exc:
        eval_run.status = "failed"
        eval_run.error_message = f"Failed to enqueue quality check: {exc}"[:1000]
        model.eval_status = "failed"
        metadata = dict(model.metadata_json or {})
        metadata["lifecycle_status"] = "candidate"
        metadata["eval_enqueue_error"] = str(exc)[:1000]
        model.metadata_json = metadata
        db.query(TrainingCandidate).filter(
            TrainingCandidate.training_job_id == job.id,
            TrainingCandidate.model_version_id.is_(None),
        ).update({"training_job_id": None}, synchronize_session="fetch")
        db.commit()
        _log(log_path, f"quality check enqueue failed — {exc}")
    return eval_run.id


def handle_train_pipeline(db: Session, job_db_id: int, payload: dict) -> None:
    job = db.query(TrainingJob).filter(TrainingJob.id == job_db_id).first()
    if not job:
        logger.error("TrainingJob id=%d not found in DB", job_db_id)
        return
    if is_terminal_status(job.status):
        logger.info(
            "TrainingJob id=%d is terminal (%s); skipping duplicate delivery",
            job_db_id,
            job.status,
        )
        return
    if job.model_version_id is not None:
        model = db.query(ModelVersion).filter(ModelVersion.id == job.model_version_id).first()
        if not model:
            raise ValueError(
                f"TrainingJob id={job_db_id} references missing ModelVersion id={job.model_version_id}"
            )
        log_path = job.logs_path or str(
            Path(settings.data_dir) / "training_logs" / f"{job_db_id}.log"
        )
        artifact = artifacts.directory_manifest(model.merged_path)
        eval_run_id = _queue_quality_eval(
            db,
            job=job,
            model=model,
            artifact_sha256=artifact["sha256"],
            artifact_root=model.merged_path,
            log_path=log_path,
        )
        output = dict(job.output_json or {})
        output.update({
            "model_version_id": model.id,
            "eval_run_id": eval_run_id,
            "version_name": model.version_name,
        })
        _update_job(
            db,
            job,
            status="completed",
            finished_at=datetime.now(timezone.utc),
            progress_current=100,
            output_json=output,
        )
        logger.warning(
            "Recovered training job after model commit: job_id=%d model_id=%d",
            job_db_id,
            model.id,
        )
        return

    dataset_version = payload.get("dataset_version", f"ds-job{job_db_id}")

    # Use the parent snapshot captured at job-creation time (in input_json) so we
    # always train on the model that was live when the user pressed Eğit — not
    # whatever is deployed when the worker finally picks this job up.
    input_json = job.input_json or {}
    snapped_parent_id = input_json.get("parent_model_version_id")
    snapped_parent_name = input_json.get("parent_version_name")

    if snapped_parent_id and snapped_parent_name:
        snapped_mv = db.query(ModelVersion).filter(ModelVersion.id == snapped_parent_id).first()
        if snapped_mv and snapped_mv.merged_path:
            parent_version_name = snapped_parent_name
            parent_model_id = snapped_parent_id
            base_model_path = snapped_mv.merged_path
        else:
            parent_version_name = snapped_parent_name
            parent_model_id = snapped_parent_id
            base_model_path = str(Path(settings.model_dir) / "merged" / snapped_parent_name)
    else:
        # Fallback: resolve from active deployment (pre-snapshot jobs or first boot)
        parent_model = _active_model(db)
        if parent_model and parent_model.merged_path:
            parent_version_name = parent_model.version_name
            parent_model_id = parent_model.id
            base_model_path = parent_model.merged_path
        else:
            parent_version_name = settings.model_active_version
            parent_model_id = None
            base_model_path = str(Path(settings.model_dir) / "merged" / settings.model_active_version)

    new_version_name = next_version_name(db, parent_version_name, job_db_id)

    dataset_path = str(Path(settings.data_dir) / "datasets" / f"{dataset_version}.jsonl")
    adapter_path = str(Path(settings.model_dir) / "adapters" / new_version_name)
    merged_path = str(Path(settings.model_dir) / "merged" / new_version_name)
    adapter_partial_path = f"{adapter_path}.partial"
    merged_partial_path = f"{merged_path}.partial"
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
        for partial_path in (adapter_partial_path, merged_partial_path):
            shutil.rmtree(partial_path, ignore_errors=True)

        # Step 1: Build dataset
        _log(log_path, "step 1/3 — build_dataset")
        _update_job(db, job, progress_current=5, progress_total=100)
        ds_result = build_dataset.build(
            db,
            dataset_path,
            dataset_version,
            data_dir=settings.data_dir,
            candidate_ids=payload.get("candidate_ids") or None,
        )
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

        train_result = train_lora.train(
            dataset_path,
            adapter_partial_path,
            train_cfg,
            _progress_cb,
        )
        _log(log_path, f"training done — steps={train_result.get('steps')} mode={train_result.get('mode')}")
        _update_job(db, job, progress_current=80)

        # Step 3: Merge model
        _log(log_path, "step 3/3 — merge_model")
        merge_result = merge_model.merge(
            base_model_path,
            adapter_partial_path,
            merged_partial_path,
            settings.training_mode,
        )
        _log(log_path, f"merge done → {merged_partial_path}")
        _update_job(db, job, progress_current=95)

        # Publish complete artifacts atomically only after train and merge pass.
        shutil.rmtree(adapter_path, ignore_errors=True)
        shutil.rmtree(merged_path, ignore_errors=True)
        Path(adapter_partial_path).replace(adapter_path)
        Path(merged_partial_path).replace(merged_path)
        train_result["adapter_path"] = adapter_path
        merge_result["merged_path"] = merged_path
        artifact_manifest = {
            "dataset": artifacts.file_manifest(dataset_path),
            "adapter": artifacts.directory_manifest(adapter_path),
            "merged": artifacts.directory_manifest(merged_path),
        }
        candidate_publish_manifest = None
        candidate_publication = None
        if settings.training_mode == "real":
            _log(
                log_path,
                "publishing merged model to candidate serving path "
                f"{settings.candidate_publish_path}",
            )
            candidate_publication = artifacts.begin_directory_publication(
                merged_path,
                settings.candidate_publish_path,
            )
            candidate_publish_manifest = candidate_publication.manifest
            artifact_manifest["candidate_serving"] = candidate_publish_manifest
            _log(
                log_path,
                "candidate serving path staged — "
                f"{settings.candidate_publish_path}",
            )

        # Register ModelVersion. Keep the previous candidate serving tree until
        # this commit succeeds so a database failure cannot leave an untracked
        # model active at the stable serving path.
        existing = db.query(ModelVersion).filter(ModelVersion.version_name == new_version_name).first()
        if not existing:
            serving_mode = "mock" if settings.training_mode == "mock" else "real"
            mv = ModelVersion(
                version_name=new_version_name,
                base_model=parent_version_name,
                lora_path=adapter_path,
                merged_path=merged_path,
                dataset_version=dataset_version,
                eval_status="pending",
                deployment_status="inactive",
                parent_model_version_id=parent_model_id,
                metadata_json={
                    "lifecycle_status": "candidate",
                    "job_id": job_db_id,
                    "parent_version": parent_version_name,
                    "train_steps": train_result.get("steps"),
                    "row_count": ds_result["row_count"],
                    "training_mode": settings.training_mode,
                    "artifact_manifest": {
                        "valid": True,
                        **artifact_manifest["merged"],
                    },
                    "dataset_manifest": ds_result,
                    "pipeline_artifacts": artifact_manifest,
                    "candidate_publish_manifest": candidate_publish_manifest,
                    # Candidate is evaluated as a runtime LoRA adapter on the
                    # shared production vLLM server: same base, only the small
                    # adapter is loaded, so eval needs no second model and no
                    # production downtime. The adapter dir is mounted at
                    # /adapters/<version> inside that server.
                    "serving": (
                        {
                            "mode": "mock",
                            "base_url": "",
                            "model_name": new_version_name,
                            "slot": "mock",
                        }
                        if settings.training_mode == "mock"
                        else {
                            "mode": "real",
                            "base_url": settings.candidate_lora_base_url,
                            "model_name": new_version_name,
                            "slot": "candidate-lora",
                            "lora_path": f"/adapters/{Path(adapter_path).name}",
                        }
                    ),
                },
            )
            model_version_id = model_registration.commit_model_version(
                db,
                mv,
                candidate_publication,
            )
            if candidate_publication is not None:
                _log(
                    log_path,
                    "candidate serving path committed — "
                    f"{settings.candidate_publish_path}",
                )
        else:
            if candidate_publication is not None:
                candidate_publication.finalize()
                _log(
                    log_path,
                    "candidate serving path committed for existing model — "
                    f"{settings.candidate_publish_path}",
                )
            model_version_id = existing.id

        # Link the job to its produced model version so deploy/bake/release can find it
        job.model_version_id = model_version_id
        db.commit()

        # Auto-trigger quality eval (both mock and real mode).
        model = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
        eval_run_id = _queue_quality_eval(
            db,
            job=job,
            model=model,
            artifact_sha256=artifact_manifest["merged"].get("sha256", ""),
            artifact_root=merged_path,
            log_path=log_path,
        )

        _log(
            log_path,
            f"ModelVersion created — id={model_version_id} name={new_version_name}"
            f" parent={parent_version_name}",
        )
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
                "parent_model_version_id": parent_model_id,
                "eval_run_id": eval_run_id,
                "version_name": new_version_name,
                "parent_version": parent_version_name,
                **ds_result,
                **train_result,
                **merge_result,
                "artifact_manifest": artifact_manifest,
            },
        )
        logger.info(
            "Pipeline completed: job_id=%d version=%s parent=%s",
            job_db_id,
            new_version_name,
            parent_version_name,
        )

    except Exception as exc:
        shutil.rmtree(adapter_partial_path, ignore_errors=True)
        shutil.rmtree(merged_partial_path, ignore_errors=True)
        msg = str(exc)
        logger.error("Pipeline failed: job_id=%d error=%s", job_db_id, msg)
        _log(log_path, f"ERROR — {msg}")
        # Release candidates so they re-enter the active batch and can be retrained.
        # Only release candidates that are still locked into this job but not yet
        # baked into a model version (model_version_id IS NULL).
        try:
            released = (
                db.query(TrainingCandidate)
                .filter(
                    TrainingCandidate.training_job_id == job_db_id,
                    TrainingCandidate.model_version_id.is_(None),
                )
                .update({"training_job_id": None}, synchronize_session="fetch")
            )
            logger.info("Released %d candidates after pipeline failure — job_id=%d", released, job_db_id)
        except Exception as rel_exc:
            logger.error("Failed to release candidates after pipeline failure: %s", rel_exc)
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
    recovered = requeue_interrupted_jobs(r)
    logger.info("Training worker started. Queue: %s | mode: %s", QUEUE_NAME, settings.training_mode)
    if recovered:
        logger.warning("Recovered %d interrupted training job(s)", recovered)

    while True:
        raw = r.brpoplpush(
            QUEUE_NAME,
            PROCESSING_QUEUE_NAME,
            timeout=POLL_INTERVAL,
        )
        if raw is None:
            continue

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
        else:
            r.lrem(PROCESSING_QUEUE_NAME, 1, raw)


if __name__ == "__main__":
    main()
