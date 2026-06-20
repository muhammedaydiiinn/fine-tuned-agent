"""Correction panel routes — list corrections and save corrections from turns."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import Correction, CorrectionMemory, TrainingCandidate, Turn

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/corrections", response_class=HTMLResponse)
def corrections_list(request: Request, db: DBSession = Depends(get_db)):
    total = db.query(Correction).count()
    memory_count = db.query(CorrectionMemory).filter(CorrectionMemory.active == True).count()  # noqa: E712
    training_count = db.query(Correction).filter(Correction.send_to_training == True).count()  # noqa: E712
    return templates.TemplateResponse(
        "corrections.html",
        {
            "request": request,
            "total_count": total,
            "memory_count": memory_count,
            "training_count": training_count,
        },
    )


@router.get("/corrections/data")
def corrections_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source for corrections table."""
    corrections = (
        db.query(Correction)
        .order_by(Correction.created_at.desc())
        .limit(500)
        .all()
    )
    rows = []
    for c in corrections:
        rows.append({
            "id": c.id,
            "correction_type": c.correction_type,
            "old_response": (c.old_agent_response or "")[:80],
            "new_response": (c.corrected_agent_response or "")[:80],
            "next_action": c.corrected_next_action or "",
            "apply_immediately": c.apply_immediately,
            "send_to_training": c.send_to_training,
            "created_at": c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if c.created_at else None,
            "session_id": c.session_id,
            "turn_id": c.turn_id,
        })
    return {"data": rows}


@router.post("/sessions/{session_id}/turns/{turn_id}/correct")
async def save_correction(
    session_id: int,
    turn_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
    corrected_response: str = Form(""),
    corrected_next_action: str = Form(""),
    notes: str = Form(""),
    apply_immediately: bool = Form(False),
    send_to_training: bool = Form(False),
    mark_good: str = Form(""),
    mark_bad: str = Form(""),
    _csrf: None = Depends(require_csrf),
):
    turn = db.query(Turn).filter(Turn.id == turn_id).first()
    if not turn:
        return HTMLResponse('<div class="alert alert-error">Turn not found.</div>')

    correction_type = "response_correction"
    if mark_good:
        correction_type = "mark_good"
        corrected_response = turn.agent_response or ""
    elif mark_bad:
        correction_type = "mark_bad"

    correction = Correction(
        session_id=session_id,
        turn_id=turn_id,
        correction_type=correction_type,
        old_agent_response=turn.agent_response,
        corrected_agent_response=corrected_response or turn.agent_response,
        old_next_action=turn.next_action,
        corrected_next_action=corrected_next_action or turn.next_action,
        notes=notes,
        apply_immediately=apply_immediately,
        send_to_training=send_to_training,
        approved=True,
        created_by="panel",
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)

    if mark_good:
        return HTMLResponse('<div class="alert alert-success">Marked as good.</div>')

    # apply_immediately -> correction_memory
    if apply_immediately and corrected_response:
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
        db.commit()
        logger.info("correction_memory updated: trigger=%s", trigger_key)

    # send_to_training -> training_candidate
    if send_to_training:
        candidate = _build_candidate(turn, corrected_response, corrected_next_action, correction_type)
        if candidate:
            db.add(candidate)
            db.commit()

    parts = [f"Correction saved (id={correction.id})"]
    if apply_immediately:
        parts.append("Correction memory updated")
    if send_to_training:
        parts.append("Training candidate created")

    msg = " &nbsp;|&nbsp; ".join(parts)
    return HTMLResponse(f'<div class="alert alert-success">{msg}</div>')


def _build_candidate(turn: Turn, corrected_response: str, corrected_next_action: str, correction_type: str = "response_correction"):
    """Build a TrainingCandidate from a turn and corrected response."""
    import json as _json

    SYSTEM_INSTRUCTION = (
        "You are an CallShield Gold Paket sales policy agent. "
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
