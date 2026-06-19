import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import Session as SessionModel, Turn

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def sessions_list(request: Request, db: DBSession = Depends(get_db)):
    sessions = (
        db.query(SessionModel)
        .order_by(SessionModel.created_at.desc())
        .limit(100)
        .all()
    )
    # Her session için turn sayısı
    turn_counts = dict(
        db.query(Turn.session_id, func.count(Turn.id))
        .group_by(Turn.session_id)
        .all()
    )
    return templates.TemplateResponse(
        "sessions.html",
        {"request": request, "sessions": sessions, "turn_counts": turn_counts},
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session bulunamadı", status_code=404)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    state_pretty = json.dumps(session.state_json, indent=2, ensure_ascii=False)
    return templates.TemplateResponse(
        "session_detail.html",
        {"request": request, "session": session, "turns": turns, "state_pretty": state_pretty},
    )
