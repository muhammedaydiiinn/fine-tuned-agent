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
from app.csrf import require_csrf
from app.db import get_db
from app.models import Deployment, EvalRun, ModelVersion
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


@router.get("/model-registry/models/data")
def models_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source — trained models."""
    models = (
        db.query(ModelVersion)
        .order_by(ModelVersion.created_at.desc())
        .all()
    )
    latest_runs: dict[int, EvalRun] = {}
    for run in db.query(EvalRun).order_by(EvalRun.created_at.desc()).all():
        latest_runs.setdefault(run.model_version_id, run)

    rows = []
    for model in models:
        metadata = model.metadata_json or {}
        artifact = metadata.get("artifact_manifest", {})
        serving = metadata.get("serving", {})
        latest_eval = latest_runs.get(model.id)
        gate = (latest_eval.metrics_json or {}).get("deployment_gate", {}) if latest_eval else {}
        lifecycle = metadata.get("lifecycle_status", "candidate")

        # Training source
        dataset_version = html.escape(model.dataset_version or "baseline")
        candidate_ids = metadata.get("dataset_manifest", {}).get("candidate_ids", [])
        training_source = f'<code>{dataset_version}</code>'
        if candidate_ids:
            training_source += f'<div class="text-muted" style="font-size:11px;">{len(candidate_ids)} reviewed turn(s)</div>'

        # Quality check
        if model.eval_status == "passed":
            badge_cls, badge_text = "badge-approved", "Passed"
        elif model.eval_status == "failed":
            badge_cls, badge_text = "badge-error", "Failed"
        else:
            badge_cls, badge_text = "badge-running", "Waiting"
        quality_check = f'<span class="badge {badge_cls}">{badge_text}</span>'
        if latest_eval and latest_eval.metrics_json:
            score = int(round((latest_eval.metrics_json or {}).get("quality_score", 0) * 100))
            quality_check += f'<div class="text-muted" style="font-size:11px;">Score {score}%</div>'

        # Release status
        release_status = f'<span class="badge badge-info">{html.escape(lifecycle)}</span>'

        # Actions — conditional HTMX forms mirroring the Jinja template logic
        vn = html.escape(model.version_name)
        action_parts: list[str] = []
        if model.eval_status in ("pending", "failed"):
            action_parts.append(
                f'<form hx-post="/model-registry/{model.id}/quality-check" hx-target="#registry-result" hx-swap="innerHTML">'
                f'<button class="btn btn-outline btn-sm" type="submit">Run Quality Check</button></form>'
            )
        if (
            model.eval_status == "passed"
            and gate.get("passed")
            and artifact.get("valid")
            and lifecycle not in ("approved", "deployed")
        ):
            action_parts.append(
                f'<form hx-post="/model-registry/{vn}/approve" hx-target="#registry-result" hx-swap="innerHTML">'
                f'<button class="btn btn-outline btn-sm" type="submit">Approve Release</button></form>'
            )
        if lifecycle == "approved" and gate.get("evidence_mode") == "real":
            action_parts.append(
                f'<form hx-post="/model-registry/{vn}/deploy" hx-target="#registry-result" hx-swap="innerHTML">'
                f'<input type="hidden" name="environment" value="production">'
                f'<button class="btn btn-primary btn-sm" type="submit">Make Live</button></form>'
            )
        elif lifecycle == "approved":
            action_parts.append('<span class="badge badge-pending">GPU verification required</span>')

        # Technical details collapsible
        artifact_status = "verified" if artifact and artifact.get("valid", True) else "unverified"
        eval_link = f'<a href="/eval-jobs/{latest_eval.id}">Open quality report #{latest_eval.id}</a>' if latest_eval else ""
        serving_base_url = html.escape(serving.get("base_url", ""))
        serving_model_name = html.escape(serving.get("model_name", model.version_name))
        serving_mode = html.escape(serving.get("mode", "—"))
        serving_slot = html.escape(serving.get("slot", "—"))
        technical = (
            f'<details class="registry-serving-form"><summary>Technical details</summary>'
            f'<div class="technical-summary">Artifact: {artifact_status}<br>'
            f'Runtime: {serving_mode} / {serving_slot}<br>{eval_link}</div>'
            f'<form hx-post="/model-registry/{vn}/verify" hx-target="#registry-result" hx-swap="innerHTML">'
            f'<button class="btn btn-outline btn-sm" type="submit">Verify Artifact</button></form>'
            f'<form hx-post="/model-registry/{vn}/serving-target" hx-target="#registry-result" hx-swap="innerHTML">'
            f'<select name="mode"><option value="real">real</option><option value="mock">mock</option></select>'
            f'<input type="text" name="base_url" placeholder="http://vllm-candidate:8000/v1" value="{serving_base_url}">'
            f'<input type="text" name="model_name" required placeholder="served model name" value="{serving_model_name}">'
            f'<select name="slot"><option value="green">green</option><option value="blue">blue</option><option value="mock">mock</option></select>'
            f'<button class="btn btn-outline btn-sm" type="submit">Verify target</button></form>'
            f'</details>'
        )
        actions = f'<div class="registry-actions">{"".join(action_parts)}</div>{technical}'

        rows.append({
            "version_name": model.version_name,
            "training_source": training_source,
            "quality_check": quality_check,
            "release_status": release_status,
            "created_at": model.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if model.created_at else None,
            "actions": actions,
            "open_id": model.version_name,
        })
    return {"data": rows}


@router.post("/model-registry/{version_name}/verify", response_class=HTMLResponse)
def verify(version_name: str, _csrf: None = Depends(require_csrf)):
    return _action(f"/models/{version_name}/verify-artifact", "Artifact verified")


@router.post("/model-registry/{version_name}/approve", response_class=HTMLResponse)
def approve(version_name: str, _csrf: None = Depends(require_csrf)):
    return _action(f"/models/{version_name}/approve", "Model approved")


@router.post("/model-registry/{model_version_id}/quality-check", response_class=HTMLResponse)
def quality_check(model_version_id: int, _csrf: None = Depends(require_csrf)):
    try:
        result = _backend_post("/eval-runs", {"model_version_id": model_version_id})
        return toast_fragment(
            f"Quality check #{int(result['id'])} started.",
            kind="success",
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(
            str(detail),
            kind="error",
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
        return toast_redirect("/model-registry", "Model not found.", kind="error")
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
def deploy(version_name: str, environment: str = Form("production"), _csrf: None = Depends(require_csrf)):
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
    _csrf: None = Depends(require_csrf),
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
def rollback(environment: str, _csrf: None = Depends(require_csrf)):
    return _action(
        f"/deployments/{environment}/rollback",
        f"{environment.title()} rolled back",
        {"actor": settings.admin_user},
    )


def _action(path: str, success: str, payload: dict | None = None) -> HTMLResponse:
    try:
        _backend_post(path, payload)
        return toast_fragment(success, kind="success")
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = str(exc)
        return toast_fragment(
            str(detail),
            kind="error",
            status_code=exc.response.status_code,
        )
    except Exception as exc:
        logger.exception("Registry action failed: path=%s", path)
        return toast_fragment(
            str(exc),
            kind="error",
            status_code=502,
        )
