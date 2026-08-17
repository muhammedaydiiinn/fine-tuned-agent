import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.csrf import require_csrf
from app.db import get_db
from app.livekit_tokens import build_voice_token, publish_control
from app.models import Deployment, ModelVersion, Session as SessionModel, Turn, VoiceEvent
from app.ui_feedback import toast_fragment, toast_redirect
from app.config import settings
from app.voice_actions import prepare_voice_action
from app.voice_observability import (
    build_recent_voice_turns,
    build_voice_acceptance,
    build_voice_health,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _backend_headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def _set_no_store(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.post("/sessions/start")
def start_session(
    external_session_id: str = Form(""),
    customer_name: str = Form(""),
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    external_id = external_session_id.strip() or f"voice-test-{uuid.uuid4().hex[:10]}"
    try:
        response = httpx.post(
            f"{settings.agent_backend_url}/sessions",
            json={
                "external_session_id": external_id,
                "customer_name": customer_name.strip(),
            },
            headers=_backend_headers(),
            timeout=10.0,
        )
        response.raise_for_status()
        session_id = int(response.json()["id"])
    except Exception as exc:
        logger.exception("Could not start session")
        return toast_redirect(
            "/",
            f"Test görüşmesi oluşturulamadı: {exc}",
            kind="error",
            title="Görüşme başlatılamadı",
        )
    return toast_redirect(
        f"/sessions/{session_id}",
        "Test görüşmesi oluşturuldu. Hazır olduğunuzda mikrofonu başlatabilirsiniz.",
        title="Görüşme hazır",
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
        return toast_redirect("/", "Görüşme bulunamadı.", kind="error")
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    latest_turn = turns[-1] if turns else None
    state_pretty = json.dumps(session.state_json, indent=2, ensure_ascii=False)

    # Active model badge: find the currently deployed production model version name
    active_model_name = None
    active_deployment = (
        db.query(Deployment)
        .filter(Deployment.environment == "production", Deployment.status == "active")
        .order_by(Deployment.id.desc())
        .first()
    )
    if active_deployment:
        mv = db.query(ModelVersion).filter(ModelVersion.id == active_deployment.model_version_id).first()
        if mv:
            active_model_name = mv.version_name

    response = templates.TemplateResponse(
        "session_detail.html",
        {
            "request": request,
            "session": session,
            "turns": turns,
            "latest_turn": latest_turn,
            "state_pretty": state_pretty,
            "active_model_name": active_model_name,
        },
    )
    return _set_no_store(response)


@router.post("/sessions/{session_id}/voice-token")
def session_voice_token(
    session_id: int,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        return JSONResponse({"detail": "Görüşme bulunamadı"}, status_code=404)
    if session.status != "active":
        return JSONResponse(
            {"detail": "Ses yalnızca aktif bir görüşmede başlatılabilir"},
            status_code=409,
        )
    if not session.external_session_id:
        return JSONResponse(
            {"detail": "Görüşmenin dış görüşme kimliği yok"},
            status_code=409,
        )

    participant_identity = f"supervisor-{uuid.uuid4().hex[:10]}"
    token = build_voice_token(
        participant_identity=participant_identity,
        room_name=session.external_session_id,
        dispatch_agent=True,
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
        return JSONResponse({"detail": "Görüşme bulunamadı"}, status_code=404)
    if session.status != "active":
        return JSONResponse(
            {"detail": "Ses yalnızca aktif bir görüşmede başlatılabilir"},
            status_code=409,
        )
    if not session.external_session_id:
        return JSONResponse(
            {"detail": "Görüşmenin dış görüşme kimliği yok"},
            status_code=409,
        )

    participant_identity = f"supervisor-{uuid.uuid4().hex[:10]}"
    token = build_voice_token(
        participant_identity=participant_identity,
        room_name=session.external_session_id,
        dispatch_agent=False,
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
        return toast_fragment("Görüşme bulunamadı.", kind="error", status_code=404)
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
        return toast_fragment("Görüşme bulunamadı.", kind="error", status_code=404)
    turn_count = (
        db.query(func.count(Turn.id))
        .filter(Turn.session_id == session_id)
        .scalar()
        or 0
    )
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc(), Turn.id.asc())
        .all()
    )
    events = (
        db.query(VoiceEvent)
        .filter(VoiceEvent.session_id == session_id)
        .order_by(VoiceEvent.sequence.asc(), VoiceEvent.id.asc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        "_session_summary.html",
        {
            "request": request,
            "session": session,
            "turn_count": turn_count,
            "voice_health": build_voice_health(turns, events),
        },
    )


@router.get("/sessions/{session_id}/voice-diagnostics", response_class=HTMLResponse)
def session_voice_diagnostics(
    session_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return toast_fragment("Görüşme bulunamadı.", kind="error", status_code=404)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc(), Turn.id.asc())
        .all()
    )
    events = (
        db.query(VoiceEvent)
        .filter(VoiceEvent.session_id == session_id)
        .order_by(VoiceEvent.sequence.asc(), VoiceEvent.id.asc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        "_voice_diagnostics.html",
        {
            "request": request,
            "voice_health": build_voice_health(turns, events),
            "recent_voice_turns": build_recent_voice_turns(turns, limit=5),
            "voice_acceptance": build_voice_acceptance(turns, events),
        },
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
        "voice_session_ready": "Ses çalışma zamanı hazır",
        "transcript_final": "Transkript kesinleşti",
        "partial_transcript": "Kısmi transkript",
        "agent_response": "Ajan yanıtı hazır",
        "supervisor_action_requested": "Süpervizör işlemi istendi",
        "supervisor_stop_applied": "Süpervizör durdurması uygulandı",
        "supervisor_replacement_started": "Süpervizör değişimi başladı",
        "supervisor_replacement_completed": "Süpervizör değişimi tamamlandı",
        "supervisor_action_ignored": "Süpervizör işlemi yok sayıldı",
        "stt_unavailable": "STT kullanılamıyor",
        "tts_fallback_activated": "TTS yedeği etkinleştirildi",
        "interruption_detected": "Müşteri sözü kesti",
        "playback_cancelled": "Oynatma iptal edildi",
        "backchannel_detected": "Geri bildirim sesi algılandı",
        "duplicate_transcript_ignored": "Yinelenen yok sayıldı",
        "stale_response_discarded": "Eski yanıt atıldı",
        "voice_turn_complete": "Tur tamamlandı",
        "voice_error": "Ses hatası",
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
        return toast_redirect("/", "Görüşme bulunamadı.", kind="error")
    session.status = "closed"
    db.commit()
    return toast_redirect(
        f"/review/{session_id}",
        "Görüşme kapatıldı. İnceleme ve eğitim kontrolleri artık kullanılabilir.",
        title="Görüşme incelemeye taşındı",
    )


@router.post("/sessions/{session_id}/voice-actions")
def session_voice_action(
    session_id: int,
    action: str = Form(...),
    replacement_text: str = Form(""),
    corrected_next_action: str = Form(""),
    notes: str = Form(""),
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        return JSONResponse({"detail": "Görüşme bulunamadı"}, status_code=404)
    if session.status != "active":
        return JSONResponse({"detail": "Görüşme aktif değil"}, status_code=409)
    if not session.external_session_id:
        return JSONResponse({"detail": "Görüşmenin dış görüşme kimliği yok"}, status_code=409)

    latest_turn = (
        db.query(Turn)
        .filter(Turn.session_id == session.id)
        .order_by(Turn.turn_index.desc(), Turn.id.desc())
        .first()
    )
    action_name = action.strip()
    action_id = uuid.uuid4().hex
    actor = settings.admin_user

    try:
        prepared = prepare_voice_action(
            action=action_name,
            action_id=action_id,
            actor=actor,
            external_session_id=session.external_session_id,
            session_id=session.id,
            latest_turn=latest_turn,
            replacement_text=replacement_text,
            corrected_next_action=corrected_next_action,
            notes=notes,
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    if prepared.correction_payload is not None:
        try:
            response = httpx.post(
                f"{settings.agent_backend_url}/corrections",
                json=prepared.correction_payload,
                headers=_backend_headers(),
                timeout=15.0,
            )
            response.raise_for_status()
            correction = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Live correction rejected — session=%s status=%d body=%.300s",
                session_id,
                exc.response.status_code,
                exc.response.text,
            )
            return JSONResponse(
                {"detail": "Canlı düzeltme kaydedilemedi"},
                status_code=502,
            )
        except Exception:
            logger.exception("Live correction failed — session=%s", session_id)
            return JSONResponse(
                {"detail": "Canlı düzeltme kaydedilemedi"},
                status_code=502,
            )
        prepared.audit_payload["payload"]["correction_id"] = correction["id"]
    else:
        correction = None

    try:
        httpx.post(
            f"{settings.agent_backend_url}/voice/events",
            json=prepared.audit_payload,
            headers=_backend_headers(),
            timeout=10.0,
        )
    except Exception:
        logger.exception("Could not persist supervisor action audit event — session=%s", session_id)

    # Deliver the control command server-side so Stop/replace works even when the
    # supervisor is only monitoring and has NOT joined the audio room.
    delivered = publish_control(session.external_session_id, prepared.command)

    return {
        "ok": True,
        "command": prepared.command,
        "delivered": delivered,
        "correction_id": correction["id"] if correction else None,
    }
