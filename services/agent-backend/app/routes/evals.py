"""Evaluation run API."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models import EvalRun, ModelVersion
from app.core import model_runtime


def _safe_path(raw: str) -> Path:
    """Resolve *raw* and ensure it falls within the configured data directory.

    Raises HTTPException 400 if the resolved path escapes the data root, guarding
    against path-traversal attacks if the DB value is ever manipulated.
    """
    data_root = Path(settings.data_dir).resolve()
    resolved = Path(raw).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError:
        logger.warning(
            "Path traversal attempt blocked: raw=%r resolved=%s data_root=%s",
            raw,
            resolved,
            data_root,
        )
        raise HTTPException(
            status_code=400,
            detail="Requested file path is outside the allowed data directory",
        )
    return resolved
from app.schemas import CreateEvalRunRequest, EvalRunResponse
from app.workers.queue import enqueue_eval_job

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/eval-runs", response_model=EvalRunResponse, status_code=201)
def create_eval_run(
    body: CreateEvalRunRequest,
    db: DBSession = Depends(get_db),
):
    model_version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == body.model_version_id)
        .first()
    )
    if not model_version:
        raise HTTPException(status_code=404, detail="Model version not found")
    existing_run = (
        db.query(EvalRun)
        .filter(
            EvalRun.model_version_id == body.model_version_id,
            EvalRun.status.in_(("pending", "running")),
        )
        .first()
    )
    if existing_run:
        raise HTTPException(
            status_code=409,
            detail=f"Evaluation run {existing_run.id} is already in progress for this model",
        )
    artifact = model_runtime.inspect_artifact(model_version.merged_path)
    if not artifact["valid"]:
        raise HTTPException(
            status_code=422,
            detail=f"Model artifact is invalid: {artifact['error']}",
        )
    target = model_runtime.serving_target(model_version)
    active = model_runtime.active_model(db)
    active_slot = model_runtime.serving_target(active)["slot"] if active else None
    if (
        model_version.deployment_status == "inactive"
        and target["mode"] != "mock"
        and target["slot"] == active_slot
    ):
        raise HTTPException(
            status_code=409,
            detail="Candidate evaluation cannot use the active production serving slot",
        )
    health = model_runtime.check_serving_target(target)
    if not health.get("healthy"):
        raise HTTPException(
            status_code=503,
            detail=f"Model serving target is unhealthy: {health.get('error', 'unknown error')}",
        )

    metadata = dict(model_version.metadata_json or {})
    metadata["artifact_manifest"] = artifact
    metadata["serving_health"] = health
    model_version.metadata_json = metadata
    model_version.eval_status = "running"

    run = EvalRun(
        model_version_id=body.model_version_id,
        status="pending",
        metrics_json={
            "deployment_evidence": {
                "artifact_sha256": artifact["sha256"],
                "artifact_root": artifact["root"],
                "serving_target": target,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        progress_current=0,
        progress_total=15,
    )
    db.add(run)
    db.add(model_version)
    db.commit()
    db.refresh(run)

    try:
        enqueue_eval_job(run.id, body.model_version_id)
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"Failed to enqueue eval job: {exc}"[:1000]
        model_version.eval_status = "failed"
        db.commit()
        logger.exception("Failed to enqueue eval run id=%d", run.id)
        raise HTTPException(status_code=503, detail="Eval queue is unavailable") from exc

    logger.info(
        "Eval run created and enqueued: id=%d model_version_id=%d",
        run.id,
        body.model_version_id,
    )
    return run


@router.get("/eval-runs", response_model=list[EvalRunResponse])
def list_eval_runs(
    model_version_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: DBSession = Depends(get_db),
):
    query = db.query(EvalRun)
    if model_version_id is not None:
        query = query.filter(EvalRun.model_version_id == model_version_id)
    if status:
        query = query.filter(EvalRun.status == status)
    return query.order_by(EvalRun.created_at.desc()).limit(limit).all()


@router.get("/eval-runs/{eval_run_id}", response_model=EvalRunResponse)
def get_eval_run(eval_run_id: int, db: DBSession = Depends(get_db)):
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/eval-runs/{eval_run_id}/logs")
def get_eval_run_logs(
    eval_run_id: int,
    tail: int = Query(default=100, ge=1, le=5000),
    db: DBSession = Depends(get_db),
):
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    logs = ""
    if run.logs_path:
        try:
            path = _safe_path(run.logs_path)
            if path.exists():
                logs = "\n".join(path.read_text(encoding="utf-8").splitlines()[-tail:])
        except HTTPException:
            raise
        except OSError as exc:
            logger.warning("Could not read eval log %s: %s", run.logs_path, exc)
            logs = f"[log read error: {exc}]"

    return {"eval_run_id": eval_run_id, "status": run.status, "logs": logs}


@router.get("/eval-runs/{eval_run_id}/results")
def get_eval_run_results(eval_run_id: int, db: DBSession = Depends(get_db)):
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    if not run.results_path:
        raise HTTPException(status_code=404, detail="Eval results are not available")

    try:
        path = _safe_path(run.results_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Eval results file not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        logger.error("Invalid eval results JSON for run id=%d: %s", eval_run_id, exc)
        raise HTTPException(status_code=500, detail="Eval results file is invalid") from exc
    except OSError as exc:
        logger.error("Could not read eval results for run id=%d: %s", eval_run_id, exc)
        raise HTTPException(status_code=500, detail="Could not read eval results") from exc
    return payload
