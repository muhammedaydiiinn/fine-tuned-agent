"""Training candidate panel — list, approve/reject, JSONL export (download)."""
import io
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import TrainingCandidate

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/training-candidates", response_class=HTMLResponse)
def candidates_list(
    request: Request,
    approved: str = "",   # "true" | "false" | "" (all)
    db: DBSession = Depends(get_db),
):
    q = db.query(TrainingCandidate)
    if approved == "true":
        q = q.filter(TrainingCandidate.approved == True)   # noqa: E712
    elif approved == "false":
        q = q.filter(TrainingCandidate.approved == False)  # noqa: E712
    candidates = q.order_by(TrainingCandidate.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "training_candidates.html",
        {
            "request": request,
            "candidates": candidates,
            "filter_approved": approved,
        },
    )


@router.post("/training-candidates/{candidate_id}/approve", response_class=HTMLResponse)
def approve_candidate(candidate_id: int, db: DBSession = Depends(get_db)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        return HTMLResponse('<span class="badge badge-error">Not found</span>')
    c.approved = True
    db.commit()
    logger.info("training_candidate approved: id=%d", candidate_id)
    return HTMLResponse('<span class="badge badge-approved">Approved</span>')


@router.post("/training-candidates/{candidate_id}/reject", response_class=HTMLResponse)
def reject_candidate(candidate_id: int, db: DBSession = Depends(get_db)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        return HTMLResponse('<span class="badge badge-error">Not found</span>')
    c.approved = False
    db.commit()
    logger.info("training_candidate rejected: id=%d", candidate_id)
    return HTMLResponse('<span class="badge badge-rejected">Rejected</span>')


@router.post("/training-candidates/export-jsonl")
def export_jsonl(db: DBSession = Depends(get_db)):
    """Stream approved, not-yet-exported candidates as a JSONL file download.

    The panel has no ./data:/data mount, so the file is served as a download
    rather than written to disk. Records are marked exported=True after streaming.
    """
    candidates = (
        db.query(TrainingCandidate)
        .filter(TrainingCandidate.approved == True, TrainingCandidate.exported == False)  # noqa: E712
        .order_by(TrainingCandidate.created_at.asc())
        .all()
    )

    if not candidates:
        return HTMLResponse(
            '<div class="alert alert-error">No approved candidates pending export.</div>'
        )

    lines: list[str] = []
    exported_ids: list[int] = []
    for c in candidates:
        lines.append(json.dumps({"messages": c.messages_json}, ensure_ascii=False))
        c.exported = True
        exported_ids.append(c.id)
    db.commit()

    content = "\n".join(lines) + "\n"
    filename = f"dataset_{settings.model_active_version}.jsonl"
    logger.info("Panel JSONL export: %d rows, file=%s", len(lines), filename)

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="application/jsonlines",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
