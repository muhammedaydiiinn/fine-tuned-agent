"""Correction endpoint'leri — Milestone 2/3'te doldurulacak."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/corrections")
def list_corrections():
    # TODO: Milestone 2 — correction listesi
    return {"status": "not_implemented", "milestone": 2}


@router.post("/corrections")
def create_correction():
    # TODO: Milestone 2 — correction kaydet + apply_immediately + send_to_training
    return {"status": "not_implemented", "milestone": 2}
