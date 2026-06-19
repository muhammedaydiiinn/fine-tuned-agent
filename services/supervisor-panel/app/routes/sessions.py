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
    total_count  = db.query(func.count(SessionModel.id)).scalar() or 0
    active_count = db.query(func.count(SessionModel.id)).filter(SessionModel.status == "active").scalar() or 0
    total_turns  = db.query(func.count(Turn.id)).scalar() or 0
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
            "created_at": s.created_at.strftime("%m-%d %H:%M"),
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
    state_pretty = json.dumps(session.state_json, indent=2, ensure_ascii=False)
    return templates.TemplateResponse(
        "session_detail.html",
        {"request": request, "session": session, "turns": turns, "state_pretty": state_pretty},
    )
