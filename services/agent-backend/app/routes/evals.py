"""Eval endpoints — to be implemented in Milestone 5."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/eval-runs")
def list_eval_runs():
    # TODO: Milestone 5 — eval results list
    return {"status": "not_implemented", "milestone": 5}


@router.post("/eval-runs")
def create_eval_run():
    # TODO: Milestone 5 — start eval run
    return {"status": "not_implemented", "milestone": 5}
