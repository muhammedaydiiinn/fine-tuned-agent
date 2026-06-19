"""Training candidate and job endpoints — Milestone 3."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.candidate_builder import build_candidate_from_turn
from app.db import get_db
from app.models import Correction, TrainingCandidate, Turn
from app.schemas import ExportResult, TrainingCandidateResponse

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

    exported_ids: list[int] = []
    with open(file_path, "w", encoding="utf-8") as fh:
        for c in candidates:
            line = json.dumps({"messages": c.messages_json}, ensure_ascii=False)
            fh.write(line + "\n")
            c.exported = True
            exported_ids.append(c.id)

    db.commit()
    logger.info("JSONL export complete: file=%s rows=%d", file_path, len(exported_ids))
    return ExportResult(
        file_path=str(file_path),
        count=len(exported_ids),
        exported_ids=exported_ids,
    )


# ── Training jobs (M4 stubs) ─────────────────────────────────────────────────

@router.get("/training-jobs")
def list_jobs():
    # TODO: Milestone 4 — job list
    return {"status": "not_implemented", "milestone": 4}


@router.post("/training-jobs")
def create_job():
    # TODO: Milestone 4 — enqueue job
    return {"status": "not_implemented", "milestone": 4}
