"""Eval endpoint'leri — Milestone 5'te doldurulacak."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/eval-runs")
def list_eval_runs():
    # TODO: Milestone 5 — eval sonuçları
    return {"status": "not_implemented", "milestone": 5}


@router.post("/eval-runs")
def create_eval_run():
    # TODO: Milestone 5 — eval başlat
    return {"status": "not_implemented", "milestone": 5}
