"""Resolve and validate model serving targets from the registry."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Deployment, ModelVersion

logger = logging.getLogger(__name__)

REQUIRED_MODEL_FILES = ("config.json",)
WEIGHT_PATTERNS = ("*.safetensors", "*.bin")


def lifecycle_status(model: ModelVersion) -> str:
    return str((model.metadata_json or {}).get("lifecycle_status") or "candidate")


def serving_target(model: ModelVersion) -> dict[str, str]:
    metadata = model.metadata_json or {}
    production = metadata.get("production_serving")
    if (
        str(model.deployment_status).startswith("active_production")
        and isinstance(production, dict)
    ):
        return {
            "mode": str(production.get("mode") or settings.vllm_mode),
            "base_url": str(production.get("base_url") or settings.vllm_base_url),
            "model_name": str(production.get("model_name") or settings.production_served_model_name),
            "slot": str(production.get("slot") or "production"),
        }
    serving = metadata.get("serving") if isinstance(metadata.get("serving"), dict) else {}
    target = {
        "mode": str(serving.get("mode") or settings.vllm_mode),
        "base_url": str(serving.get("base_url") or ""),
        "model_name": str(serving.get("model_name") or model.version_name),
        "slot": str(serving.get("slot") or "candidate"),
    }
    # LoRA-served candidate: carry the adapter path so the eval worker can hot-load
    # it on the shared server and routing hits the adapter (not the base).
    if serving.get("lora_path"):
        target["lora_path"] = str(serving["lora_path"])
    return target


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
        "model_name": (
            settings.production_served_model_name
            if settings.vllm_mode == "real"
            else settings.vllm_model_name
        ),
        "slot": "production-fallback",
    }


def production_serving_target() -> dict[str, str]:
    return {
        "mode": settings.vllm_mode,
        "base_url": settings.vllm_base_url,
        "model_name": (
            settings.production_served_model_name
            if settings.vllm_mode == "real"
            else settings.vllm_model_name
        ),
        "slot": "production" if settings.vllm_mode == "real" else "mock",
    }


def artifact_is_valid(path_value: str | None) -> dict[str, Any]:
    """Cheap structural validity check — no hashing.

    ``inspect_artifact`` reads and sha256-hashes every file in the model directory
    (~19 GB), which takes minutes and must never run on a hot path like startup.
    This verifies only that the directory exists and contains the required files.
    """
    if not path_value:
        return {"valid": False, "error": "merged_path is not configured"}
    root = Path(path_value).resolve()
    model_root = Path(settings.model_dir).resolve()
    if root != model_root and model_root not in root.parents:
        return {"valid": False, "error": f"artifact path must be inside {model_root}"}
    if not root.is_dir():
        return {"valid": False, "error": "artifact directory does not exist"}
    missing = [name for name in REQUIRED_MODEL_FILES if not (root / name).is_file()]
    has_weights = any(
        file.is_file()
        for pattern in WEIGHT_PATTERNS
        for file in root.glob(pattern)
    )
    if not has_weights:
        missing.append("*.safetensors or *.bin")
    if missing:
        return {"valid": False, "error": f"missing required artifact files: {', '.join(missing)}"}
    return {"valid": True, "root": str(root)}


# Sidecar caching the sha256 manifest of an (immutable, write-once) merged model.
# Hashing 16 GB takes ~48 s; recomputing it on every eval/deploy request blocks the
# HTTP call past the proxy timeout. The sidecar lets an unchanged artifact return its
# manifest instantly; the (path,size,mtime) signature invalidates it if files change.
_ARTIFACT_SIDECAR = ".artifact_manifest.json"


def _artifact_content_files(root: Path) -> list[Path]:
    """Model files to hash — every regular file except the sidecar itself."""
    return sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and item.name != _ARTIFACT_SIDECAR
    )


def _artifact_signature(files: list[Path], root: Path) -> str:
    """Cheap fingerprint (no content read) — path, size and mtime of each file."""
    sig = hashlib.sha256()
    for file in files:
        st = file.stat()
        sig.update(f"{file.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return sig.hexdigest()


def _compute_artifact_manifest(root: Path, files: list[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for file in files:
        file_digest = hashlib.sha256()
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(chunk)
        relative = str(file.relative_to(root))
        checksum = file_digest.hexdigest()
        size = file.stat().st_size
        digest.update(f"{relative}:{size}:{checksum}\n".encode())
        entries.append({"path": relative, "size": size, "sha256": checksum})
    return {
        "valid": True,
        "root": str(root),
        "sha256": digest.hexdigest(),
        "file_count": len(entries),
        "files": entries,
    }


def write_artifact_manifest(path_value: str) -> dict[str, Any]:
    """Compute the manifest and persist it as a sidecar (requires a writable mount,
    e.g. training-worker/model-manager). Called after a merge so later read-only
    consumers (agent-backend) get an instant cache hit instead of re-hashing."""
    manifest = inspect_artifact(path_value)
    if not manifest.get("valid"):
        return manifest
    root = Path(path_value).resolve()
    files = _artifact_content_files(root)
    payload = {"signature": _artifact_signature(files, root), "manifest": manifest}
    (root / _ARTIFACT_SIDECAR).write_text(json.dumps(payload), encoding="utf-8")
    return manifest


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

    files = _artifact_content_files(root)
    signature = _artifact_signature(files, root)

    # Fast path: an unchanged artifact returns its cached manifest without hashing.
    sidecar = root / _ARTIFACT_SIDECAR
    if sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            manifest = cached.get("manifest")
            if cached.get("signature") == signature and isinstance(manifest, dict):
                # The sidecar travels with the directory (it may have been written at
                # a staging path like <name>.partial before publication); always
                # report the path it actually lives at now, not the stored one.
                manifest["root"] = str(root)
                return manifest
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("artifact sidecar unreadable, recomputing — %s", sidecar)

    manifest = _compute_artifact_manifest(root, files)

    # Best-effort cache write; the mount is read-only for some services (agent-backend).
    try:
        sidecar.write_text(
            json.dumps({"signature": signature, "manifest": manifest}), encoding="utf-8"
        )
    except OSError:
        logger.debug("artifact sidecar not writable (read-only mount) — %s", sidecar)

    return manifest


def ensure_lora_loaded(target: dict[str, str]) -> None:
    """Hot-load a candidate's LoRA adapter on the shared server so its served model
    name resolves before the pre-eval health check. No-op unless the target carries
    a lora_path (merged/mock targets skip it). Non-fatal on error."""
    lora_path = target.get("lora_path")
    if str(target.get("mode")) != "real" or not lora_path:
        return
    base_url = str(target.get("base_url") or "").rstrip("/")
    name = str(target.get("model_name") or "")
    if not base_url or not name:
        return
    try:
        httpx.post(
            f"{base_url}/load_lora_adapter",
            json={"lora_name": name, "lora_path": str(lora_path)},
            timeout=180.0,
        )
        logger.info("LoRA adapter load requested for eval: %s", name)
    except Exception:  # noqa: BLE001 — health check reports the real state next
        logger.warning("LoRA adapter load attempt failed for %s", name, exc_info=True)


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


def wait_for_serving_target(
    target: dict[str, str],
    *,
    timeout_seconds: float,
    smoke_messages: list[dict] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {"healthy": False, "error": "not checked"}
    while time.monotonic() < deadline:
        last = check_serving_target(target, smoke_messages=smoke_messages)
        if last.get("healthy"):
            return last
        time.sleep(5)
    return {
        **last,
        "healthy": False,
        "error": f"serving target did not become healthy within {timeout_seconds:.0f}s: {last.get('error', 'unknown error')}",
    }


def promote_production_model(source_path: str) -> dict[str, Any]:
    if settings.vllm_mode == "mock":
        return {"status": "skipped", "mode": "mock"}
    headers = (
        {"X-Model-Manager-Token": settings.model_manager_token}
        if settings.model_manager_token
        else {}
    )
    payload = {
        "source_path": source_path,
        "target_path": settings.production_model_path,
        "restart": True,
    }
    try:
        with httpx.Client(timeout=settings.vllm_start_timeout_seconds) as client:
            response = client.post(
                f"{settings.model_manager_url.rstrip('/')}/promote",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Model manager promotion failed: {exc}") from exc
