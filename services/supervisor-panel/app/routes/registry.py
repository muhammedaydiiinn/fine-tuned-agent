"""Model registry and deployment controls."""
from __future__ import annotations

import html
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import Deployment, EvalRun, ModelVersion

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


@router.get("/model-registry", response_class=HTMLResponse)
def registry(request: Request, db: DBSession = Depends(get_db)):
    models = (
        db.query(ModelVersion)
        .order_by(ModelVersion.created_at.desc())
        .all()
    )
    deployments = (
        db.query(Deployment)
        .order_by(Deployment.created_at.desc())
        .limit(100)
        .all()
    )
    latest_runs: dict[int, EvalRun] = {}
    for run in db.query(EvalRun).order_by(EvalRun.created_at.desc()).all():
        latest_runs.setdefault(run.model_version_id, run)
    active = {
        deployment.environment: deployment
        for deployment in deployments
        if deployment.status == "active"
    }
    return templates.TemplateResponse(
        "model_registry.html",
        {
            "request": request,
            "models": models,
            "deployments": deployments,
            "latest_runs": latest_runs,
            "active": active,
        },
    )


@router.post("/model-registry/{version_name}/verify", response_class=HTMLResponse)
def verify(version_name: str):
    return _action(f"/models/{version_name}/verify-artifact", "Artifact verified")


@router.post("/model-registry/{version_name}/approve", response_class=HTMLResponse)
def approve(version_name: str):
    return _action(f"/models/{version_name}/approve", "Model approved")


@router.post("/model-registry/{model_version_id}/quality-check", response_class=HTMLResponse)
def quality_check(model_version_id: int):
    try:
        result = _backend_post("/eval-runs", {"model_version_id": model_version_id})
        return HTMLResponse(
            '<div class="alert alert-success">Quality check started. '
            f'<a href="/eval-jobs/{int(result["id"])}">View progress</a></div>'
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return HTMLResponse(
            f'<div class="alert alert-error">{html.escape(str(detail))}</div>',
            status_code=exc.response.status_code,
        )


@router.get("/model-registry/{version_name}", response_class=HTMLResponse)
def model_detail(version_name: str, request: Request, db: DBSession = Depends(get_db)):
    model = (
        db.query(ModelVersion)
        .filter(ModelVersion.version_name == version_name)
        .first()
    )
    if not model:
        return HTMLResponse("<div class='alert alert-error'>Model not found.</div>", status_code=404)
    eval_runs = (
        db.query(EvalRun)
        .filter(EvalRun.model_version_id == model.id)
        .order_by(EvalRun.created_at.desc())
        .all()
    )
    deployments = (
        db.query(Deployment)
        .filter(
            (Deployment.model_version_id == model.id)
            | (Deployment.rollback_model_version_id == model.id)
        )
        .order_by(Deployment.deployed_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "model_detail.html",
        {
            "request": request,
            "model": model,
            "eval_runs": eval_runs,
            "deployments": deployments,
        },
    )


@router.post("/model-registry/{version_name}/deploy", response_class=HTMLResponse)
def deploy(version_name: str, environment: str = Form("production")):
    return _action(
        f"/models/{version_name}/deploy",
        f"Model deployed to {environment}",
        {"environment": environment, "actor": settings.admin_user},
    )


@router.post("/model-registry/{version_name}/serving-target", response_class=HTMLResponse)
def serving_target(
    version_name: str,
    mode: str = Form(...),
    base_url: str = Form(""),
    model_name: str = Form(...),
    slot: str = Form("candidate"),
):
    return _action(
        f"/models/{version_name}/serving-target",
        "Serving target verified",
        {
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "slot": slot,
        },
    )


@router.post("/model-registry/rollback/{environment}", response_class=HTMLResponse)
def rollback(environment: str):
    return _action(
        f"/deployments/{environment}/rollback",
        f"{environment.title()} rolled back",
        {"actor": settings.admin_user},
    )


def _action(path: str, success: str, payload: dict | None = None) -> HTMLResponse:
    try:
        _backend_post(path, payload)
        return HTMLResponse(
            f'<div class="alert alert-success">{html.escape(success)}. '
            '<a href="/model-registry">Refresh registry</a></div>'
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return HTMLResponse(
            f'<div class="alert alert-error">{html.escape(str(detail))}</div>',
            status_code=exc.response.status_code,
        )
    except Exception as exc:
        logger.exception("Registry action failed: path=%s", path)
        return HTMLResponse(
            f'<div class="alert alert-error">{html.escape(str(exc))}</div>',
            status_code=502,
        )
