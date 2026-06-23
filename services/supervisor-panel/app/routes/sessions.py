import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from livekit import api
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.csrf import require_csrf
from app.db import get_db
from app.models import Session as SessionModel, Turn, VoiceEvent
from app.ui_feedback import toast_redirect
from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _backend_headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


@router.post("/sessions/start")
def start_session(
    external_session_id: str = Form(""),
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    external_id = external_session_id.strip() or f"voice-test-{uuid.uuid4().hex[:10]}"
    try:
        response = httpx.post(
            f"{settings.agent_backend_url}/sessions",
            json={"external_session_id": external_id},
            headers=_backend_headers(),
            timeout=10.0,
        )
        response.raise_for_status()
        session_id = int(response.json()["id"])
    except Exception as exc:
        logger.exception("Could not start session")
        return toast_redirect(
            "/",
            f"Could not create the test session: {exc}",
            kind="error",
            title="Session start failed",
        )
    return toast_redirect(
        f"/sessions/{session_id}",
        "Test session created. You can start the microphone when ready.",
        title="Session ready",
    )


@router.get("/", response_class=HTMLResponse)
def sessions_list(request: Request, db: DBSession = Depends(get_db)):
    product_session = or_(
        SessionModel.external_session_id.is_(None),
        ~SessionModel.external_session_id.like("eval-%"),
    )
    total_count = (
        db.query(func.count(SessionModel.id))
        .filter(product_session)
        .scalar()
        or 0
    )
    active_count = (
        db.query(func.count(SessionModel.id))
        .filter(product_session, SessionModel.status == "active")
        .scalar()
        or 0
    )
    total_turns = (
        db.query(func.count(Turn.id))
        .join(SessionModel, SessionModel.id == Turn.session_id)
        .filter(product_session)
        .scalar()
        or 0
    )
    return templates.TemplateResponse(
        "sessions.html",
        {
            "request": request,
            "total_count": total_count,
            "active_count": active_count,
            "total_turns": total_turns,
            "closed_count": total_count - active_count,
        },
    )


@router.get("/sessions/data")
def sessions_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source for sessions table."""
    sessions = (
        db.query(SessionModel)
        .filter(
            or_(
                SessionModel.external_session_id.is_(None),
                ~SessionModel.external_session_id.like("eval-%"),
            )
        )
        .order_by(SessionModel.created_at.desc())
        .limit(500)
        .all()
    )
    turn_counts = dict(
        db.query(Turn.session_id, func.count(Turn.id))
        .group_by(Turn.session_id)
        .all()
    )
    rows = []
    for s in sessions:
        state = s.state_json or {}
        rows.append({
            "id": s.id,
            "external_session_id": s.external_session_id or f"#{s.id}",
            "status": s.status,
            "stage": s.current_stage or state.get("stage", "") or "",
            "turns": turn_counts.get(s.id, 0),
            "hard_decline": state.get("hard_decline_count", 0),
            "created_at": s.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if s.created_at else None,
        })
    return {"data": rows}


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session not found", status_code=404)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    latest_turn = turns[-1] if turns else None
    state_pretty = json.dumps(session.state_json, indent=2, ensure_ascii=False)
    return templates.TemplateResponse(
        "session_detail.html",
        {
            "request": request,
            "session": session,
            "turns": turns,
            "latest_turn": latest_turn,
            "state_pretty": state_pretty,
        },
    )


@router.post("/sessions/{session_id}/voice-token")
def session_voice_token(
    session_id: int,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        return JSONResponse({"detail": "Session not found"}, status_code=404)
    if session.status != "active":
        return JSONResponse(
            {"detail": "Voice can only be started for an active session"},
            status_code=409,
        )
    if not session.external_session_id:
        return JSONResponse(
            {"detail": "Session has no external session ID"},
            status_code=409,
        )

    participant_identity = f"supervisor-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps(
        {"session_id": session.external_session_id},
        separators=(",", ":"),
    )
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_name("Supervisor voice test")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=session.external_session_id,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=settings.livekit_agent_name,
                        metadata=metadata,
                    )
                ]
            )
        )
        .to_jwt()
    )
    return {
        "token": token,
        "server_url": settings.livekit_public_url,
        "session_id": session.external_session_id,
        "participant_identity": participant_identity,
    }


@router.post("/sessions/{session_id}/voice-token-resume")
def session_voice_token_resume(
    session_id: int,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    """Token without agent dispatch — used when rejoining an existing room."""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        return JSONResponse({"detail": "Session not found"}, status_code=404)
    if session.status != "active":
        return JSONResponse(
            {"detail": "Voice can only be started for an active session"},
            status_code=409,
        )
    if not session.external_session_id:
        return JSONResponse(
            {"detail": "Session has no external session ID"},
            status_code=409,
        )

    participant_identity = f"supervisor-{uuid.uuid4().hex[:10]}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_name("Supervisor voice test")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=session.external_session_id,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
    return {
        "token": token,
        "server_url": settings.livekit_public_url,
        "session_id": session.external_session_id,
        "participant_identity": participant_identity,
    }


@router.get("/sessions/{session_id}/conversation", response_class=HTMLResponse)
def session_conversation(
    session_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session not found", status_code=404)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    return templates.TemplateResponse(
        "_session_conversation.html",
        {"request": request, "session": session, "turns": turns},
    )


@router.get("/sessions/{session_id}/live-summary", response_class=HTMLResponse)
def session_live_summary(
    session_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session not found", status_code=404)
    turn_count = (
        db.query(func.count(Turn.id))
        .filter(Turn.session_id == session_id)
        .scalar()
        or 0
    )
    return templates.TemplateResponse(
        "_session_summary.html",
        {"request": request, "session": session, "turn_count": turn_count},
    )


@router.get("/sessions/{session_id}/voice-events", response_class=HTMLResponse)
def session_voice_events(
    session_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    events = (
        db.query(VoiceEvent)
        .filter(VoiceEvent.session_id == session_id)
        .order_by(VoiceEvent.sequence.desc(), VoiceEvent.id.desc())
        .limit(12)
        .all()
    )
    labels = {
        "voice_session_ready": "Voice runtime ready",
        "transcript_final": "Transcript final",
        "partial_transcript": "Partial transcript",
        "agent_response": "Agent response ready",
        "interruption_detected": "Customer interrupted",
        "playback_cancelled": "Playback cancelled",
        "backchannel_detected": "Backchannel detected",
        "duplicate_transcript_ignored": "Duplicate ignored",
        "stale_response_discarded": "Stale response discarded",
        "voice_turn_complete": "Turn completed",
        "voice_error": "Voice error",
    }
    barge_in_count = (
        db.query(func.count(VoiceEvent.id))
        .filter(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "interruption_detected",
        )
        .scalar()
    ) or 0
    return templates.TemplateResponse(
        "_voice_events.html",
        {
            "request": request,
            "events": events,
            "labels": labels,
            "barge_in_count": barge_in_count,
        },
    )


@router.post("/sessions/{session_id}/close")
def close_session(
    session_id: int,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session not found", status_code=404)
    session.status = "closed"
    db.commit()
    return toast_redirect(
        f"/review/{session_id}",
        "Session closed. Review and training controls are now available.",
        title="Session moved to review",
    )
