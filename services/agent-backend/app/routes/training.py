"""Training candidate and job endpoints — Milestone 3."""
import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.candidate_builder import build_candidate_from_turn
from app.db import get_db
from app.models import Correction, TrainingCandidate, TrainingJob, Turn
from app.schemas import (
    CreateTrainingJobRequest,
    ExportResult,
    TrainingCandidateResponse,
    TrainingJobResponse,
)
from app.workers.queue import enqueue_training_job

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Training candidates ──────────────────────────────────────────────────────

@router.post(
    "/training-candidates/from-correction/{correction_id}",
    response_model=TrainingCandidateResponse,
    summary="Build a training candidate from an existing correction",
)
def candidate_from_correction(correction_id: int, db: DBSession = Depends(get_db)):
    correction = db.query(Correction).filter(Correction.id == correction_id).first()
    if not correction:
        raise HTTPException(status_code=404, detail="Correction not found")

    if not correction.turn_id:
        raise HTTPException(status_code=422, detail="Correction has no linked turn_id")

    turn = db.query(Turn).filter(Turn.id == correction.turn_id).first()
    if not turn:
        raise HTTPException(status_code=404, detail="Related Turn not found")

    existing = (
        db.query(TrainingCandidate)
        .filter(
            TrainingCandidate.source_type == "correction",
            TrainingCandidate.source_id == correction.turn_id,
        )
        .first()
    )
    if existing:
        logger.info("Candidate already exists for turn_id=%d", correction.turn_id)
        return existing

    built = build_candidate_from_turn(
        turn=turn,
        corrected_response=correction.corrected_agent_response or turn.agent_response or "",
        corrected_next_action=correction.corrected_next_action or turn.next_action or "",
        correction_type=correction.correction_type,
        model_version=settings.model_active_version,
    )
    candidate = TrainingCandidate(
        source_type="correction",
        source_id=correction.turn_id,
        messages_json=built["messages"],
        metadata_json=built["metadata"],
        approved=True,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    logger.info("training_candidate created: id=%d correction_id=%d", candidate.id, correction_id)
    return candidate


@router.get(
    "/training-candidates",
    response_model=list[TrainingCandidateResponse],
    summary="List training candidates",
)
def list_candidates(
    approved: bool | None = None,
    exported: bool | None = None,
    source_type: str | None = None,
    limit: int = 100,
    db: DBSession = Depends(get_db),
):
    q = db.query(TrainingCandidate)
    if approved is not None:
        q = q.filter(TrainingCandidate.approved == approved)
    if exported is not None:
        q = q.filter(TrainingCandidate.exported == exported)
    if source_type:
        q = q.filter(TrainingCandidate.source_type == source_type)
    return q.order_by(TrainingCandidate.created_at.desc()).limit(limit).all()


@router.get(
    "/training-candidates/{candidate_id}",
    response_model=TrainingCandidateResponse,
    summary="Get a single training candidate",
)
def get_candidate(candidate_id: int, db: DBSession = Depends(get_db)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return c


@router.post(
    "/training-candidates/{candidate_id}/approve",
    response_model=TrainingCandidateResponse,
    summary="Approve a training candidate",
)
def approve_candidate(candidate_id: int, db: DBSession = Depends(get_db)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.approved = True
    db.commit()
    db.refresh(c)
    logger.info("training_candidate approved: id=%d", candidate_id)
    return c


@router.post(
    "/training-candidates/{candidate_id}/reject",
    response_model=TrainingCandidateResponse,
    summary="Reject a training candidate",
)
def reject_candidate(candidate_id: int, db: DBSession = Depends(get_db)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.approved = False
    db.commit()
    db.refresh(c)
    logger.info("training_candidate rejected: id=%d", candidate_id)
    return c


@router.post(
    "/training-candidates/export-jsonl",
    response_model=ExportResult,
    summary="Export approved candidates to JSONL",
)
def export_jsonl(db: DBSession = Depends(get_db)):
    """Write approved, not-yet-exported candidates to a JSONL file.

    Output path: {data_dir}/training_candidates/dataset_{model_version}_{seq:04d}.jsonl
    Each line: {"messages": [...]} as valid JSON.
    """
    candidates = (
        db.query(TrainingCandidate)
        .filter(TrainingCandidate.approved == True, TrainingCandidate.exported == False)  # noqa: E712
        .order_by(TrainingCandidate.created_at.asc())
        .all()
    )

    if not candidates:
        raise HTTPException(status_code=422, detail="No approved candidates pending export")

    out_dir = Path(settings.data_dir) / "training_candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_count = len(list(out_dir.glob(f"dataset_{settings.model_active_version}_*.jsonl")))
    seq = existing_count + 1
    file_path = out_dir / f"dataset_{settings.model_active_version}_{seq:04d}.jsonl"

    # Mark candidates exported in-transaction BEFORE writing the file.
    # Write to a temp file first, then atomically rename into place so that
    # a failed commit never leaves a partial file on disk.
    exported_ids: list[int] = [c.id for c in candidates]
    for c in candidates:
        c.exported = True

    tmp_path: str | None = None
    try:
        # Write JSONL to a sibling temp file, then atomically swap into final path.
        fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".tmp_export_", suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for c in candidates:
                    fh.write(json.dumps({"messages": c.messages_json}, ensure_ascii=False) + "\n")
        except Exception:
            os.unlink(tmp_path)
            tmp_path = None
            raise

        # Commit the DB state (exported=True), then rename.
        db.commit()
        os.replace(tmp_path, file_path)
        tmp_path = None
    except Exception:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        db.rollback()
        logger.exception("JSONL export failed — transaction rolled back")
        raise HTTPException(status_code=500, detail="Export failed")

    logger.info("JSONL export complete: file=%s rows=%d", file_path, len(exported_ids))
    return ExportResult(
        file_path=str(file_path),
        count=len(exported_ids),
        exported_ids=exported_ids,
    )


# ── Training jobs ─────────────────────────────────────────────────────────────

@router.post(
    "/training-jobs",
    response_model=TrainingJobResponse,
    summary="Create a training job and enqueue it",
    status_code=201,
)
def create_training_job(
    body: CreateTrainingJobRequest,
    db: DBSession = Depends(get_db),
):
    input_data: dict = {}
    if body.dataset_version:
        input_data["dataset_version"] = body.dataset_version
    for field in ("lora_rank", "lora_alpha", "epochs", "lr", "batch_size"):
        val = getattr(body, field)
        if val is not None:
            input_data[field] = val
    if body.session_id is not None:
        input_data["session_id"] = body.session_id
    if body.candidate_ids:
        candidate_ids = sorted(set(body.candidate_ids))
        approved_count = (
            db.query(TrainingCandidate)
            .filter(
                TrainingCandidate.id.in_(candidate_ids),
                TrainingCandidate.approved == True,  # noqa: E712
            )
            .count()
        )
        if approved_count != len(candidate_ids):
            raise HTTPException(
                status_code=422,
                detail="Every requested candidate must exist and be approved",
            )
        input_data["candidate_ids"] = candidate_ids

    # Stage the job first so we have a DB-generated id for the queue payload,
    # then attempt to enqueue. If enqueueing fails the job is rolled back —
    # no orphaned "pending" record stays in the DB.
    job = TrainingJob(
        job_type="train_pipeline",
        status="pending",
        input_json=input_data or None,
        progress_current=0,
        progress_total=100,
    )
    db.add(job)
    db.flush()  # assigns job.id without committing

    payload = {
        "job_id": job.id,
        "dataset_version": body.dataset_version or f"ds-job{job.id}",
        "candidate_ids": input_data.get("candidate_ids") or [],
        "session_id": body.session_id,
    }
    try:
        enqueue_training_job("train_pipeline", payload)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to enqueue training job — transaction rolled back")
        raise HTTPException(status_code=503, detail="Training queue is unavailable") from exc

    db.commit()
    db.refresh(job)
    logger.info("training_job created and enqueued: id=%d", job.id)
    return job


@router.get(
    "/training-jobs",
    response_model=list[TrainingJobResponse],
    summary="List training jobs",
)
def list_training_jobs(
    status: str | None = None,
    limit: int = 50,
    db: DBSession = Depends(get_db),
):
    q = db.query(TrainingJob)
    if status:
        q = q.filter(TrainingJob.status == status)
    return q.order_by(TrainingJob.created_at.desc()).limit(limit).all()


@router.get(
    "/training-jobs/{job_id}",
    response_model=TrainingJobResponse,
    summary="Get a single training job",
)
def get_training_job(job_id: int, db: DBSession = Depends(get_db)):
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    return job


@router.get(
    "/training-jobs/{job_id}/logs",
    summary="Stream tail of training job log file",
)
def get_job_logs(job_id: int, tail: int = 100, db: DBSession = Depends(get_db)):
    """Return the last N lines of the training log.

    Response: {"job_id": int, "status": str, "logs": str}
    """
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")

    logs = ""
    if job.logs_path:
        data_root = Path(settings.data_dir).resolve()
        resolved = Path(job.logs_path).resolve()
        try:
            resolved.relative_to(data_root)
        except ValueError:
            logger.warning(
                "Path traversal blocked for training log: raw=%r resolved=%s",
                job.logs_path,
                resolved,
            )
            raise HTTPException(
                status_code=400,
                detail="Requested log path is outside the allowed data directory",
            )
        try:
            if resolved.exists():
                lines = resolved.read_text(encoding="utf-8").splitlines()
                logs = "\n".join(lines[-tail:])
        except OSError as exc:
            logger.warning("Could not read training log %s: %s", job.logs_path, exc)
            logs = f"[log read error: {exc}]"

    return {"job_id": job_id, "status": job.status, "logs": logs}
