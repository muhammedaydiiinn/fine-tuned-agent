"""Training panel — candidates + training jobs."""
import json as _json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import TrainingCandidate, TrainingJob
from app.ui_feedback import toast_fragment, toast_redirect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _backend_headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


# ── Training candidates ───────────────────────────────────────────────────────

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
        try:
            user_content = _json.loads(msgs[1]["content"]) if len(msgs) > 1 else {}
            customer_msg = user_content.get("customer_message", "") if isinstance(user_content, dict) else ""
        except Exception:
            customer_msg = msgs[1].get("content", "") if len(msgs) > 1 else ""
        try:
            asst_content = _json.loads(msgs[2]["content"]) if len(msgs) > 2 else {}
            corrected_msg = asst_content.get("agent_response", "") if isinstance(asst_content, dict) else ""
        except Exception:
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
            "created_at": c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if c.created_at else None,
        })
    return {"data": rows}


@router.post("/training-candidates/{candidate_id}/approve", response_class=HTMLResponse)
def approve_candidate(candidate_id: int, db: DBSession = Depends(get_db), _csrf: None = Depends(require_csrf)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        return toast_fragment("Aday bulunamadı.", kind="error", status_code=404)
    c.approved = True
    db.commit()
    logger.info("training_candidate approved: id=%d", candidate_id)
    return HTMLResponse('<span class="badge badge-approved">Onaylandı</span>')


@router.post("/training-candidates/{candidate_id}/reject", response_class=HTMLResponse)
def reject_candidate(candidate_id: int, db: DBSession = Depends(get_db), _csrf: None = Depends(require_csrf)):
    c = db.query(TrainingCandidate).filter(TrainingCandidate.id == candidate_id).first()
    if not c:
        return toast_fragment("Aday bulunamadı.", kind="error", status_code=404)
    c.approved = False
    db.commit()
    logger.info("training_candidate rejected: id=%d", candidate_id)
    return HTMLResponse('<span class="badge badge-rejected">Reddedildi</span>')



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
            "started_at": j.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if j.started_at else None,
            "input_session_id": (j.input_json or {}).get("session_id", "—"),
            "duration": _fmt_duration(j),
            "version_name": (j.output_json or {}).get("version_name", ""),
            "error_message": (j.error_message or "")[:80],
        })

    return {"data": rows}


@router.get("/training-jobs/{job_id}", response_class=HTMLResponse)
def training_job_detail(job_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Job detail page — metadata + live log viewer."""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        return toast_redirect("/pipeline", "Eğitim işi bulunamadı.", kind="error")

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
            headers=_backend_headers(),
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        logs = (data.get("logs") or "").strip()
    except Exception as exc:
        logger.error("Log proxy error job_id=%d: %s", job_id, exc)
        logs = f"[proxy error: {exc}]"

    if not logs:
        return HTMLResponse('<span style="opacity:.4;padding:12px;display:block;">Henüz günlük çıktısı yok.</span>')

    escaped = logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return HTMLResponse(f'<pre class="log-viewer">{escaped}</pre>')
