"""Customer-simulation API — run reactive customer↔agent conversations as tests."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import nullslast
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core import model_runtime, sim_runner
from app.db import get_db
from app.models import EvalRun, Turn, TurnEvaluation

router = APIRouter()
logger = logging.getLogger(__name__)


class SimulationRequest(BaseModel):
    count: int | None = None
    max_turns: int | None = None
    # Optional: simulate a specific (e.g. candidate) model for before/after
    # comparison. Defaults to the active production model.
    model_version_id: int | None = None


@router.post("/simulations", status_code=201)
def create_simulation(body: SimulationRequest, db: DBSession = Depends(get_db)):
    """Start a batch of reactive customer conversations against a model.

    Defaults to the active production model; pass model_version_id to simulate a
    candidate (routed via the eval header) for a before/after quality comparison.
    """
    if body.model_version_id is not None:
        from app.models import ModelVersion
        model = db.query(ModelVersion).filter(ModelVersion.id == body.model_version_id).first()
        if model is None:
            raise HTTPException(status_code=404, detail="Model version not found")
    else:
        model = model_runtime.active_model(db)
    if model is None:
        raise HTTPException(status_code=409, detail="No active production model to simulate against")
    count = int(body.count or settings.sim_default_count)
    max_turns = int(body.max_turns or settings.sim_max_turns)
    run = EvalRun(
        model_version_id=model.id,
        run_kind="simulation",
        status="pending",
        progress_current=0,
        progress_total=count,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    sim_runner.start_simulation(run.id, count, max_turns)
    return {
        "simulation_id": run.id,
        "model_version_id": model.id,
        "count": count,
        "max_turns": max_turns,
    }


@router.get("/simulations/{sim_id}")
def get_simulation(
    sim_id: int,
    limit: int = Query(default=300, ge=1, le=1000),
    db: DBSession = Depends(get_db),
):
    run = (
        db.query(EvalRun)
        .filter(EvalRun.id == sim_id, EvalRun.run_kind == "simulation")
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    rows = (
        db.query(TurnEvaluation)
        .filter(TurnEvaluation.eval_run_id == sim_id)
        .order_by(nullslast(TurnEvaluation.overall.asc()))  # worst first
        .limit(limit)
        .all()
    )
    turn_ids = [r.turn_id for r in rows if r.turn_id]
    turns = {t.id: t for t in db.query(Turn).filter(Turn.id.in_(turn_ids)).all()} if turn_ids else {}

    results = []
    for r in rows:
        turn = turns.get(r.turn_id)
        results.append({
            "turn_evaluation_id": r.id,
            "turn_id": r.turn_id,
            "persona": r.scenario_id,
            "overall": r.overall,
            "scores": r.scores_json,
            "suggestion": r.suggestion,
            "rationale": r.rationale,
            "status": r.status,
            "customer_text": turn.customer_text if turn else None,
            "agent_response": turn.agent_response if turn else None,
        })
    return {
        "simulation_id": run.id,
        "status": run.status,
        "progress": {"current": run.progress_current, "total": run.progress_total},
        "metrics": run.metrics_json,
        "turns": results,
    }
