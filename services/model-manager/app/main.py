from __future__ import annotations

import secrets

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.docker_control import restart_vllm_server
from app.publisher import inspect_artifact, publish_directory, resolve_under


app = FastAPI(title="CallShield Model Manager", version="1.0.0")


class PromoteRequest(BaseModel):
    source_path: str = Field(..., min_length=1)
    target_path: str | None = None
    restart: bool = True


def _authorize(x_model_manager_token: str = Header(default="")) -> None:
    if not settings.model_manager_token:
        return
    if not secrets.compare_digest(x_model_manager_token, settings.model_manager_token):
        raise HTTPException(status_code=401, detail="Invalid model manager token")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/promote")
def promote_model(
    body: PromoteRequest,
    x_model_manager_token: str = Header(default=""),
):
    _authorize(x_model_manager_token)
    try:
        source = resolve_under(body.source_path, settings.model_dir)
        target = resolve_under(body.target_path or settings.production_model_path, settings.model_dir)
        source_manifest = inspect_artifact(str(source))
        manifest = publish_directory(source, target)
        restart_result = restart_vllm_server() if body.restart else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "status": "promoted",
        "source": source_manifest,
        "target": manifest,
        "restart": restart_result,
    }
