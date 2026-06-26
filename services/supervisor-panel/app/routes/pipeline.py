"""Unified pipeline page — training lifecycle in one view."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import (
    Deployment,
    EvalRun,
    ModelVersion,
    TrainingCandidate,
    TrainingJob,
)
from app.pipeline_state import select_actionable_candidate
from app.ui_feedback import toast_fragment, toast_redirect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def _backend_post(path: str, json: dict | None = None) -> dict:
    response = httpx.post(
        f"{settings.agent_backend_url}{path}",
        json=json,
        headers=_headers(),
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()


def _pipeline_state(db: DBSession) -> dict:
    """Compute the current pipeline state for the SIRADAKI card."""

    # Active batch: approved candidates not yet locked or baked
    ready_count = (
        db.query(func.count(TrainingCandidate.id))
        .filter(
            TrainingCandidate.approved == True,  # noqa: E712
            TrainingCandidate.training_job_id.is_(None),
            TrainingCandidate.model_version_id.is_(None),
        )
        .scalar()
        or 0
    )

    # Running training job
    running_job = (
        db.query(TrainingJob)
        .filter(TrainingJob.status.in_(("pending", "running")))
        .order_by(TrainingJob.id.desc())
        .first()
    )

    # Running eval
    running_eval = (
        db.query(EvalRun)
        .filter(EvalRun.status.in_(("pending", "running")))
        .order_by(EvalRun.id.desc())
        .first()
    )
    # Candidate model waiting for action (trained but not deployed)
    # Look for the most recent ModelVersion that is eval-completed but not deployed
    candidate_model = None
    if not running_job and not running_eval:
        inactive_models = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.deployment_status == "inactive",
                ModelVersion.eval_status.in_(("passed", "failed")),
            )
            .order_by(ModelVersion.id.desc())
            .limit(50)
            .all()
        )
        candidate_model = select_actionable_candidate(inactive_models)

    # Latest eval for the candidate model
    candidate_eval = None
    if candidate_model:
        candidate_eval = (
            db.query(EvalRun)
            .filter(
                EvalRun.model_version_id == candidate_model.id,
                EvalRun.status == "completed",
            )
            .order_by(EvalRun.id.desc())
            .first()
        )

    # Determine the phase
    if running_job:
        phase = "training"
    elif running_eval:
        phase = "evaluating"
        # Find its model version
        if not candidate_model:
            candidate_model = (
                db.query(ModelVersion)
                .filter(ModelVersion.id == running_eval.model_version_id)
                .first()
            )
    elif candidate_model and candidate_model.eval_status == "passed":
        phase = "ready_to_publish"
    elif candidate_model and candidate_model.eval_status == "failed":
        phase = "eval_failed"
    else:
        phase = "ready"

    # Quality score from best eval
    quality_score = None
    if candidate_eval and candidate_eval.metrics_json:
        q = candidate_eval.metrics_json.get("quality_score")
        if q is not None:
            quality_score = int(round(q * 100))

    return {
        "phase": phase,
        "ready_count": ready_count,
        "running_job": running_job,
        "running_eval": running_eval,
        "candidate_model": candidate_model,
        "quality_score": quality_score,
    }


@router.get("/pipeline", response_class=HTMLResponse)
def pipeline(request: Request, db: DBSession = Depends(get_db)):
    # Active deployment
    active_deployment = (
        db.query(Deployment)
        .filter(Deployment.environment == "production", Deployment.status == "active")
        .order_by(Deployment.id.desc())
        .first()
    )
    active_model = None
    active_eval = None
    if active_deployment:
        active_model = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == active_deployment.model_version_id)
            .first()
        )
        if active_model:
            latest_eval = (
                db.query(EvalRun)
                .filter(
                    EvalRun.model_version_id == active_model.id,
                    EvalRun.status == "completed",
                )
                .order_by(EvalRun.id.desc())
                .first()
            )
            active_eval = latest_eval

    active_quality_score = None
    if active_eval and active_eval.metrics_json:
        q = active_eval.metrics_json.get("quality_score")
        if q is not None:
            active_quality_score = int(round(q * 100))

    state = _pipeline_state(db)

    return templates.TemplateResponse(
        "pipeline.html",
        {
            "request": request,
            "active_deployment": active_deployment,
            "active_model": active_model,
            "active_quality_score": active_quality_score,
            **state,
        },
    )


@router.post("/pipeline/train", response_class=HTMLResponse)
def pipeline_train(_csrf: None = Depends(require_csrf)):
    """Start a new training job using the active batch."""
    try:
        result = _backend_post("/training-jobs", {})
        job_id = result.get("id")
        return toast_fragment(
            f"Eğitim başlatıldı · Job #{job_id}",
            kind="success",
            extra_headers={"HX-Refresh": "true"},
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(str(detail), kind="error", status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Pipeline train failed")
        return toast_fragment(str(exc), kind="error", status_code=502)


@router.post("/pipeline/{version_name}/approve-and-deploy", response_class=HTMLResponse)
def pipeline_approve_and_deploy(
    version_name: str,
    _csrf: None = Depends(require_csrf),
):
    try:
        _backend_post(
            f"/models/{version_name}/approve-and-deploy",
            {"environment": "production", "actor": settings.admin_user},
        )
        return toast_fragment(
            f"{version_name} onaylandı ve yayınlandı.",
            kind="success",
            extra_headers={"HX-Refresh": "true"},
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(str(detail), kind="error", status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Pipeline approve-and-deploy failed: %s", version_name)
        return toast_fragment(str(exc), kind="error", status_code=502)


@router.post("/pipeline/{version_name}/discard", response_class=HTMLResponse)
def pipeline_discard(
    version_name: str,
    _csrf: None = Depends(require_csrf),
):
    try:
        _backend_post(f"/models/{version_name}/discard")
        return toast_fragment(
            f"{version_name} iptal edildi · veri serbest bırakıldı.",
            kind="warning",
            extra_headers={"HX-Refresh": "true"},
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(str(detail), kind="error", status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Pipeline discard failed: %s", version_name)
        return toast_fragment(str(exc), kind="error", status_code=502)


@router.post("/pipeline/rollback", response_class=HTMLResponse)
def pipeline_rollback(_csrf: None = Depends(require_csrf)):
    try:
        _backend_post("/deployments/production/rollback", {"actor": settings.admin_user})
        return toast_fragment(
            "Önceki versiyona geri dönüldü.",
            kind="warning",
            extra_headers={"HX-Refresh": "true"},
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(str(detail), kind="error", status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Pipeline rollback failed")
        return toast_fragment(str(exc), kind="error", status_code=502)
