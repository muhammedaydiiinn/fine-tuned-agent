import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import Session as SessionModel, Turn, Correction

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _pretty(obj) -> str:
    if obj is None:
        return "null"
    return json.dumps(obj, indent=2, ensure_ascii=False)


@router.get("/sessions/{session_id}/turns/{turn_id}", response_class=HTMLResponse)
def turn_detail(session_id: int, turn_id: int, request: Request, db: DBSession = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    turn = db.query(Turn).filter(Turn.id == turn_id, Turn.session_id == session_id).first()
    if not turn:
        return HTMLResponse("Turn not found", status_code=404)

    corrections = (
        db.query(Correction)
        .filter(Correction.turn_id == turn_id)
        .order_by(Correction.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "turn_detail.html",
        {
            "request": request,
            "session": session,
            "turn": turn,
            "corrections": corrections,
            "raw_json": _pretty(turn.raw_model_json),
            "repaired_json": _pretty(turn.repaired_model_json),
            "state_before": _pretty(turn.state_before_json),
            "state_after": _pretty(turn.state_after_json),
            "latency": _pretty(turn.latency_json),
        },
    )
