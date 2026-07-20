"""Background simulation runner — drives reactive customer↔agent conversations
against the live agent, then judges every agent turn.

Reuses everything: /agent-turn (localhost) persists turns; judge.score grades them;
TurnEvaluation(source='simulation') groups them under an EvalRun(run_kind='simulation').
The customer side is customer_sim (LLM persona, or scripted in mock mode).
"""
import logging
import threading
import uuid
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.core import customer_sim, judge, model_runtime
from app.db import SessionLocal
from app.models import EvalRun, Turn, TurnEvaluation

logger = logging.getLogger(__name__)


def _agent_turn(
    session_id: str,
    customer_text: str,
    eval_model_version_id: int | None = None,
) -> dict:
    headers = {"X-API-Key": settings.api_key} if settings.api_key else {}
    # Route to a specific (non-active) candidate for before/after comparison; the
    # active production model needs no header. Judge stays on the production base.
    if eval_model_version_id is not None:
        headers["X-Eval-Model-Version-ID"] = str(eval_model_version_id)
        if settings.eval_internal_token:
            headers["X-Eval-Token"] = settings.eval_internal_token
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(
            f"{settings.self_base_url.rstrip('/')}/agent-turn",
            json={"session_id": session_id, "customer_text": customer_text},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


def _run_one_conversation(
    run_token: str,
    persona: dict,
    index: int,
    max_turns: int,
    eval_model_version_id: int | None = None,
) -> list[int]:
    session_id = f"sim-{run_token}-{index}-{persona['id']}"
    transcript: list[dict] = []
    turn_ids: list[int] = []

    # Agent opens the call (empty customer_text → greeting).
    resp = _agent_turn(session_id, "", eval_model_version_id)
    transcript.append({"role": "agent", "text": resp.get("agent_response", "")})
    if resp.get("turn_id"):
        turn_ids.append(resp["turn_id"])

    for _ in range(max_turns):
        cust = customer_sim.next_customer_message(persona, transcript)
        if not cust["text"]:
            break
        transcript.append({"role": "customer", "text": cust["text"]})
        resp = _agent_turn(session_id, cust["text"], eval_model_version_id)
        transcript.append({"role": "agent", "text": resp.get("agent_response", "")})
        if resp.get("turn_id"):
            turn_ids.append(resp["turn_id"])
        if cust["done"]:
            break
    return turn_ids


def run_simulation(eval_run_id: int, count: int, max_turns: int) -> None:
    db = SessionLocal()
    run = None
    try:
        run = db.query(EvalRun).filter(EvalRun.id == eval_run_id).first()
        if run is None:
            logger.error("simulation: EvalRun id=%d not found", eval_run_id)
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.progress_current = 0
        run.progress_total = count
        db.commit()

        run_token = uuid.uuid4().hex[:8]
        active = model_runtime.active_model(db)
        active_id = active.id if active else None
        # Simulate the model the EvalRun targets. When it is not the active
        # production model, route /agent-turn to it via the eval header.
        model_version_id = run.model_version_id or active_id
        eval_model_version_id = (
            model_version_id if model_version_id != active_id else None
        )
        personas = customer_sim.get_personas(count)

        overalls: list[float] = []
        per_persona: dict[str, float | None] = {}
        for i, persona in enumerate(personas, 1):
            try:
                turn_ids = _run_one_conversation(
                    run_token, persona, i, max_turns, eval_model_version_id
                )
            except Exception:
                logger.exception("simulation conversation failed — persona=%s", persona["id"])
                turn_ids = []

            persona_overalls: list[float] = []
            for tid in turn_ids:
                turn = db.query(Turn).filter(Turn.id == tid).first()
                if turn is None or not (turn.agent_response or "").strip():
                    continue
                verdict = judge.score(
                    turn.customer_text,
                    turn.state_before_json,
                    turn.agent_response,
                    turn.final_policy_json or turn.repaired_model_json or {},
                )
                db.add(TurnEvaluation(
                    eval_run_id=eval_run_id,
                    turn_id=tid,
                    source="simulation",
                    scenario_id=persona["id"],
                    model_version_id=model_version_id,
                    judge_model="production-base",
                    scores_json=verdict.get("scores"),
                    overall=verdict.get("overall"),
                    suggestion=verdict.get("suggestion"),
                    rationale=verdict.get("rationale"),
                    passed=verdict.get("passed"),
                    status="pending",
                    raw_judge_json=verdict,
                ))
                if verdict.get("overall") is not None:
                    overalls.append(float(verdict["overall"]))
                    persona_overalls.append(float(verdict["overall"]))
            per_persona[persona["id"]] = (
                round(sum(persona_overalls) / len(persona_overalls), 4) if persona_overalls else None
            )
            run.progress_current = i
            db.commit()

        judged = len(overalls)
        aggregate = {
            "count": count,
            "judged": judged,
            "mean_overall": round(sum(overalls) / judged, 4) if judged else None,
            "pass_rate": (
                round(sum(1 for o in overalls if o >= settings.judge_pass_threshold) / judged, 4)
                if judged else None
            ),
            "pass_threshold": settings.judge_pass_threshold,
            "per_persona": per_persona,
            "max_turns": max_turns,
        }
        metrics = dict(run.metrics_json or {})
        metrics["simulation"] = aggregate
        run.metrics_json = metrics
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("simulation completed — run=%d agg=%s", eval_run_id, aggregate)
    except Exception:
        logger.exception("run_simulation crashed — run=%d", eval_run_id)
        if run is not None:
            try:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
            except Exception:
                db.rollback()
    finally:
        db.close()


def start_simulation(eval_run_id: int, count: int, max_turns: int) -> None:
    threading.Thread(
        target=run_simulation,
        args=(eval_run_id, count, max_turns),
        name=f"simulation-{eval_run_id}",
        daemon=True,
    ).start()
