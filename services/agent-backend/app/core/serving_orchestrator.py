"""Run the production vLLM serving transition in the background.

``model_runtime.promote_production_model`` (a 19 GB copy + vLLM container restart)
followed by ``wait_for_serving_target`` (polling up to ``vllm_start_timeout_seconds``)
used to run inline in the FastAPI lifespan and in the deploy/rollback request
handlers, blocking them for minutes. This module moves that work onto a daemon
thread that reports progress through ``serving_state``.

Only one transition runs at a time (guarded by ``_lock``); concurrent requests get
``False`` from ``start_transition`` and should surface a "busy" message.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings
from app.core import model_runtime, serving_state

logger = logging.getLogger(__name__)

_lock = threading.Lock()

_SMOKE = [{"role": "user", "content": "Antworte mit einem kurzen JSON-Objekt."}]


def is_busy() -> bool:
    return _lock.locked()


def start_transition(
    *,
    merged_path: str,
    model_name: str | None,
    force_promote: bool = False,
    deployment_id: int | None = None,
) -> bool:
    """Spawn the background transition. Returns False if one is already running."""
    if not _lock.acquire(blocking=False):
        logger.warning("Serving transition already running — skipping new request")
        return False
    thread = threading.Thread(
        target=_run,
        kwargs={
            "merged_path": merged_path,
            "model_name": model_name,
            "force_promote": force_promote,
            "deployment_id": deployment_id,
        },
        name="serving-transition",
        daemon=True,
    )
    thread.start()
    return True


def _run(
    *,
    merged_path: str,
    model_name: str | None,
    force_promote: bool,
    deployment_id: int | None,
) -> None:
    try:
        target = model_runtime.production_serving_target()
        if target["mode"] == "mock":
            serving_state.set(
                status="ready",
                detail="Mock mode — no vLLM model load required.",
                active_model=model_name,
                served_model_name=target["model_name"],
                models=[target["model_name"]],
                error=None,
            )
            return

        # Smart skip: if the production directory already holds a valid artifact and
        # this is not a forced (re)deploy, don't re-copy 19 GB or restart vLLM — doing
        # so would kill a vLLM that is mid-load. Just wait for it to finish serving.
        do_promote = force_promote
        if not force_promote:
            artifact = model_runtime.artifact_is_valid(settings.production_model_path)
            do_promote = not artifact.get("valid")
            if not do_promote:
                logger.info(
                    "Production artifact already present at %s — skipping promote/restart",
                    settings.production_model_path,
                )

        if do_promote:
            serving_state.set(
                status="promoting",
                detail="Publishing model and restarting vLLM…",
                active_model=model_name,
                served_model_name=target["model_name"],
                error=None,
            )
            model_runtime.promote_production_model(merged_path)

        serving_state.set(
            status="loading",
            detail="Waiting for vLLM to load the model…",
            active_model=model_name,
            served_model_name=target["model_name"],
            error=None,
        )
        health = model_runtime.wait_for_serving_target(
            target,
            timeout_seconds=settings.vllm_start_timeout_seconds,
            smoke_messages=_SMOKE,
        )

        if health.get("healthy"):
            serving_state.set(
                status="ready",
                detail="Model is serving.",
                active_model=model_name,
                served_model_name=target["model_name"],
                models=health.get("models", []),
                error=None,
            )
            logger.info("Serving transition complete — model=%s is healthy", model_name)
        else:
            serving_state.set(
                status="error",
                detail="Model did not become healthy.",
                active_model=model_name,
                served_model_name=target["model_name"],
                error=health.get("error"),
            )
            logger.error("Serving transition failed: %s", health.get("error"))

        if deployment_id is not None:
            _patch_deployment_health(deployment_id, health)
    except Exception as exc:  # noqa: BLE001 — never let the thread die silently
        logger.exception("Serving transition crashed")
        serving_state.set(status="error", detail="Serving transition crashed.", error=str(exc))
    finally:
        _lock.release()


def _patch_deployment_health(deployment_id: int, health: dict[str, Any]) -> None:
    """Record the final serving health on the deployment row (own session)."""
    from app.db import SessionLocal
    from app.models import Deployment

    db = SessionLocal()
    try:
        deployment = db.query(Deployment).filter(Deployment.id == deployment_id).first()
        if deployment is not None:
            metadata = dict(deployment.metadata_json or {})
            metadata["serving_health"] = health
            deployment.metadata_json = metadata
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record serving health on deployment %s", deployment_id)
    finally:
        db.close()
