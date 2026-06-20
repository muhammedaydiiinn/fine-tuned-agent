"""Model registry, serving target, deployment and rollback endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from app.core import deployment_policy, model_runtime
from app.config import settings
from app.db import get_db
from app.models import Deployment, EvalRun, ModelVersion
from app.schemas import (
    ConfigureServingRequest,
    DeploymentRequest,
    DeploymentResponse,
    ModelVersionResponse,
    RegisterModelRequest,
    RollbackRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)

CURRENT_EVAL_POLICY_VERSION = "m6-gate-v1"


def _model_or_404(db: DBSession, version_name: str) -> ModelVersion:
    model = (
        db.query(ModelVersion)
        .filter(ModelVersion.version_name == version_name)
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Model version not found")
    return model


def _metadata(model: ModelVersion) -> dict:
    return dict(model.metadata_json or {})


def _latest_gate_run(db: DBSession, model_id: int) -> EvalRun | None:
    return (
        db.query(EvalRun)
        .filter(
            EvalRun.model_version_id == model_id,
            EvalRun.status == "completed",
        )
        .order_by(EvalRun.finished_at.desc(), EvalRun.id.desc())
        .first()
    )


def _assert_evidence_matches(
    model: ModelVersion,
    run: EvalRun,
    artifact: dict,
) -> None:
    try:
        deployment_policy.validate_deployment_evidence(
            run.metrics_json,
            artifact,
            model_runtime.serving_target(model),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _sync_model_deployment_state(db: DBSession, model: ModelVersion) -> None:
    environments = sorted(
        environment
        for environment, in (
            db.query(Deployment.environment)
            .filter(
                Deployment.model_version_id == model.id,
                Deployment.status == "active",
            )
            .all()
        )
    )
    model.deployment_status, metadata = deployment_policy.deployment_state(
        environments,
        _metadata(model),
    )
    model.metadata_json = metadata


def _lock_environment(db: DBSession, environment: str) -> None:
    """Serialize deploy/rollback transactions for one environment."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"model-deployment:{environment}"},
    )


def _assert_deployable(
    db: DBSession,
    model: ModelVersion,
    environment: str,
) -> EvalRun:
    metadata = _metadata(model)
    if metadata.get("lifecycle_status") != "approved":
        raise HTTPException(status_code=409, detail="Model lifecycle status must be approved")
    if model.eval_status != "passed":
        raise HTTPException(status_code=409, detail="Model has not passed evaluation")
    run = _latest_gate_run(db, model.id)
    gate = (run.metrics_json or {}).get("deployment_gate") if run else None
    if not isinstance(gate, dict) or not gate.get("passed"):
        raise HTTPException(status_code=409, detail="Latest evaluation did not pass deployment gate")
    if gate.get("policy_version") != CURRENT_EVAL_POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail="Latest evaluation uses an obsolete deployment gate policy",
        )
    if (
        environment == "production"
        and gate.get("evidence_mode") != "real"
        and not settings.allow_mock_production_deploy
    ):
        raise HTTPException(
            status_code=409,
            detail="Production deployment requires a real candidate-serving evaluation",
        )
    artifact = model_runtime.inspect_artifact(model.merged_path)
    if not artifact["valid"]:
        raise HTTPException(status_code=422, detail=artifact["error"])
    _assert_evidence_matches(model, run, artifact)
    return run


@router.get("/models", response_model=list[ModelVersionResponse])
def list_models(
    lifecycle_status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: DBSession = Depends(get_db),
):
    models = (
        db.query(ModelVersion)
        .order_by(ModelVersion.created_at.desc())
        .limit(limit)
        .all()
    )
    if lifecycle_status:
        models = [
            model for model in models
            if model_runtime.lifecycle_status(model) == lifecycle_status
        ]
    return models


