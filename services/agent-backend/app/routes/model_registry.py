"""Model registry endpoints — to be implemented in Milestone 6."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/models")
def list_models():
    # TODO: Milestone 6 — model version list
    return {"status": "not_implemented", "milestone": 6}


@router.post("/models/{version_name}/deploy")
def deploy_model(version_name: str):
    # TODO: Milestone 6 — deploy to vLLM
    return {"status": "not_implemented", "milestone": 6}
