"""Real-log LLM-judge batch — scores recent production turns via /judge/score.

Additive/visibility only: writes TurnEvaluation rows and an aggregate into
EvalRun.metrics_json["judge"]. It NEVER calls gate.evaluate, so the deterministic
deploy gate is untouched. Single-GPU friendly: sequential, per-call timeout.
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from config import settings
from models import EvalRun, ModelVersion, Session as SessionModel, Turn, TurnEvaluation
from queue_recovery import is_terminal_status

logger = logging.getLogger(__name__)

_JUDGE_MODEL_LABEL = "production-base"


def _fetch_turns(db: Session, model_version_name: str | None, limit: int) -> list[Turn]:
    query = (
        db.query(Turn)
        .join(SessionModel, SessionModel.id == Turn.session_id)
        .filter(Turn.agent_response.isnot(None))
        # Exclude eval/self-generated sessions (same pattern as panel review.py).
        .filter(~SessionModel.external_session_id.like("eval-%"))
    )
    if model_version_name:
        query = query.filter(Turn.model_version == model_version_name)
    return list(query.order_by(Turn.created_at.desc()).limit(limit))


def call_judge_score(
    customer_text: str | None,
    state_before: dict | None,
    agent_response: str | None,
    policy_json: dict | None,
) -> dict:
    """POST one turn to agent-backend /judge/score and return the verdict dict."""
    body = {
        "customer_text": customer_text or "",
        "state_before": state_before or {},
        "agent_response": agent_response or "",
        "policy_json": policy_json or {},
    }
    headers = {"X-Eval-Token": settings.eval_internal_token} if settings.eval_internal_token else {}
    with httpx.Client(timeout=settings.judge_request_timeout_seconds) as client:
        resp = client.post(settings.judge_url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _judge_turn(turn: Turn) -> dict:
    return call_judge_score(
        turn.customer_text,
        turn.state_before_json,
        turn.agent_response,
        turn.final_policy_json or turn.repaired_model_json or {},
    )


def handle_judge_batch(db: Session, eval_run_id: int, model_version_id: int, payload: dict) -> None:
    run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
    if run is None:
        logger.error("judge_batch: EvalRun id=%d not found", eval_run_id)
        return
    if is_terminal_status(run.status):
        logger.info("judge_batch: run=%d terminal (%s); skipping", eval_run_id, run.status)
        return

    mv = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    model_version_name = mv.version_name if mv else None
    max_turns = int(payload.get("max_turns") or settings.real_log_max_turns)

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.progress_current = 0
    db.commit()

    turns = _fetch_turns(db, model_version_name, max_turns)
    run.progress_total = len(turns) or 1
    db.commit()
    logger.info(
        "judge_batch started — run=%d model=%s turns=%d", eval_run_id, model_version_name, len(turns)
    )

    overalls: list[float] = []
    errors = 0
    for index, turn in enumerate(turns, 1):
        verdict: dict | None
        try:
            verdict = _judge_turn(turn)
        except Exception:
            logger.exception("judge_batch: scoring failed for turn=%s", turn.id)
            errors += 1
            verdict = None

        db.add(TurnEvaluation(
            eval_run_id=eval_run_id,
            turn_id=turn.id,
            source="real_log",
            model_version_id=model_version_id,
            judge_model=_JUDGE_MODEL_LABEL,
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
        run.progress_current = index
        if index % 5 == 0:
            db.commit()

    judged = len(overalls)
    aggregate = {
        "count": len(turns),
        "judged": judged,
        "errors": errors,
        "mean_overall": round(sum(overalls) / judged, 4) if judged else None,
        "pass_rate": (
            round(sum(1 for o in overalls if o >= settings.judge_pass_threshold) / judged, 4)
            if judged else None
        ),
        "pass_threshold": settings.judge_pass_threshold,
    }
    metrics = dict(run.metrics_json or {})
    metrics["judge"] = aggregate
    run.metrics_json = metrics
    run.status = "completed"
    run.progress_current = len(turns)
    run.progress_total = len(turns) or 1
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("judge_batch completed — run=%d judged=%d agg=%s", eval_run_id, judged, aggregate)
