"""Correction endpoint'leri — kaydet, correction_memory uygula, training candidate oluştur."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import Correction, CorrectionMemory, TrainingCandidate, Turn
from app.schemas import CreateCorrectionRequest, CorrectionResponse

router = APIRouter()
logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are an CallShield Gold Paket sales policy agent. "
    "Return ONLY a valid JSON policy object."
)


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
        raise HTTPException(status_code=404, detail="Correction bulunamadı")
    return c


@router.post("/corrections", response_model=CorrectionResponse)
def create_correction(req: CreateCorrectionRequest, db: DBSession = Depends(get_db)):
    # 1. Correction kaydet
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
    db.commit()
    db.refresh(correction)
    logger.info("Correction kaydedildi: id=%d type=%s", correction.id, correction.correction_type)

    # 2. apply_immediately → correction_memory upsert
    if req.apply_immediately and req.corrected_agent_response:
        trigger_key = _derive_trigger_key(req, db)
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
            logger.info("correction_memory güncellendi: trigger=%s", trigger_key)
        else:
            mem = CorrectionMemory(
                trigger_key=trigger_key,
                correct_response=req.corrected_agent_response,
                correct_next_action=req.corrected_next_action,
                source_correction_id=correction.id,
                active=True,
                priority=10,
            )
            db.add(mem)
            logger.info("correction_memory oluşturuldu: trigger=%s", trigger_key)
        db.commit()

    # 3. send_to_training → training_candidate oluştur
    if req.send_to_training and req.turn_id:
        candidate = _build_training_candidate(req, db)
        if candidate:
            db.add(candidate)
            db.commit()
            logger.info("training_candidate oluşturuldu: turn_id=%d", req.turn_id)

    return correction


# ── Yardımcı fonksiyonlar ────────────────────────────────────────────────────

def _derive_trigger_key(req: CreateCorrectionRequest, db: DBSession) -> str:
    """Turn'ün intent'ini trigger_key olarak kullan; yoksa correction_type."""
    if req.turn_id:
        turn = db.query(Turn).filter(Turn.id == req.turn_id).first()
        if turn and turn.intent:
            return turn.intent
    return req.correction_type


def _build_training_candidate(req: CreateCorrectionRequest, db: DBSession):
    """Turn verisinden JSONL-uyumlu training candidate oluşturur."""
    from app.core.product_facts import format_for_prompt

    turn = db.query(Turn).filter(Turn.id == req.turn_id).first()
    if not turn:
        return None

    assistant_policy = {
        "intent": turn.intent or "unknown",
        "emotion": turn.emotion or "neutral",
        "risk": turn.risk or "low",
        "next_action": req.corrected_next_action or turn.next_action or "",
        "behavior_strategy": "corrected",
        "allowed_to_continue": turn.allowed_to_continue if turn.allowed_to_continue is not None else True,
        "agent_response": req.corrected_agent_response or turn.agent_response or "",
        "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"},
    }

    messages = [
        {
            "role": "system",
            "content": SYSTEM_INSTRUCTION + "\n\n" + format_for_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "customer_message": turn.customer_text,
                    "state": turn.state_before_json or {},
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(assistant_policy, ensure_ascii=False),
        },
    ]

    return TrainingCandidate(
        source_type="correction",
        source_id=req.turn_id,
        messages_json=messages,
        metadata_json={
            "source": "correction",
            "approved": True,
            "model_version": settings.model_active_version,
            "correction_type": req.correction_type,
        },
        approved=True,
    )
