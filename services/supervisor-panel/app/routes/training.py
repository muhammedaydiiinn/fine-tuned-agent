"""Training panel — candidates + training jobs."""
import io
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import TrainingCandidate, TrainingJob

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


# ── Training candidates ───────────────────────────────────────────────────────

@router.get("/training-candidates", response_class=HTMLResponse)
def candidates_list(
    request: Request,
    approved: str = "",
    db: DBSession = Depends(get_db),
):
    q = db.query(TrainingCandidate)
    if approved == "true":
        q = q.filter(TrainingCandidate.approved == True)   # noqa: E712
    elif approved == "false":
        q = q.filter(TrainingCandidate.approved == False)  # noqa: E712
    candidates = q.order_by(TrainingCandidate.created_at.desc()).limit(200).all()
    total_approved = db.query(TrainingCandidate).filter(TrainingCandidate.approved == True).count()  # noqa: E712
    return templates.TemplateResponse(
        "training_candidates.html",
        {
            "request": request,
            "candidates": candidates,
            "filter_approved": approved,
            "total_approved": total_approved,
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


# ── Training jobs ─────────────────────────────────────────────────────────────

@router.get("/training-jobs", response_class=HTMLResponse)
def training_jobs_list(request: Request, db: DBSession = Depends(get_db)):
    jobs = (
        db.query(TrainingJob)
        .order_by(TrainingJob.created_at.desc())
        .limit(100)
        .all()
    )
    approved_count = db.query(TrainingCandidate).filter(TrainingCandidate.approved == True).count()  # noqa: E712

    status_counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for j in jobs:
        if j.status in status_counts:
            status_counts[j.status] += 1

    return templates.TemplateResponse(
        "training_jobs.html",
        {
            "request": request,
            "jobs": jobs,
            "approved_count": approved_count,
            "status_counts": status_counts,
        },
    )


@router.post("/training-jobs/start", response_class=HTMLResponse)
def start_training(request: Request, db: DBSession = Depends(get_db)):
    """Trigger a new training pipeline job via agent-backend."""
    approved_count = db.query(TrainingCandidate).filter(TrainingCandidate.approved == True).count()  # noqa: E712
    if approved_count == 0:
        return HTMLResponse(
            '<div class="alert alert-error">No approved training candidates.</div>'
        )

    try:
        resp = httpx.post(
            f"{settings.agent_backend_url}/training-jobs",
            json={},
            timeout=10.0,
        )
        resp.raise_for_status()
        job = resp.json()
        job_id = job.get("id")
        logger.info("Training job created via backend: id=%s", job_id)
        return HTMLResponse(
            f'<div class="alert alert-success">'
            f'Training job <strong>#{job_id}</strong> queued. '
            f'<a href="/training-jobs">View jobs →</a>'
            f'</div>'
        )
    except Exception as exc:
        logger.error("Failed to start training job: %s", exc)
        return HTMLResponse(
            f'<div class="alert alert-error">Failed to start job: {exc}</div>'
        )


@router.get("/training-jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_fragment(job_id: int, db: DBSession = Depends(get_db)):
    """HTMX polling endpoint — returns status badge + progress bar fragment."""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        return HTMLResponse('<span class="badge badge-error">Not found</span>')

    pct = 0
    if job.progress_total and job.progress_total > 0:
        pct = min(100, int(job.progress_current / job.progress_total * 100))

    status_class = {
        "pending": "badge-pending",
        "running": "badge-running",
        "completed": "badge-approved",
        "failed": "badge-error",
    }.get(job.status, "badge-pending")

    poll_attr = ""
    if job.status in ("pending", "running"):
        poll_attr = f'hx-get="/training-jobs/{job_id}/status" hx-trigger="every 3s" hx-swap="outerHTML"'

    return HTMLResponse(
        f'<span class="job-status-cell" {poll_attr}>'
        f'<span class="badge {status_class}">{job.status}</span>'
        f'<div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>'
        f'</span>'
    )
