"""POST /agent-turn — 12-step stateful agent flow."""
import logging
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db
from app.models import Session as SessionModel, Turn, LatencyMetric
from app.schemas import AgentTurnRequest, AgentTurnResponse, LatencyInfo, PolicySummary
from app.core import (
    state_manager,
    correction_memory,
    prompt_builder,
    vllm_client,
    json_repair,
    guardrails,
    latency as latency_mod,
    model_runtime,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/agent-turn", response_model=AgentTurnResponse)
def agent_turn(
    req: AgentTurnRequest,
    db: DBSession = Depends(get_db),
    eval_model_version_id: int | None = Header(
        default=None,
        alias="X-Eval-Model-Version-ID",
    ),
    eval_token: str = Header(default="", alias="X-Eval-Token"),
):
    backend_start = time.perf_counter()

    if eval_model_version_id is not None:
        if settings.eval_internal_token:
            if not secrets.compare_digest(eval_token, settings.eval_internal_token):
                raise HTTPException(status_code=403, detail="Invalid evaluation token")
        elif settings.environment == "production":
            raise HTTPException(
                status_code=503,
                detail="Isolated evaluation routing is not configured",
            )

    # 1. Load or create session
    session = db.query(SessionModel).filter(
        SessionModel.external_session_id == req.session_id
    ).first()
    if not session:
        session = SessionModel(
            external_session_id=req.session_id,
            status="active",
            state_json={},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        logger.info("agent-turn: session auto-created — %s", req.session_id)

    # 2. Load state
    state = state_manager.load(session)

    # 3. Correction memory check
    correction_hints = correction_memory.get_hints(db, req.customer_text, state)

    # 4. Build prompt
    recent_turns = (
        db.query(Turn)
        .filter(Turn.session_id == session.id)
        .order_by(Turn.turn_index.desc())
        .limit(5)
        .all()[::-1]
    )
    messages = prompt_builder.build(
        customer_text=req.customer_text,
        state=state,
        recent_turns=recent_turns,
        correction_hints=correction_hints,
    )

    # 5. Resolve the production deployment, or an explicitly isolated eval model.
    try:
        model_version, runtime_target = model_runtime.resolve_for_turn(
            db,
            requested_model_version_id=eval_model_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # 6. vLLM call
    llm_start = time.perf_counter()
    try:
        raw_output = vllm_client.chat(messages, runtime_target)
    except Exception as exc:
        logger.error("vLLM call failed: %s", exc)
        raw_output = ""
    llm_ms = (time.perf_counter() - llm_start) * 1000

    # 7. Extract JSON
    raw_policy = json_repair.extract_json(raw_output)

    # 8. Repair
    repaired_policy = json_repair.repair(raw_policy)

    # 9. Correction memory override. Policy-intent matching is evaluated after
    # repair so intent-keyed hotfixes work for natural-language customer input.
    policy_hints = correction_memory.get_policy_hints(db, repaired_policy)
    after_correction = correction_memory.apply_override(
        repaired_policy,
        policy_hints or correction_hints,
    )

    # 10. Apply guardrails (including product fact templates)
    safe_policy = guardrails.apply(after_correction, state)

    # 11. Update state
    new_state = state_manager.update(state, safe_policy, customer_text=req.customer_text)
    state_manager.persist(db, session, new_state)

    # 12. Save turn and latency
    total_ms = (time.perf_counter() - backend_start) * 1000
    backend_ms = total_ms - llm_ms

    turn_index = int(state.get("turn_count", 0))
    turn = Turn(
        session_id=session.id,
        turn_index=turn_index,
        customer_text=req.customer_text,
        agent_response=safe_policy.get("agent_response"),
        intent=safe_policy.get("intent"),
        emotion=safe_policy.get("emotion"),
        risk=safe_policy.get("risk"),
        next_action=safe_policy.get("next_action"),
        allowed_to_continue=safe_policy.get("allowed_to_continue"),
        state_before_json=state,
        state_after_json=new_state,
        raw_model_json=raw_policy,
        repaired_model_json=repaired_policy,
        latency_json={"llm_ms": llm_ms, "backend_ms": backend_ms, "total_ms": total_ms},
        model_version=(
            model_version.version_name
            if model_version is not None
            else settings.model_active_version
        ),
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)

    latency_mod.save_metrics(db, session.id, turn.id, llm_ms, backend_ms, total_ms)

    logger.info(
        "agent-turn complete — session=%s turn=%d intent=%s llm=%.0fms total=%.0fms",
        req.session_id, turn_index, safe_policy.get("intent"), llm_ms, total_ms,
    )

    # 13. Return response
    return AgentTurnResponse(
        session_id=req.session_id,
        customer_text=req.customer_text,
        agent_response=safe_policy["agent_response"],
        policy=PolicySummary(
            intent=safe_policy.get("intent", "unknown"),
            next_action=safe_policy.get("next_action", ""),
            risk=safe_policy.get("risk", "low"),
            allowed_to_continue=safe_policy.get("allowed_to_continue", True),
        ),
        state=new_state,
        latency=LatencyInfo(llm_ms=llm_ms, backend_ms=backend_ms, total_ms=total_ms),
    )
