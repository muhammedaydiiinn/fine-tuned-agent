import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import Session as SessionModel, Turn
from app.schemas import CreateSessionRequest, SessionResponse, TurnResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/sessions", response_model=SessionResponse)
def create_session(req: CreateSessionRequest, db: DBSession = Depends(get_db)):
    external_id = req.external_session_id or f"session-{uuid.uuid4().hex[:12]}"

    # Aynı external_id varsa döndür
    existing = db.query(SessionModel).filter(
        SessionModel.external_session_id == external_id
    ).first()
    if existing:
        return existing

    session = SessionModel(
        external_session_id=external_id,
        status="active",
        state_json={},
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    logger.info("Yeni session oluşturuldu: %s (id=%d)", external_id, session.id)
    return session


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, db: DBSession = Depends(get_db)):
    session = _get_or_404(db, session_id)
    return session


@router.get("/sessions/{session_id}/turns", response_model=list[TurnResponse])
def list_turns(session_id: str, db: DBSession = Depends(get_db)):
    session = _get_or_404(db, session_id)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session.id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    return turns


def _get_or_404(db: DBSession, session_id: str) -> SessionModel:
    session = db.query(SessionModel).filter(
        SessionModel.external_session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session bulunamadı: {session_id}")
    return session
