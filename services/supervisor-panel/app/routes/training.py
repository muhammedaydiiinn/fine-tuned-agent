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
def candidates_list(request: Request, db: DBSession = Depends(get_db)):
    total          = db.query(TrainingCandidate).count()
    total_approved = db.query(TrainingCandidate).filter(TrainingCandidate.approved == True).count()   # noqa: E712
    total_exported = db.query(TrainingCandidate).filter(TrainingCandidate.exported == True).count()   # noqa: E712
    return templates.TemplateResponse(
        "training_candidates.html",
        {
            "request": request,
            "total_count": total,
            "total_approved": total_approved,
            "total_exported": total_exported,
            "total_pending": total - total_approved,
        },
    )


@router.get("/training-candidates/data")
def candidates_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source for training candidates table."""
    candidates = (
        db.query(TrainingCandidate)
        .order_by(TrainingCandidate.created_at.desc())
        .limit(500)
        .all()
    )
    rows = []
    for c in candidates:
        msgs = c.messages_json or []
        customer_msg  = msgs[1].get("content", "") if len(msgs) > 1 else ""
        corrected_msg = msgs[2].get("content", "") if len(msgs) > 2 else ""
        correction_type = (c.metadata_json or {}).get("correction_type", "")
        rows.append({
            "id": c.id,
            "source_type": c.source_type,
            "customer_msg": customer_msg[:100],
            "corrected_response": corrected_msg[:100],
            "correction_type": correction_type,
            "approved": c.approved,
            "exported": c.exported,
            "created_at": c.created_at.strftime("%m-%d %H:%M"),
        })
    return {"data": rows}


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

@router.get("/training-jobs/data")
def training_jobs_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source — returns {data: [...]} format."""
    jobs = (
        db.query(TrainingJob)
        .order_by(TrainingJob.created_at.desc())
        .limit(500)
        .all()
    )

    def _fmt_duration(job: TrainingJob) -> str:
        if not job.started_at:
            return "—"
        end = job.finished_at or job.started_at
        secs = int((end - job.started_at).total_seconds())
        if job.finished_at is None:
            return "running…"
        if secs >= 3600:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
        if secs >= 60:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs}s"

    rows = []
    for j in jobs:
        pct = 0
        if j.progress_total and j.progress_total > 0:
            pct = min(100, int(j.progress_current / j.progress_total * 100))
        rows.append({
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "progress_pct": pct,
            "dataset_version": (j.input_json or {}).get("dataset_version", "—"),
            "started_at": j.started_at.strftime("%m-%d %H:%M") if j.started_at else "—",
            "duration": _fmt_duration(j),
            "version_name": (j.output_json or {}).get("version_name", ""),
            "error_message": (j.error_message or "")[:80],
        })

    return {"data": rows}


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


@router.get("/training-jobs/{job_id}", response_class=HTMLResponse)
def training_job_detail(job_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Job detail page — metadata + live log viewer."""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        return HTMLResponse('<div class="alert alert-error">Job not found.</div>', status_code=404)

    pct = 0
    if job.progress_total and job.progress_total > 0:
        pct = min(100, int(job.progress_current / job.progress_total * 100))

    return templates.TemplateResponse(
        "training_job_detail.html",
        {"request": request, "job": job, "pct": pct},
    )


@router.get("/training-jobs/{job_id}/logs", response_class=HTMLResponse)
def job_logs_proxy(job_id: int, tail: int = 200):
    """Proxy log lines from agent-backend and return as HTML fragment for HTMX."""
    try:
        resp = httpx.get(
            f"{settings.agent_backend_url}/training-jobs/{job_id}/logs",
            params={"tail": tail},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        logs = (data.get("logs") or "").strip()
    except Exception as exc:
        logger.error("Log proxy error job_id=%d: %s", job_id, exc)
        logs = f"[proxy error: {exc}]"

    if not logs:
        return HTMLResponse('<span style="opacity:.4;padding:12px;display:block;">No log output yet.</span>')

    escaped = logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return HTMLResponse(f'<pre class="log-viewer">{escaped}</pre>')
