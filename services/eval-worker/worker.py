"""Eval worker — consumes fixed-scenario evaluation jobs from Redis."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import redis
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from evals import gate, run_eval
from evals.judge_batch import call_judge_score, handle_judge_batch
from models import EvalRun, ModelVersion, TrainingCandidate, TrainingJob, TurnEvaluation
from queue_recovery import (
    PROCESSING_QUEUE_NAME,
    QUEUE_NAME,
    is_terminal_status,
    requeue_interrupted_jobs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("redis").setLevel(logging.WARNING)
logger = logging.getLogger("eval-worker")

POLL_INTERVAL = 5


def _update_run(db: Session, run: EvalRun, **fields) -> None:
    for key, value in fields.items():
        setattr(run, key, value)
    db.commit()


def _log(log_path: str, message: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def _ensure_candidate_loaded(serving: dict) -> None:
    """Load the candidate's LoRA adapter onto the shared vLLM server, if needed.

    When the candidate is served as a runtime LoRA adapter (serving.lora_path
    set), the adapter must be registered on the shared server before the model
    name resolves. Idempotent: an already-loaded adapter is treated as success.
    Non-fatal on failure — the readiness preflight reports a clear reason.
    """
    import httpx

    lora_path = serving.get("lora_path")
    if str(serving.get("mode")) != "real" or not lora_path:
        return
    base_url = str(serving.get("base_url") or "").rstrip("/")
    name = str(serving.get("model_name") or "")
    if not base_url or not name:
        return
    try:
        resp = httpx.post(
            f"{base_url}/load_lora_adapter",
            json={"lora_name": name, "lora_path": str(lora_path)},
            timeout=180.0,
        )
        if resp.status_code == 200:
            logger.info("LoRA adapter loaded for eval: %s", name)
        elif "already" in (resp.text or "").lower():
            logger.info("LoRA adapter already loaded: %s", name)
        else:
            logger.warning(
                "LoRA adapter load returned %s: %s", resp.status_code, resp.text[:200]
            )
    except Exception as exc:
        logger.warning("LoRA adapter load attempt failed for %s: %s", name, exc)


def _unload_candidate_lora(serving: dict | None) -> None:
    """Unload the candidate's LoRA adapter after eval to free the LoRA slot.
    Non-fatal and idempotent — a stale adapter would only be re-used harmlessly."""
    import httpx

    if not isinstance(serving, dict) or not serving.get("lora_path"):
        return
    base_url = str(serving.get("base_url") or "").rstrip("/")
    name = str(serving.get("model_name") or "")
    if not base_url or not name:
        return
    try:
        httpx.post(
            f"{base_url}/unload_lora_adapter",
            json={"lora_name": name},
            timeout=60.0,
        )
        logger.info("LoRA adapter unloaded after eval: %s", name)
    except Exception as exc:  # noqa: BLE001 — cleanup must never fail the run
        logger.warning("LoRA adapter unload failed for %s: %s", name, exc)


def _candidate_serving_ready(serving: dict) -> tuple[bool, str]:
    """Check the candidate's serving endpoint is up and serving the expected model.

    Returns (ready, detail). Mock serving is always ready. For real serving we
    require the endpoint to answer /models AND to actually serve the expected
    model name — otherwise every scenario would return invalid output and the
    model would look like it "failed" the quality gate when it was simply never
    served (the common single-GPU case where vllm-candidate is not running).
    """
    import httpx  # already a dependency (used by run_eval)

    if str(serving.get("mode")) != "real":
        return True, "mock serving"
    base_url = str(serving.get("base_url") or "").rstrip("/")
    model_name = str(serving.get("model_name") or "")
    if not base_url:
        return False, "serving target has no base_url"
    try:
        resp = httpx.get(f"{base_url}/models", timeout=5.0)
        resp.raise_for_status()
        served = {m.get("id") for m in (resp.json().get("data") or [])}
    except Exception as exc:
        return False, f"candidate endpoint unreachable at {base_url} ({exc})"
    if model_name and model_name not in served:
        return (
            False,
            f"endpoint {base_url} is up but not serving '{model_name}' "
            f"(served: {sorted(s for s in served if s)})",
        )
    return True, f"serving '{model_name}' at {base_url}"


def _write_results(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _release_candidates(db: Session, model_version_id: int) -> None:
    """Release training candidates locked into the job that produced model_version_id.
    Sets training_job_id=NULL so they re-enter the active batch for the next run."""
    job = (
        db.query(TrainingJob)
        .filter(TrainingJob.model_version_id == model_version_id)
        .first()
    )
    if not job:
        return
    released = (
        db.query(TrainingCandidate)
        .filter(
            TrainingCandidate.training_job_id == job.id,
            TrainingCandidate.model_version_id.is_(None),
        )
        .update({"training_job_id": None}, synchronize_session="fetch")
    )
    db.commit()
    logger.info(
        "Released %d candidates from failed eval — model_version_id=%d job_id=%d",
        released,
        model_version_id,
        job.id,
    )


def _judge_scenarios(
    db: Session,
    eval_run_id: int,
    model_version_id: int,
    results: list[dict],
) -> dict:
    """Additive: LLM-judge each scenario turn, write TurnEvaluation(source='scenario').

    Returns an aggregate for metrics["judge"]. NEVER touches gate/quality_score.
    """
    overalls: list[float] = []
    errors = 0
    for result in results:
        policy = {
            "intent": result.get("actual_intent"),
            "next_action": result.get("actual_next_action"),
            "allowed_to_continue": result.get("allowed_to_continue"),
            "agent_response": result.get("agent_response"),
        }
        verdict: dict | None
        try:
            verdict = call_judge_score(
                result.get("customer_text"),
                result.get("state_before"),
                result.get("agent_response"),
                policy,
            )
        except Exception:
            logger.exception("scenario judge failed — scenario=%s", result.get("scenario_id"))
            errors += 1
            verdict = None
        db.add(TurnEvaluation(
            eval_run_id=eval_run_id,
            turn_id=None,
            scenario_id=result.get("scenario_id"),
            source="scenario",
            model_version_id=model_version_id,
            judge_model="production-base",
            scores_json=(verdict or {}).get("scores"),
            overall=(verdict or {}).get("overall"),
            suggestion=(verdict or {}).get("suggestion"),
            rationale=(verdict or {}).get("rationale"),
            passed=(verdict or {}).get("passed"),
            status="pending",
            raw_judge_json=verdict,
        ))
        if verdict and verdict.get("overall") is not None:
            overalls.append(float(verdict["overall"]))
    db.commit()
    judged = len(overalls)
    return {
        "count": len(results),
        "judged": judged,
        "errors": errors,
        "mean_overall": round(sum(overalls) / judged, 4) if judged else None,
        "pass_rate": (
            round(sum(1 for o in overalls if o >= settings.judge_pass_threshold) / judged, 4)
            if judged else None
        ),
        "pass_threshold": settings.judge_pass_threshold,
    }


def handle_eval(
    db: Session,
    eval_run_id: int,
    model_version_id: int,
    payload: dict | None = None,
) -> None:
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if not run:
        logger.error("EvalRun id=%d not found", eval_run_id)
        return
    if is_terminal_status(run.status):
        logger.info(
            "EvalRun id=%d is terminal (%s); skipping duplicate delivery",
            eval_run_id,
            run.status,
        )
        return
    if run.model_version_id != model_version_id:
        _update_run(
            db,
            run,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message="Queue payload model_version_id does not match EvalRun",
        )
        return

    model_version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == model_version_id)
        .first()
    )
    log_path = str(Path(settings.data_dir) / "eval_logs" / f"{eval_run_id}.log")
    results_path = Path(settings.data_dir) / "eval_results" / f"{eval_run_id}.json"

    _update_run(
        db,
        run,
        status="running",
        started_at=datetime.now(timezone.utc),
        logs_path=log_path,
        progress_current=0,
        progress_total=15,
        error_message=None,
    )
    _log(
        log_path,
        f"evaluation started — run={eval_run_id} model_version_id={model_version_id}"
        f" version={model_version.version_name if model_version else 'missing'}",
    )

    candidate_serving: dict | None = None
    try:
        if not model_version:
            raise ValueError(f"ModelVersion id={model_version_id} not found")

        # Preflight: the candidate must actually be served before we score it.
        # Without this, an unserved candidate (e.g. vllm-candidate not running on
        # a single GPU) makes every scenario return invalid output and the model
        # is misreported as a quality "failed" when it was never tested. Mark the
        # run "blocked" with a clear reason instead, leave the model unjudged
        # ("pending"), and keep its candidates locked so a re-eval can run once
        # the candidate is served — no retraining needed.
        preflight_evidence = (run.metrics_json or {}).get("deployment_evidence")
        preflight_serving = (
            preflight_evidence.get("serving_target")
            if isinstance(preflight_evidence, dict)
            else None
        )
        if isinstance(preflight_serving, dict):
            candidate_serving = preflight_serving
            _ensure_candidate_loaded(preflight_serving)
            ready, detail = _candidate_serving_ready(preflight_serving)
            if not ready:
                _log(log_path, f"BLOCKED — candidate not served: {detail}")
                model_version.eval_status = "pending"
                db.add(model_version)
                _update_run(
                    db,
                    run,
                    status="blocked",
                    finished_at=datetime.now(timezone.utc),
                    error_message=(
                        "Candidate model not served — start vllm-candidate with the "
                        f"candidate model before evaluating. {detail}"
                    )[:1000],
                )
                logger.warning(
                    "Eval blocked (candidate not served): run=%d model=%d — %s",
                    eval_run_id,
                    model_version_id,
                    detail,
                )
                return

        def progress_cb(current: int, total: int, label: str) -> None:
            _update_run(
                db,
                run,
                progress_current=current,
                progress_total=total,
            )
            _log(log_path, f"scenario completed — {current}/{total} {label}")

        report = run_eval.run(
            model_version_id=model_version_id,
            agent_backend_url=settings.agent_backend_url,
            scenarios_path=Path(__file__).parent / "evals" / "scenarios.jsonl",
            progress_cb=progress_cb,
            eval_run_id=eval_run_id,
            api_key=settings.api_key,
            eval_internal_token=settings.eval_internal_token,
            timeout_seconds=settings.eval_request_timeout_seconds,
            model_version_header=model_version_id,
        )
        _write_results(results_path, report)

        run_metadata = dict(run.metrics_json or {})
        deployment_evidence = run_metadata.get("deployment_evidence")
        if not isinstance(deployment_evidence, dict):
            raise ValueError("Eval run is missing its deployment evidence snapshot")

        metrics = dict(report["metrics"])
        metrics["quality_score"] = report["quality_score"]
        metrics["deployment_evidence"] = deployment_evidence
        deployment_gate = gate.evaluate(
            metrics,
            settings.deployment_gate_thresholds,
        )
        serving = deployment_evidence.get("serving_target")
        if not isinstance(serving, dict):
            raise ValueError("Eval run deployment evidence has no serving target")
        deployment_gate["evidence_mode"] = str(
            serving.get("mode")
            or "unknown"
        )
        deployment_gate["model_version_id"] = model_version_id
        metrics["deployment_gate"] = deployment_gate
        # Additive LLM-judge pass over the scenario turns — visibility only,
        # never feeds the deterministic gate below.
        if settings.judge_enabled:
            try:
                metrics["judge"] = _judge_scenarios(
                    db, eval_run_id, model_version_id, report.get("results", [])
                )
            except Exception:
                logger.exception("scenario judge pass failed — run=%d (non-fatal)", eval_run_id)
        passed = bool(deployment_gate["passed"])
        model_version.eval_status = "passed" if passed else "failed"
        metadata = dict(model_version.metadata_json or {})
        metadata["lifecycle_status"] = (
            "deployed"
            if str(model_version.deployment_status).startswith("active_")
            else ("evaluated" if passed else "candidate")
        )
        metadata["latest_eval_run_id"] = eval_run_id
        metadata["eval_policy_version"] = gate.POLICY_VERSION
        model_version.metadata_json = metadata
        db.add(model_version)
        _update_run(
            db,
            run,
            status="completed",
            metrics_json=metrics,
            results_path=str(results_path),
            progress_current=report["scenario_count"],
            progress_total=report["scenario_count"],
            finished_at=datetime.now(timezone.utc),
        )
        if not passed:
            # Gate failed — release candidates so they can be retrained
            _release_candidates(db, model_version_id)
        _log(
            log_path,
            f"evaluation completed — score={report['quality_score']:.4f}"
            f" verdict={model_version.eval_status} gate={gate.POLICY_VERSION}",
        )
        logger.info(
            "Evaluation completed: run=%d model=%d score=%.4f",
            eval_run_id,
            model_version_id,
            report["quality_score"],
        )
    except Exception as exc:
        message = str(exc)
        logger.exception("Evaluation failed: run=%d", eval_run_id)
        _log(log_path, f"ERROR — {message}")
        if model_version:
            model_version.eval_status = "failed"
            db.add(model_version)
            _release_candidates(db, model_version_id)
        _update_run(
            db,
            run,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message=message[:1000],
        )
    finally:
        # Free the LoRA slot on the shared server regardless of outcome (blocked
        # runs never loaded one; _unload is a no-op then).
        _unload_candidate_lora(candidate_serving)


HANDLERS = {"run_eval": handle_eval, "judge_batch": handle_judge_batch}


def main() -> None:
    client = redis.from_url(settings.redis_url, decode_responses=True)
    recovered = requeue_interrupted_jobs(client)
    logger.info("Eval worker started. Queue: %s", QUEUE_NAME)
    if recovered:
        logger.warning("Recovered %d interrupted eval job(s)", recovered)

    while True:
        raw = client.brpoplpush(
            QUEUE_NAME,
            PROCESSING_QUEUE_NAME,
            timeout=POLL_INTERVAL,
        )
        if raw is None:
            continue

        try:
            message = json.loads(raw)
            job_type = message.get("job_type", "")
            payload = message.get("payload") or {}
            eval_run_id = payload.get("eval_run_id")
            model_version_id = payload.get("model_version_id")
            if not eval_run_id or not model_version_id:
                logger.warning("Eval message missing IDs: %s", message)
                continue

            handler = HANDLERS.get(job_type)
            if handler is None:
                logger.warning("Unknown eval job type: %s", job_type)
                continue

            db = get_db()
            try:
                handler(db, int(eval_run_id), int(model_version_id), payload)
            finally:
                db.close()
        except Exception:
            logger.exception("Eval message processing failed")
        else:
            client.lrem(PROCESSING_QUEUE_NAME, 1, raw)


if __name__ == "__main__":
    main()
