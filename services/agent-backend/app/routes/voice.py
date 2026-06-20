"""Voice-runtime integration endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import LatencyMetric, Session as SessionModel, Turn
from app.schemas import VoiceTurnMetricsRequest, VoiceTurnMetricsResponse

router = APIRouter()


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

    voice_latency = {
        "stt_ms": req.stt_ms,
        "backend_ms": req.backend_ms,
        "llm_ms": req.llm_ms,
        "tts_first_audio_ms": req.tts_first_audio_ms,
        "total_voice_turn_ms": req.total_voice_turn_ms,
    }
    turn.latency_json = {**(turn.latency_json or {}), **voice_latency}
    metric_names = {"stt_ms", "tts_first_audio_ms", "total_voice_turn_ms"}
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
    db.commit()

    return VoiceTurnMetricsResponse(
        turn_id=turn.id,
        session_id=req.session_id,
        latency=voice_latency,
    )
