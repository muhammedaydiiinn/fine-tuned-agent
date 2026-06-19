"""Model registry endpoint'leri — Milestone 6'da doldurulacak."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/models")
def list_models():
    # TODO: Milestone 6 — model versiyonları
    return {"status": "not_implemented", "milestone": 6}


@router.post("/models/{version_name}/deploy")
def deploy_model(version_name: str):
    # TODO: Milestone 6 — vLLM'e deploy
    return {"status": "not_implemented", "milestone": 6}
