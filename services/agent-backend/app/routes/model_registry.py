"""Model registry, serving target, deployment and rollback endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from app.core import deployment_policy, model_runtime, serving_orchestrator, serving_state
from app.config import settings
from app.db import get_db
from app.models import Deployment, EvalRun, ModelVersion, TrainingCandidate, TrainingJob
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
    # Only real deploy-gate runs count. Simulation / real_log_judge runs also live
    # in eval_runs for the same model but carry no deployment evidence, so without
    # this run_kind filter a later simulation would shadow the gate run and block
    # deployment ("no immutable deployment evidence").
    return (
        db.query(EvalRun)
        .filter(
            EvalRun.model_version_id == model_id,
            EvalRun.status == "completed",
            EvalRun.run_kind == "scenario_gate",
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


def _approve_model(db: DBSession, model: ModelVersion) -> EvalRun:
    """Apply approval invariants without committing the surrounding transaction."""
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
    db.flush()
    return run


def _deploy_approved_model(
    db: DBSession,
    model: ModelVersion,
    body: DeploymentRequest,
    *,
    action: str,
) -> Deployment:
    """Deploy one approved model and atomically update registry lifecycle state."""
    gate_run = _assert_deployable(db, model, body.environment)
    target = (
        model_runtime.production_serving_target()
        if body.environment == "production"
        else model_runtime.serving_target(model)
    )
    smoke = [{"role": "user", "content": "Antworte mit einem kurzen JSON-Objekt."}]
    promotion = None
    is_prod_real = body.environment == "production" and target["mode"] == "real"
    if is_prod_real:
        # The 19 GB promote + vLLM readiness wait run in a background thread so this
        # request returns immediately; progress is shown in the panel banner. The
        # eval gate (_assert_deployable, above) still controls what may be deployed.
        if serving_orchestrator.is_busy():
            raise HTTPException(
                status_code=409,
                detail="A model serving transition is already in progress",
            )
        health = {"healthy": None, "serving_status": "pending", "detail": "deploy queued"}
    else:
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
    if current and body.environment != "production" and target["mode"] != "mock":
        current_slot = model_runtime.serving_target(current.model_version)["slot"]
        if current_slot == target["slot"]:
            raise HTTPException(
                status_code=409,
                detail="Deployment target must use the inactive blue/green slot",
            )

    now = datetime.now(timezone.utc)
    metadata = _metadata(model)
    metadata["deployed_at"] = now.isoformat()
    if body.environment == "production":
        metadata["production_serving"] = target
        if promotion is not None:
            metadata["production_promotion"] = promotion
    model.metadata_json = metadata
    deployment = Deployment(
        model_version_id=model.id,
        environment=body.environment,
        status="active",
        deployed_at=now,
        rollback_model_version_id=previous_model_id,
        metadata_json={
            "action": action,
            "eval_run_id": gate_run.id,
            "gate_policy_version": CURRENT_EVAL_POLICY_VERSION,
            "serving_health": health,
            "serving_target": target,
            "production_promotion": promotion,
            "actor": body.actor,
            "deployed_at": now.isoformat(),
        },
    )
    affected_models = [model]
    if current:
        current.status = "superseded"
        affected_models.append(current.model_version)

    db.add(deployment)
    db.flush()
    for affected_model in {item.id: item for item in affected_models}.values():
        _sync_model_deployment_state(db, affected_model)
    _bake_candidates_for_model(db, model.id)
    db.commit()
    db.refresh(deployment)
    if is_prod_real:
        serving_state.set(
            status="loading",
            detail=f"Deploying {model.version_name}…",
            active_model=model.version_name,
        )
        serving_orchestrator.start_transition(
            merged_path=model.merged_path or "",
            model_name=model.version_name,
            force_promote=True,
            deployment_id=deployment.id,
        )
    logger.info(
        "Model deployment completed: action=%s version=%s environment=%s previous_model_id=%s",
        action,
        model.version_name,
        body.environment,
        previous_model_id,
    )
    return deployment


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
    _approve_model(db, model)
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
    return _deploy_approved_model(db, model, body, action="deploy")


def _bake_candidates_for_model(db: DBSession, model_id: int) -> int:
    """Set model_version_id on all candidates that were locked into the training job
    that produced this model version. Returns the count of baked candidates."""
    job = (
        db.query(TrainingJob)
        .filter(TrainingJob.model_version_id == model_id)
        .first()
    )
    if not job:
        return 0
    updated = (
        db.query(TrainingCandidate)
        .filter(
            TrainingCandidate.training_job_id == job.id,
            TrainingCandidate.model_version_id.is_(None),
        )
        .update({"model_version_id": model_id}, synchronize_session="fetch")
    )
    logger.info("Baked %d candidates into model_version_id=%d", updated, model_id)
    return updated


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
    promotion = None
    is_prod_real = environment == "production" and target["mode"] == "real"
    if is_prod_real:
        # Promote + readiness wait run in the background; the request returns at once
        # and the panel banner reports progress.
        if serving_orchestrator.is_busy():
            raise HTTPException(
                status_code=409,
                detail="A model serving transition is already in progress",
            )
        target = model_runtime.production_serving_target()
        health = {"healthy": None, "serving_status": "pending", "detail": "rollback queued"}
    else:
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
            "production_promotion": promotion,
            "actor": body.actor if body else None,
            "deployed_at": now.isoformat(),
        },
    )
    current.status = "rolled_back"
    target_metadata = _metadata(target_model)
    target_metadata["deployed_at"] = now.isoformat()
    if environment == "production":
        target_metadata["production_serving"] = target
        if promotion is not None:
            target_metadata["production_promotion"] = promotion
    target_model.metadata_json = target_metadata
    db.add(rollback_deployment)
    db.flush()
    for affected_model in {
        current.model_version.id: current.model_version,
        target_model.id: target_model,
    }.values():
        _sync_model_deployment_state(db, affected_model)

    # Re-open the rolled-back version's baked candidates so they enter the next
    # training batch. Without this the corrections that made v15 would be "consumed"
    # but not present in the now-active v14, causing silent data loss across versions.
    rolled_back_model_id = current.model_version_id
    released = (
        db.query(TrainingCandidate)
        .filter(TrainingCandidate.model_version_id == rolled_back_model_id)
        .update(
            {"model_version_id": None, "training_job_id": None},
            synchronize_session="fetch",
        )
    )
    if released:
        logger.warning(
            "Rollback released %d baked candidates from model_version_id=%d back to active batch",
            released,
            rolled_back_model_id,
        )

    db.commit()
    db.refresh(rollback_deployment)
    if is_prod_real:
        serving_state.set(
            status="loading",
            detail=f"Rolling back to {target_model.version_name}…",
            active_model=target_model.version_name,
        )
        serving_orchestrator.start_transition(
            merged_path=target_model.merged_path or "",
            model_name=target_model.version_name,
            force_promote=True,
            deployment_id=rollback_deployment.id,
        )
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


@router.post(
    "/models/{version_name}/approve-and-deploy",
    response_model=DeploymentResponse,
    status_code=201,
    summary="Approve model and immediately deploy to production in one step",
)
def approve_and_deploy(
    version_name: str,
    body: DeploymentRequest,
    db: DBSession = Depends(get_db),
):
    """Combined approve + deploy for the Pipeline UI's 'Onayla & Yayınla' action."""
    model = _model_or_404(db, version_name)
    _approve_model(db, model)
    return _deploy_approved_model(db, model, body, action="approve-and-deploy")


