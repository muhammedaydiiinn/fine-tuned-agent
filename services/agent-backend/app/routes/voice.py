"""Voice-runtime integration endpoints."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import LatencyMetric, Session as SessionModel, Turn, VoiceEvent
from app.schemas import (
    VoiceEventRequest,
    VoiceEventResponse,
    VoiceTurnInterruptionRequest,
    VoiceTurnInterruptionResponse,
    VoiceTurnMetricsRequest,
    VoiceTurnMetricsResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/voice/events", response_model=VoiceEventResponse)
def save_voice_event(
    req: VoiceEventRequest,
    db: DBSession = Depends(get_db),
):
    """Persist a voice lifecycle event idempotently for M8 audit/reconnect."""
    existing = db.query(VoiceEvent).filter(VoiceEvent.event_id == req.event_id).first()
    if existing:
        return VoiceEventResponse(
            id=existing.id,
            event_id=existing.event_id,
            created=False,
        )

    session = (
        db.query(SessionModel)
        .filter(SessionModel.external_session_id == req.session_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Voice session not found")

    if req.turn_id is not None:
        turn_exists = (
            db.query(Turn.id)
            .filter(Turn.id == req.turn_id, Turn.session_id == session.id)
            .first()
        )
        if turn_exists is None:
            raise HTTPException(
                status_code=409,
                detail="Voice event turn does not belong to the session",
            )

    event = VoiceEvent(
        session_id=session.id,
        turn_id=req.turn_id,
        event_id=req.event_id,
        sequence=req.sequence,
        event_type=req.event_type,
        payload_json=req.payload,
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(VoiceEvent)
            .filter(VoiceEvent.event_id == req.event_id)
            .first()
        )
        if existing:
            return VoiceEventResponse(
                id=existing.id,
                event_id=existing.event_id,
                created=False,
            )
        raise HTTPException(status_code=500, detail="Failed to save voice event")
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to save voice event — session=%s event=%s",
            req.session_id,
            req.event_type,
        )
        raise HTTPException(status_code=500, detail="Failed to save voice event")

    return VoiceEventResponse(id=event.id, event_id=event.event_id, created=True)


@router.post(
    "/voice/turns/{turn_id}/interruption",
    response_model=VoiceTurnInterruptionResponse,
)
def record_voice_turn_interruption(
    turn_id: int,
    req: VoiceTurnInterruptionRequest,
    db: DBSession = Depends(get_db),
):
    """Mark a turn as interrupted and store the portion the customer heard.

    Called by the voice-runtime when playback is cancelled by a barge-in. The
    prompt builder uses these fields so the next turn resumes contextually
    rather than assuming the full reply was delivered.
    """
    turn = (
        db.query(Turn)
        .join(SessionModel, SessionModel.id == Turn.session_id)
        .filter(
            Turn.id == turn_id,
            SessionModel.external_session_id == req.session_id,
        )
        .first()
    )
    if turn is None:
        raise HTTPException(status_code=404, detail="Voice turn not found")

    turn.was_interrupted = True
    # Fall back to the full response when the estimate is empty but audio did
    # play (defensive — the runtime sends a best-effort prefix).
    turn.spoken_response = req.spoken_response or None
    turn.latency_json = {
        **(turn.latency_json or {}),
        "interrupted": True,
        "spoken_ms": req.spoken_ms,
    }

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to record interruption — turn_id=%d session=%s",
            turn_id,
            req.session_id,
        )
        raise HTTPException(status_code=500, detail="Failed to record interruption")

    logger.info(
        "Interruption recorded — turn_id=%d session=%s spoken_ms=%.0f",
        turn_id,
        req.session_id,
        req.spoken_ms,
    )
    return VoiceTurnInterruptionResponse(
        turn_id=turn.id,
        session_id=req.session_id,
        was_interrupted=True,
    )


@router.post(
    "/voice/turns/{turn_id}/metrics",
    response_model=VoiceTurnMetricsResponse,
)
def save_voice_turn_metrics(
    turn_id: int,
    req: VoiceTurnMetricsRequest,
    db: DBSession = Depends(get_db),
):
    turn = (
        db.query(Turn)
        .join(SessionModel, SessionModel.id == Turn.session_id)
        .filter(
            Turn.id == turn_id,
            SessionModel.external_session_id == req.session_id,
        )
        .first()
    )
    if turn is None:
        raise HTTPException(status_code=404, detail="Voice turn not found")

    if turn.customer_text.strip() != req.transcript_final.strip():
        raise HTTPException(
            status_code=409,
            detail="Final transcript does not match the persisted turn",
        )
    if (turn.agent_response or "").strip() != req.heard_response.strip():
        raise HTTPException(
            status_code=409,
            detail="Heard response does not match the persisted turn",
        )

    speech_end_to_first_audio_ms = (
        req.speech_end_to_first_audio_ms
        if req.speech_end_to_first_audio_ms is not None
        else req.stt_ms + req.backend_ms + req.tts_first_audio_ms
    )
    voice_latency = {
        "stt_ms": req.stt_ms,
        "backend_ms": req.backend_ms,
        "llm_ms": req.llm_ms,
        "tts_first_audio_ms": req.tts_first_audio_ms,
        "speech_end_to_first_audio_ms": speech_end_to_first_audio_ms,
        "total_voice_turn_ms": req.total_voice_turn_ms,
    }
    turn.latency_json = {**(turn.latency_json or {}), **voice_latency}
    metric_names = {
        "stt_ms",
        "tts_first_audio_ms",
        "speech_end_to_first_audio_ms",
        "total_voice_turn_ms",
    }
    (
        db.query(LatencyMetric)
        .filter(
            LatencyMetric.turn_id == turn.id,
            LatencyMetric.metric_name.in_(metric_names),
        )
        .delete(synchronize_session=False)
    )
    db.add_all(
        [
            LatencyMetric(
                session_id=turn.session_id,
                turn_id=turn.id,
                metric_name=metric_name,
                value_ms=value_ms,
            )
            for metric_name, value_ms in voice_latency.items()
            if metric_name in metric_names
        ]
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to save voice metrics — turn_id=%d session=%s", turn_id, req.session_id)
        raise HTTPException(status_code=500, detail="Failed to save voice metrics")

    logger.info(
        "Voice metrics saved — turn_id=%d session=%s stt=%.0f total=%.0f",
        turn_id,
        req.session_id,
        req.stt_ms,
        req.total_voice_turn_ms,
    )
    return VoiceTurnMetricsResponse(
        turn_id=turn.id,
        session_id=req.session_id,
        latency=voice_latency,
    )
