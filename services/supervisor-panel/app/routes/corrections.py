"""Correction panel routes — save corrections from turns."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import Correction, CorrectionMemory, TrainingCandidate, Turn
from app.review_compiler_types import resolve_correction_type
from app.ui_feedback import toast_fragment

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/sessions/{session_id}/turns/{turn_id}/correct")
async def save_correction(
    session_id: int,
    turn_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
    corrected_response: str = Form(""),
    corrected_next_action: str = Form(""),
    notes: str = Form(""),
    mark_good: str = Form(""),
    mark_bad: str = Form(""),
    compiled_correction_type: str = Form(""),
    _csrf: None = Depends(require_csrf),
):
    turn = (
        db.query(Turn)
        .filter(Turn.id == turn_id, Turn.session_id == session_id)
        .first()
    )
    if not turn:
        return toast_fragment("Turn not found.", kind="error", status_code=404)

    correction_type = resolve_correction_type(compiled_correction_type)

    # ── Mark bad: sadece negatif sinyal kaydedilir, training'e gitmez ────────
    if mark_bad:
        correction = Correction(
            session_id=session_id,
            turn_id=turn_id,
            correction_type="mark_bad",
            old_agent_response=turn.agent_response,
            corrected_agent_response=turn.agent_response,
            old_next_action=turn.next_action,
            corrected_next_action=turn.next_action,
            notes=notes,
            apply_immediately=False,
            send_to_training=False,
            approved=True,
            created_by="panel",
        )
        db.add(correction)
        db.commit()
        return toast_fragment("İşaretlendi: geliştirilmeli.", kind="warning", refresh_event="panel-refresh")

    # ── Mark good: mevcut cevap zaten doğru, training'e ekle ─────────────────
    if mark_good:
        good_response = turn.agent_response or ""
        correction = Correction(
            session_id=session_id,
            turn_id=turn_id,
            correction_type="mark_good",
            old_agent_response=turn.agent_response,
            corrected_agent_response=good_response,
            old_next_action=turn.next_action,
            corrected_next_action=turn.next_action,
            notes=notes,
            apply_immediately=False,
            send_to_training=True,
            approved=True,
            created_by="panel",
        )
        db.add(correction)
        db.flush()
        candidate = _build_candidate(turn, good_response, turn.next_action or "", "mark_good")
        db.add(candidate)
        db.commit()
        return toast_fragment("Mark Good · training verisine eklendi.", kind="success", refresh_event="panel-refresh")

    # ── Düzeltme: hem correction_memory'ye hem training'e git (atomik) ───────
    correction = Correction(
        session_id=session_id,
        turn_id=turn_id,
        correction_type=correction_type,
        old_agent_response=turn.agent_response,
        corrected_agent_response=corrected_response or turn.agent_response,
        old_next_action=turn.next_action,
        corrected_next_action=corrected_next_action or turn.next_action,
        notes=notes,
        apply_immediately=True,
        send_to_training=True,
        approved=True,
        created_by="panel",
    )
    db.add(correction)
    db.flush()

    # correction_memory — anında uygulama
    if corrected_response:
        trigger_key = turn.intent or correction_type
        existing = (
            db.query(CorrectionMemory)
            .filter(
                CorrectionMemory.trigger_key == trigger_key,
                CorrectionMemory.active == True,  # noqa: E712
            )
            .first()
        )
        if existing:
            existing.correct_response = corrected_response
            existing.correct_next_action = corrected_next_action or None
            existing.source_correction_id = correction.id
            existing.context_json = {
                "intent": turn.intent,
                "customer_text": turn.customer_text,
            }
        else:
            db.add(CorrectionMemory(
                trigger_key=trigger_key,
                context_json={
                    "intent": turn.intent,
                    "customer_text": turn.customer_text,
                },
                correct_response=corrected_response,
                correct_next_action=corrected_next_action or None,
                source_correction_id=correction.id,
                active=True,
                priority=10,
            ))
        logger.info("correction_memory updated: trigger=%s", trigger_key)

    # training candidate — her düzeltmede otomatik
    candidate = _build_candidate(
        turn,
        corrected_response or turn.agent_response or "",
        corrected_next_action or turn.next_action or "",
        correction_type,
    )
    db.add(candidate)
    db.commit()
    logger.info("correction saved atomically: id=%d, candidate=%d", correction.id, candidate.id)

    return toast_fragment(
        "Düzeltme kaydedildi · canlıya uygulandı · training verisine eklendi.",
        kind="success",
        refresh_event="panel-refresh",
    )


def _build_candidate(turn: Turn, corrected_response: str, corrected_next_action: str, correction_type: str = "response_correction"):
    """Build a TrainingCandidate from a turn and corrected response."""
    import json as _json

    SYSTEM_INSTRUCTION = (
        "You are an Anrufblocker Gold Paket sales policy agent. "
        "Return ONLY a valid JSON policy object."
    )
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {
            "role": "user",
            "content": _json.dumps(
                {"customer_message": turn.customer_text, "state": turn.state_before_json or {}},
                ensure_ascii=False,
            ),
        },
        {
            "role": "assistant",
            "content": _json.dumps(
                {
                    "intent": turn.intent or "unknown",
                    "emotion": turn.emotion or "neutral",
                    "risk": turn.risk or "low",
                    "next_action": corrected_next_action or turn.next_action or "",
                    "behavior_strategy": "corrected",
                    "allowed_to_continue": (
                        turn.allowed_to_continue if turn.allowed_to_continue is not None else True
                    ),
                    "agent_response": corrected_response or turn.agent_response or "",
                    "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"},
                },
                ensure_ascii=False,
            ),
        },
    ]
    return TrainingCandidate(
        source_type="correction",
        source_id=turn.id,
        messages_json=messages,
        metadata_json={
            "source": "correction",
            "correction_type": correction_type,
            "approved": True,
            "model_version": settings.model_active_version,
        },
        approved=True,
    )