@router.post(
    "/models/{version_name}/discard",
    response_model=ModelVersionResponse,
    summary="Retire a candidate model and release its locked training batch",
)
def discard_model(version_name: str, db: DBSession = Depends(get_db)):
    """Mark a candidate model as retired and release its training candidates so they
    can be included in the next training run."""
    model = _model_or_404(db, version_name)
    lifecycle = (_metadata(model)).get("lifecycle_status")
    if lifecycle in ("deployed", "active_production"):
        raise HTTPException(status_code=409, detail="Cannot discard an active deployed model")

    # Release candidates locked into this model's training job (but not yet baked)
    job = (
        db.query(TrainingJob)
        .filter(TrainingJob.model_version_id == model.id)
        .first()
    )
    released = 0
    if job:
        released = (
            db.query(TrainingCandidate)
            .filter(
                TrainingCandidate.training_job_id == job.id,
                TrainingCandidate.model_version_id.is_(None),
            )
            .update({"training_job_id": None}, synchronize_session="fetch")
        )

    metadata = _metadata(model)
    metadata["lifecycle_status"] = "retired"
    metadata["retired_at"] = datetime.now(timezone.utc).isoformat()
    model.metadata_json = metadata
    model.deployment_status = "retired"
    db.commit()
    db.refresh(model)
    logger.info(
        "Model discarded: version=%s released_candidates=%d",
        model.version_name,
        released,
    )
    return model
