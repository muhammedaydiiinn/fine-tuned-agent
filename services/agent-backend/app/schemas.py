"""Pydantic request/response şemaları — dokümandaki JSON sözleşmesine birebir uyar."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Policy (model çıktısı) ──────────────────────────────────────────────────

class VoiceStyle(BaseModel):
    tone: str = "clear"
    pace: str = "normal"
    confidence: str = "high"


class PolicyOutput(BaseModel):
    intent: str = "unknown"
    emotion: str = "neutral"
    risk: str = "low"
    next_action: str = "greet"
    behavior_strategy: str = "standard"
    allowed_to_continue: bool = True
    agent_response: str = ""
    voice_style: VoiceStyle = Field(default_factory=VoiceStyle)


# ── Agent turn ──────────────────────────────────────────────────────────────

class AgentTurnRequest(BaseModel):
    session_id: str = Field(..., description="External session ID")
    customer_text: str = Field(..., min_length=1)


class LatencyInfo(BaseModel):
    llm_ms: float
    backend_ms: float
    total_ms: float


class PolicySummary(BaseModel):
    """Agent yanıtındaki özet policy bilgisi (agent_response hariç)."""
    intent: str
    next_action: str
    risk: str
    allowed_to_continue: bool


class AgentTurnResponse(BaseModel):
    session_id: str
    customer_text: str
    agent_response: str
    policy: PolicySummary
    state: dict[str, Any]
    latency: LatencyInfo


# ── Session ─────────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    external_session_id: str | None = None


class SessionResponse(BaseModel):
    id: int
    external_session_id: str | None
    status: str
    current_stage: str | None
    current_goal: str | None
    state_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Turn ─────────────────────────────────────────────────────────────────────

class TurnResponse(BaseModel):
    id: int
    session_id: int
    turn_index: int
    customer_text: str
    agent_response: str | None
    intent: str | None
    emotion: str | None
    risk: str | None
    next_action: str | None
    allowed_to_continue: bool | None
    latency_json: dict[str, Any] | None
    model_version: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Correction ───────────────────────────────────────────────────────────────

class CreateCorrectionRequest(BaseModel):
    session_id: int | None = None
    turn_id: int | None = None
    correction_type: str = "response_correction"
    old_agent_response: str | None = None
    corrected_agent_response: str | None = None
    old_next_action: str | None = None
    corrected_next_action: str | None = None
    notes: str | None = None
    apply_immediately: bool = False
    send_to_training: bool = False


class CorrectionResponse(BaseModel):
    id: int
    correction_type: str
    apply_immediately: bool
    send_to_training: bool
    approved: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Training Candidate ───────────────────────────────────────────────────────

class TrainingCandidateResponse(BaseModel):
    id: int
    source_type: str
    source_id: int | None
    messages_json: list[Any]
    metadata_json: dict[str, Any]
    approved: bool
    exported: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ExportResult(BaseModel):
    file_path: str
    count: int
    exported_ids: list[int]


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db: bool
    redis: bool
    vllm_mode: str
    version: str = "1.0.0"
