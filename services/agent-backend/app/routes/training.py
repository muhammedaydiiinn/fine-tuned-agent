"""Training candidate ve job endpoint'leri — Milestone 3/4'te doldurulacak."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/training-candidates")
def list_candidates():
    # TODO: Milestone 3 — training candidate listesi
    return {"status": "not_implemented", "milestone": 3}


@router.post("/training-candidates/export-jsonl")
def export_jsonl():
    # TODO: Milestone 3 — JSONL export
    return {"status": "not_implemented", "milestone": 3}


@router.get("/training-jobs")
def list_jobs():
    # TODO: Milestone 4 — job listesi
    return {"status": "not_implemented", "milestone": 4}


@router.post("/training-jobs")
def create_job():
    # TODO: Milestone 4 — job kuyruğa ekle
    return {"status": "not_implemented", "milestone": 4}
