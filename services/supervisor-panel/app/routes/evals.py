"""Evaluation jobs panel."""
import html
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import EvalRun, ModelVersion

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _backend_headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def _duration(run: EvalRun) -> str:
    if not run.started_at:
        return "—"
    if not run.finished_at:
        return "running…"
    seconds = max(0, int((run.finished_at - run.started_at).total_seconds()))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


@router.get("/eval-jobs/data")
def eval_jobs_data(db: DBSession = Depends(get_db)):
    runs = db.query(EvalRun).order_by(EvalRun.created_at.desc()).limit(500).all()
    model_ids = {run.model_version_id for run in runs}
    versions = {
        version.id: version.version_name
        for version in db.query(ModelVersion).filter(ModelVersion.id.in_(model_ids)).all()
    } if model_ids else {}

    rows = []
    for run in runs:
        progress_pct = 0
        if run.progress_total:
            progress_pct = min(100, int(run.progress_current / run.progress_total * 100))
        score = (run.metrics_json or {}).get("quality_score")
        rows.append({
            "id": run.id,
            "model_version": versions.get(run.model_version_id, f"Model #{run.model_version_id}"),
            "status": run.status,
            "progress_pct": progress_pct,
            "quality_score": score,
            "started_at": run.started_at.strftime("%m-%d %H:%M") if run.started_at else "—",
            "duration": _duration(run),
            "error_message": (run.error_message or "")[:100],
        })
    return {"data": rows}


@router.get("/eval-jobs", response_class=HTMLResponse)
def eval_jobs_list(request: Request, db: DBSession = Depends(get_db)):
    runs = db.query(EvalRun).order_by(EvalRun.created_at.desc()).limit(500).all()
    model_versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.merged_path.is_not(None))
        .order_by(ModelVersion.created_at.desc())
        .all()
    )
    status_counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for run in runs:
        if run.status in status_counts:
            status_counts[run.status] += 1
    return templates.TemplateResponse(
        "eval_jobs.html",
        {
            "request": request,
            "runs": runs,
            "model_versions": model_versions,
            "status_counts": status_counts,
        },
    )


@router.post("/eval-jobs/start", response_class=HTMLResponse)
def start_eval(model_version_id: int = Form(...)):
    try:
        response = httpx.post(
            f"{settings.agent_backend_url}/eval-runs",
            json={"model_version_id": model_version_id},
            headers=_backend_headers(),
            timeout=10.0,
        )
        response.raise_for_status()
        eval_run = response.json()
        run_id = int(eval_run["id"])
        return HTMLResponse(
            '<div class="alert alert-success">'
            f'Evaluation <strong>#{run_id}</strong> queued. '
            f'<a href="/eval-jobs/{run_id}">Open evaluation</a>'
            "</div>"
        )
    except Exception as exc:
        logger.exception("Failed to start eval for model_version_id=%d", model_version_id)
        return HTMLResponse(
            f'<div class="alert alert-error">Failed to start evaluation: '
            f'{html.escape(str(exc))}</div>',
            status_code=502,
        )


@router.get("/eval-jobs/{eval_run_id}", response_class=HTMLResponse)
def eval_job_detail(
    eval_run_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if not run:
        return HTMLResponse(
            '<div class="alert alert-error">Evaluation not found.</div>',
            status_code=404,
        )
    model_version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == run.model_version_id)
        .first()
    )
    results: dict[str, Any] = {}
    if run.results_path:
        try:
            response = httpx.get(
                f"{settings.agent_backend_url}/eval-runs/{eval_run_id}/results",
                headers=_backend_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            results = response.json()
        except Exception as exc:
            logger.warning("Could not load eval results id=%d: %s", eval_run_id, exc)

    progress_pct = (
        min(100, int(run.progress_current / run.progress_total * 100))
        if run.progress_total
        else 0
    )
    return templates.TemplateResponse(
        "eval_job_detail.html",
        {
            "request": request,
            "run": run,
            "model_version": model_version,
            "metrics": run.metrics_json or {},
            "results": results.get("results") or [],
            "progress_pct": progress_pct,
            "duration": _duration(run),
        },
    )


@router.get("/eval-jobs/{eval_run_id}/logs", response_class=HTMLResponse)
def eval_job_logs(eval_run_id: int, tail: int = 200):
    try:
        response = httpx.get(
            f"{settings.agent_backend_url}/eval-runs/{eval_run_id}/logs",
            params={"tail": tail},
            headers=_backend_headers(),
            timeout=5.0,
        )
        response.raise_for_status()
        logs = (response.json().get("logs") or "").strip()
    except Exception as exc:
        logger.error("Eval log proxy failed id=%d: %s", eval_run_id, exc)
        logs = f"[proxy error: {exc}]"

    if not logs:
        return HTMLResponse(
            '<span style="opacity:.4;padding:12px;display:block;">No log output yet.</span>'
        )
    return HTMLResponse(f'<pre class="log-viewer">{html.escape(logs)}</pre>')
