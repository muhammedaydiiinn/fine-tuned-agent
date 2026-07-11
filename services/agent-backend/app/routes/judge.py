"""Internal LLM-as-judge endpoint — called by the eval-worker (X-Eval-Token guarded)."""
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core import judge

router = APIRouter()
logger = logging.getLogger(__name__)


class JudgeScoreRequest(BaseModel):
    customer_text: str = ""
    state_before: dict = Field(default_factory=dict)
    agent_response: str = ""
    policy_json: dict = Field(default_factory=dict)


def _guard(token: str) -> None:
    """Mirror the agent-turn eval-token guard: require the internal token when set."""
    if settings.eval_internal_token:
        if not secrets.compare_digest(token, settings.eval_internal_token):
            raise HTTPException(status_code=403, detail="Invalid evaluation token")
    elif settings.environment == "production":
        raise HTTPException(status_code=503, detail="Judge routing is not configured")


@router.post("/judge/score")
def judge_score(
    req: JudgeScoreRequest,
    eval_token: str = Header(default="", alias="X-Eval-Token"),
) -> dict:
    _guard(eval_token)
    return judge.score(
        req.customer_text,
        req.state_before,
        req.agent_response,
        req.policy_json,
    )
