"""Resolve and validate model serving targets from the registry."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Deployment, ModelVersion

REQUIRED_MODEL_FILES = ("config.json",)
WEIGHT_PATTERNS = ("*.safetensors", "*.bin")


def lifecycle_status(model: ModelVersion) -> str:
    return str((model.metadata_json or {}).get("lifecycle_status") or "candidate")


def serving_target(model: ModelVersion) -> dict[str, str]:
    metadata = model.metadata_json or {}
    serving = metadata.get("serving") if isinstance(metadata.get("serving"), dict) else {}
    return {
        "mode": str(serving.get("mode") or settings.vllm_mode),
        "base_url": str(serving.get("base_url") or ""),
        "model_name": str(serving.get("model_name") or model.version_name),
        "slot": str(serving.get("slot") or "candidate"),
    }


def active_model(db: Session, environment: str = "production") -> ModelVersion | None:
    deployment = (
        db.query(Deployment)
        .filter(
            Deployment.environment == environment,
            Deployment.status == "active",
        )
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .first()
    )
    return deployment.model_version if deployment else None


def resolve_for_turn(
    db: Session,
    requested_model_version_id: int | None = None,
) -> tuple[ModelVersion | None, dict[str, str]]:
    if requested_model_version_id is not None:
        model = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == requested_model_version_id)
            .first()
        )
        if not model:
            raise ValueError(f"ModelVersion id={requested_model_version_id} not found")
        return model, serving_target(model)

    model = active_model(db)
    if model:
        return model, serving_target(model)
    return None, {
        "mode": settings.vllm_mode,
        "base_url": settings.vllm_base_url,
        "model_name": settings.vllm_model_name,
        "slot": "production-fallback",
    }


def inspect_artifact(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {"valid": False, "error": "merged_path is not configured", "files": []}

    root = Path(path_value).resolve()
    model_root = Path(settings.model_dir).resolve()
    if root != model_root and model_root not in root.parents:
        return {
            "valid": False,
            "error": f"artifact path must be inside {model_root}",
            "files": [],
        }
    if not root.is_dir():
        return {"valid": False, "error": "artifact directory does not exist", "files": []}

    missing = [name for name in REQUIRED_MODEL_FILES if not (root / name).is_file()]
    weights = sorted({
        file
        for pattern in WEIGHT_PATTERNS
        for file in root.glob(pattern)
        if file.is_file()
    })
    if not weights:
        missing.append("*.safetensors or *.bin")
    if missing:
        return {
            "valid": False,
            "error": f"missing required artifact files: {', '.join(missing)}",
            "files": [],
        }

    files: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for file in sorted(item for item in root.rglob("*") if item.is_file()):
        file_digest = hashlib.sha256()
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(chunk)
        relative = str(file.relative_to(root))
        checksum = file_digest.hexdigest()
        size = file.stat().st_size
        digest.update(f"{relative}:{size}:{checksum}\n".encode())
        files.append({"path": relative, "size": size, "sha256": checksum})
    return {
        "valid": True,
        "root": str(root),
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def check_serving_target(
    target: dict[str, str],
    *,
    smoke_messages: list[dict] | None = None,
) -> dict[str, Any]:
    if target["mode"] == "mock":
        return {"healthy": True, "mode": "mock", "models": [target["model_name"]]}
    if not target["base_url"]:
        return {"healthy": False, "error": "serving base_url is not configured"}

    try:
        with httpx.Client(timeout=settings.model_health_timeout_seconds) as client:
            models_response = client.get(f"{target['base_url'].rstrip('/')}/models")
            models_response.raise_for_status()
            models_payload = models_response.json()
            model_ids = [
                str(item.get("id"))
                for item in models_payload.get("data", [])
                if isinstance(item, dict) and item.get("id")
            ]
            if target["model_name"] not in model_ids:
                return {
                    "healthy": False,
                    "error": f"model {target['model_name']} is not loaded",
                    "models": model_ids,
                }
            if smoke_messages:
                response = client.post(
                    f"{target['base_url'].rstrip('/')}/chat/completions",
                    json={
                        "model": target["model_name"],
                        "messages": smoke_messages,
                        "temperature": 0,
                        "max_tokens": 64,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                if not str(content).strip():
                    raise ValueError("smoke response was empty")
            return {"healthy": True, "mode": "real", "models": model_ids}
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        return {"healthy": False, "error": str(exc)}
