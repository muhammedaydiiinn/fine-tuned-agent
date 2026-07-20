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
    response_repair,
    latency as latency_mod,
    model_runtime,
    product_facts,
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

    # 6. vLLM call — constrain policy output to the canonical enums so model
    # output, guardrails and the deploy-gate scenarios share one taxonomy.
    response_format = None
    if settings.policy_guided_decoding and runtime_target.get("mode") == "real":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "policy",
                "schema": product_facts.policy_response_schema(),
                "strict": True,
            },
        }
    llm_start = time.perf_counter()
    try:
        raw_output = vllm_client.chat(messages, runtime_target, response_format=response_format)
    except Exception as exc:
        llm_ms = (time.perf_counter() - llm_start) * 1000
        logger.exception(
            "vLLM call failed — session=%s llm=%.0fms",
            req.session_id,
            llm_ms,
        )
        raise HTTPException(
            status_code=503,
            detail="LLM upstream unavailable",
        ) from exc
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

    # 10. Apply guardrails (PDF templates + hard limits)
    safe_policy = guardrails.apply_with_context(
        after_correction,
        state,
        req.customer_text,
    )

    # 10b. Hard response repairs (SMS code, vague price, premature link, forbidden data)
    repair_state = {**state, "filled_slots": state_manager.derive_filled_slots(state, safe_policy)}
    repaired_text, repairs = response_repair.repair_all(
        safe_policy.get("agent_response", ""),
        repair_state,
        req.customer_text,
    )
    if repairs:
        safe_policy = dict(safe_policy)
        safe_policy["agent_response"] = repaired_text
        logger.info("response_repair applied — session=%s rules=%s", req.session_id, repairs)

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
        final_policy_json=safe_policy,
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
        turn_id=turn.id,
        turn_index=turn.turn_index,
        session_id=req.session_id,
        customer_text=req.customer_text,
        agent_response=safe_policy["agent_response"],
        voice_style=safe_policy.get("voice_style", {}),
        policy=PolicySummary(
            intent=safe_policy.get("intent", "unknown"),
            next_action=safe_policy.get("next_action", ""),
            risk=safe_policy.get("risk", "low"),
            allowed_to_continue=safe_policy.get("allowed_to_continue", True),
        ),
        state=new_state,
        latency=LatencyInfo(llm_ms=llm_ms, backend_ms=backend_ms, total_ms=total_ms),
    )
