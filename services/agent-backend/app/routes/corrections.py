"""Correction endpoints — save, apply to correction_memory, create training candidate."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core.candidate_builder import build_candidate_from_turn
from app.core.review_compiler import compile_instruction
from app.db import get_db
from app.models import Correction, CorrectionMemory, Session as SessionModel, TrainingCandidate, Turn
from app.schemas import (
    CompileReviewInstructionRequest,
    CompileReviewInstructionResponse,
    CreateCorrectionRequest,
    CorrectionResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/review-compiler/compile",
    response_model=CompileReviewInstructionResponse,
)
def compile_review_instruction(req: CompileReviewInstructionRequest):
    return compile_instruction(
        req.instruction,
        customer_text=req.customer_text,
        agent_response=req.agent_response,
        current_next_action=req.current_next_action,
    ).as_dict()


@router.get("/corrections", response_model=list[CorrectionResponse])
def list_corrections(limit: int = 50, db: DBSession = Depends(get_db)):
    return (
        db.query(Correction)
        .order_by(Correction.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/corrections/{correction_id}", response_model=CorrectionResponse)
def get_correction(correction_id: int, db: DBSession = Depends(get_db)):
    c = db.query(Correction).filter(Correction.id == correction_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Correction not found")
    return c


@router.post("/corrections", response_model=CorrectionResponse)
def create_correction(req: CreateCorrectionRequest, db: DBSession = Depends(get_db)):
    # Validate FK references before INSERT
    if req.session_id is not None:
        if not db.query(SessionModel).filter(SessionModel.id == req.session_id).first():
            raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")
    if req.turn_id is not None:
        turn_check = db.query(Turn).filter(Turn.id == req.turn_id).first()
        if not turn_check:
            raise HTTPException(status_code=404, detail=f"Turn {req.turn_id} not found")
        if req.session_id is not None and turn_check.session_id != req.session_id:
            raise HTTPException(status_code=400, detail="Turn does not belong to the given session")

    try:
        # 1. Save correction — flush only to obtain the generated id
        correction = Correction(
            session_id=req.session_id,
            turn_id=req.turn_id,
            correction_type=req.correction_type,
            old_agent_response=req.old_agent_response,
            corrected_agent_response=req.corrected_agent_response,
            old_next_action=req.old_next_action,
            corrected_next_action=req.corrected_next_action,
            notes=req.notes,
            apply_immediately=req.apply_immediately,
            send_to_training=req.send_to_training,
            approved=True,
            created_by="panel",
        )
        db.add(correction)
        db.flush()  # assigns correction.id without committing
        logger.info("Correction staged: id=%d type=%s", correction.id, correction.correction_type)

        # 2. apply_immediately -> upsert correction_memory (same transaction)
        if req.apply_immediately and req.corrected_agent_response:
            trigger_key = _derive_trigger_key(req, db)
            context_json = _derive_context(req, db)
            existing = (
                db.query(CorrectionMemory)
                .filter(
                    CorrectionMemory.trigger_key == trigger_key,
                    CorrectionMemory.active == True,  # noqa: E712
                )
                .first()
            )
            if existing:
                existing.correct_response = req.corrected_agent_response
                existing.correct_next_action = req.corrected_next_action
                existing.source_correction_id = correction.id
                existing.context_json = context_json
                logger.info("correction_memory updated: trigger=%s", trigger_key)
            else:
                mem = CorrectionMemory(
                    trigger_key=trigger_key,
                    context_json=context_json,
                    correct_response=req.corrected_agent_response,
                    correct_next_action=req.corrected_next_action,
                    source_correction_id=correction.id,
                    active=True,
                    priority=10,
                )
                db.add(mem)
                logger.info("correction_memory staged: trigger=%s", trigger_key)

        # 3. send_to_training -> create training_candidate (same transaction)
        if req.send_to_training and req.turn_id:
            candidate = _build_training_candidate(req, db)
            if candidate:
                db.add(candidate)
                logger.info("training_candidate staged: turn_id=%d", req.turn_id)

        # Single commit — all three objects land atomically or none do.
        db.commit()
        db.refresh(correction)
        logger.info(
            "Correction committed: id=%d apply_immediately=%s send_to_training=%s",
            correction.id,
            req.apply_immediately,
            req.send_to_training,
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to persist correction — transaction rolled back")
        raise HTTPException(status_code=500, detail="Failed to save correction")

    return correction


# ── Helpers ──────────────────────────────────────────────────────────────────

def _derive_trigger_key(req: CreateCorrectionRequest, db: DBSession) -> str:
    """Use the turn's intent as trigger_key; fall back to correction_type."""
    if req.turn_id:
        turn = db.query(Turn).filter(Turn.id == req.turn_id).first()
        if turn and turn.intent:
            return turn.intent
    return req.correction_type


def _derive_context(req: CreateCorrectionRequest, db: DBSession) -> dict:
    """Persist auditable matching context for the correction memory entry."""
    if req.turn_id:
        turn = db.query(Turn).filter(Turn.id == req.turn_id).first()
        if turn:
            return {
                "intent": turn.intent,
                "customer_text": turn.customer_text,
            }
    return {"correction_type": req.correction_type}


def _build_training_candidate(req: CreateCorrectionRequest, db: DBSession):
    """Return a TrainingCandidate ORM instance built from turn data."""
    turn = db.query(Turn).filter(Turn.id == req.turn_id).first()
    if not turn:
        return None

    built = build_candidate_from_turn(
        turn=turn,
        corrected_response=req.corrected_agent_response or turn.agent_response or "",
        corrected_next_action=req.corrected_next_action or turn.next_action or "",
        correction_type=req.correction_type,
        model_version=settings.model_active_version,
    )
    return TrainingCandidate(
        source_type="correction",
        source_id=req.turn_id,
        messages_json=built["messages"],
        metadata_json=built["metadata"],
        approved=True,
    )