@router.post("/models", response_model=ModelVersionResponse, status_code=201)
def register_model(body: RegisterModelRequest, db: DBSession = Depends(get_db)):
    existing = (
        db.query(ModelVersion)
        .filter(ModelVersion.version_name == body.version_name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Model version already exists")

    artifact = model_runtime.inspect_artifact(body.merged_path)
    if not artifact["valid"]:
        raise HTTPException(status_code=422, detail=artifact["error"])
    model = ModelVersion(
        version_name=body.version_name,
        base_model=body.base_model,
        lora_path=body.lora_path,
        merged_path=body.merged_path,
        dataset_version=body.dataset_version,
        eval_status="pending",
        deployment_status="inactive",
        metadata_json={
            "lifecycle_status": "candidate",
            "artifact_manifest": artifact,
        },
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


@router.get("/models/{version_name}", response_model=ModelVersionResponse)
def get_model(version_name: str, db: DBSession = Depends(get_db)):
    return _model_or_404(db, version_name)


@router.post(
    "/models/{version_name}/verify-artifact",
    response_model=ModelVersionResponse,
)
def verify_artifact(version_name: str, db: DBSession = Depends(get_db)):
    model = _model_or_404(db, version_name)
    artifact = model_runtime.inspect_artifact(model.merged_path)
    metadata = _metadata(model)
    metadata["artifact_manifest"] = artifact
    model.metadata_json = metadata
    db.commit()
    db.refresh(model)
    if not artifact["valid"]:
        raise HTTPException(status_code=422, detail=artifact["error"])
    return model


@router.post(
    "/models/{version_name}/serving-target",
    response_model=ModelVersionResponse,
)
def configure_serving_target(
    version_name: str,
    body: ConfigureServingRequest,
    db: DBSession = Depends(get_db),
):
    model = _model_or_404(db, version_name)
    if str(model.deployment_status).startswith("active_"):
        raise HTTPException(
            status_code=409,
            detail="Serving target of an active model cannot be changed",
        )
    running_eval = (
        db.query(EvalRun)
        .filter(
            EvalRun.model_version_id == model.id,
            EvalRun.status.in_(("pending", "running")),
        )
        .first()
    )
    if running_eval:
        raise HTTPException(
            status_code=409,
            detail=f"Serving target cannot change while evaluation run {running_eval.id} is active",
        )
    artifact = model_runtime.inspect_artifact(model.merged_path)
    if not artifact["valid"]:
        raise HTTPException(status_code=422, detail=artifact["error"])
    if body.mode == "real" and not body.base_url:
        raise HTTPException(status_code=422, detail="base_url is required in real mode")

    target = body.model_dump()
    active = model_runtime.active_model(db)
    if active and active.id != model.id and body.mode != "mock":
        active_slot = model_runtime.serving_target(active)["slot"]
        if body.slot == active_slot:
            raise HTTPException(
                status_code=409,
                detail=f"Serving slot {body.slot} is active in production",
            )
    health = model_runtime.check_serving_target(target)
    if not health.get("healthy"):
        raise HTTPException(
            status_code=503,
            detail=f"Serving target is not healthy: {health.get('error', 'unknown error')}",
        )

    metadata = _metadata(model)
    metadata.update({
        "artifact_manifest": artifact,
        "serving": target,
        "serving_health": health,
        "serving_verified_at": datetime.now(timezone.utc).isoformat(),
        "lifecycle_status": "candidate",
    })
    model.metadata_json = metadata
    model.eval_status = "pending"
    db.commit()
    db.refresh(model)
    return model


@router.post("/models/{version_name}/approve", response_model=ModelVersionResponse)
def approve_model(version_name: str, db: DBSession = Depends(get_db)):
    model = _model_or_404(db, version_name)
    artifact = model_runtime.inspect_artifact(model.merged_path)
    if not artifact["valid"]:
        raise HTTPException(status_code=422, detail=artifact["error"])
    if model.eval_status != "passed":
        raise HTTPException(status_code=409, detail="Only models with passed eval can be approved")
    run = _latest_gate_run(db, model.id)
    gate = (run.metrics_json or {}).get("deployment_gate") if run else None
    if not isinstance(gate, dict) or not gate.get("passed"):
        raise HTTPException(status_code=409, detail="Deployment gate has not passed")
    if gate.get("policy_version") != CURRENT_EVAL_POLICY_VERSION:
        raise HTTPException(status_code=409, detail="Evaluation gate policy is obsolete")
    _assert_evidence_matches(model, run, artifact)
    metadata = _metadata(model)
    metadata["artifact_manifest"] = artifact
    metadata["lifecycle_status"] = "approved"
    metadata["approved_at"] = datetime.now(timezone.utc).isoformat()
    metadata["approved_eval_run_id"] = run.id
    model.metadata_json = metadata
    db.commit()
    db.refresh(model)
    return model


@router.post(
    "/models/{version_name}/deploy",
    response_model=DeploymentResponse,
    status_code=201,
)
def deploy_model(
    version_name: str,
    body: DeploymentRequest,
    db: DBSession = Depends(get_db),
):
    model = _model_or_404(db, version_name)
    gate_run = _assert_deployable(db, model, body.environment)
    target = model_runtime.serving_target(model)
    smoke = [{"role": "user", "content": "Antworte mit einem kurzen JSON-Objekt."}]
    health = model_runtime.check_serving_target(target, smoke_messages=smoke)
    if not health.get("healthy"):
        raise HTTPException(
            status_code=503,
            detail=f"Pre-deploy health check failed: {health.get('error', 'unknown error')}",
        )

    _lock_environment(db, body.environment)
    current = (
        db.query(Deployment)
        .filter(
            Deployment.environment == body.environment,
            Deployment.status == "active",
        )
        .with_for_update()
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .first()
    )
    previous_model_id = current.model_version_id if current else None
    if current and current.model_version_id == model.id:
        raise HTTPException(status_code=409, detail="Model is already active")
    if current and target["mode"] != "mock":
        current_slot = model_runtime.serving_target(current.model_version)["slot"]
        if current_slot == target["slot"]:
            raise HTTPException(
                status_code=409,
                detail="Deployment target must use the inactive blue/green slot",
            )

    now = datetime.now(timezone.utc)
    deployment = Deployment(
        model_version_id=model.id,
        environment=body.environment,
        status="active",
        deployed_at=now,
        rollback_model_version_id=previous_model_id,
        metadata_json={
            "action": "deploy",
            "eval_run_id": gate_run.id,
            "gate_policy_version": CURRENT_EVAL_POLICY_VERSION,
            "serving_health": health,
            "serving_target": target,
            "actor": body.actor,
            "deployed_at": now.isoformat(),
        },
    )
    affected_models = [model]
    if current:
        current.status = "superseded"
        affected_models.append(current.model_version)

    metadata = _metadata(model)
    metadata["deployed_at"] = now.isoformat()
    model.metadata_json = metadata
    db.add(deployment)
    db.flush()
    for affected_model in {item.id: item for item in affected_models}.values():
        _sync_model_deployment_state(db, affected_model)
    db.commit()
    db.refresh(deployment)
    logger.info(
        "Model deployed: version=%s environment=%s previous_model_id=%s",
        model.version_name,
        body.environment,
        previous_model_id,
    )
    return deployment


@router.post(
    "/deployments/{environment}/rollback",
    response_model=DeploymentResponse,
    status_code=201,
)
def rollback(environment: str, body: RollbackRequest | None = None, db: DBSession = Depends(get_db)):
    if environment not in {"staging", "production"}:
        raise HTTPException(status_code=422, detail="Invalid deployment environment")
    _lock_environment(db, environment)
    current = (
        db.query(Deployment)
        .filter(Deployment.environment == environment, Deployment.status == "active")
        .with_for_update()
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .first()
    )
    if not current or not current.rollback_model_version_id:
        raise HTTPException(status_code=409, detail="No rollback target is available")
    target_model = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == current.rollback_model_version_id)
        .first()
    )
    if not target_model:
        raise HTTPException(status_code=409, detail="Rollback model no longer exists")

    target = model_runtime.serving_target(target_model)
    health = model_runtime.check_serving_target(target)
    if not health.get("healthy"):
        raise HTTPException(
            status_code=503,
            detail=f"Rollback target is unhealthy: {health.get('error', 'unknown error')}",
        )

    now = datetime.now(timezone.utc)
    rollback_deployment = Deployment(
        model_version_id=target_model.id,
        environment=environment,
        status="active",
        deployed_at=now,
        rollback_model_version_id=current.model_version_id,
        metadata_json={
            "action": "rollback",
            "rolled_back_deployment_id": current.id,
            "serving_health": health,
            "serving_target": target,
            "actor": body.actor if body else None,
            "deployed_at": now.isoformat(),
        },
    )
    current.status = "rolled_back"
    target_metadata = _metadata(target_model)
    target_metadata["deployed_at"] = now.isoformat()
    target_model.metadata_json = target_metadata
    db.add(rollback_deployment)
    db.flush()
    for affected_model in {
        current.model_version.id: current.model_version,
        target_model.id: target_model,
    }.values():
        _sync_model_deployment_state(db, affected_model)
    db.commit()
    db.refresh(rollback_deployment)
    logger.warning(
        "Deployment rolled back: environment=%s from=%d to=%d",
        environment,
        current.model_version_id,
        target_model.id,
    )
    return rollback_deployment


@router.get("/deployments", response_model=list[DeploymentResponse])
def list_deployments(
    environment: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: DBSession = Depends(get_db),
):
    query = db.query(Deployment)
    if environment:
        query = query.filter(Deployment.environment == environment)
    return query.order_by(Deployment.created_at.desc()).limit(limit).all()
