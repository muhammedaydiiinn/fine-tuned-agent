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
    turn_id: int
    turn_index: int
    session_id: str
    customer_text: str
    agent_response: str
    voice_style: VoiceStyle
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
    final_policy_json: dict[str, Any] | None
    latency_json: dict[str, Any] | None
    model_version: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VoiceTurnMetricsRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    stt_ms: float = Field(..., ge=0)
    backend_ms: float = Field(..., ge=0)
    llm_ms: float = Field(..., ge=0)
    tts_first_audio_ms: float = Field(..., ge=0)
    speech_end_to_first_audio_ms: float | None = Field(default=None, ge=0)
    total_voice_turn_ms: float = Field(..., ge=0)
    transcript_final: str = Field(..., min_length=1)
    heard_response: str = Field(..., min_length=1)


class VoiceTurnMetricsResponse(BaseModel):
    turn_id: int
    session_id: str
    latency: dict[str, float]


class VoiceEventRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    event_id: str = Field(..., min_length=1, max_length=160)
    sequence: int = Field(..., ge=0)
    event_type: str = Field(..., min_length=1, max_length=64)
    turn_id: int | None = Field(default=None, gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class VoiceEventResponse(BaseModel):
    id: int
    event_id: str
    created: bool


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


# ── Training Job ─────────────────────────────────────────────────────────────

class CreateTrainingJobRequest(BaseModel):
    dataset_version: str | None = None
    lora_rank: int | None = None
    lora_alpha: int | None = None
    epochs: int | None = None
    lr: float | None = None
    batch_size: int | None = None
    session_id: int | None = Field(default=None, gt=0)
    candidate_ids: list[int] = Field(default_factory=list)


class TrainingJobResponse(BaseModel):
    id: int
    job_type: str
    status: str
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    logs_path: str | None
    progress_current: int
    progress_total: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


# ── Eval Run ─────────────────────────────────────────────────────────────────

class CreateEvalRunRequest(BaseModel):
    model_version_id: int = Field(..., gt=0)


class EvalRunResponse(BaseModel):
    id: int
    model_version_id: int
    status: str
    metrics_json: dict[str, Any] | None
    results_path: str | None
    logs_path: str | None
    progress_current: int
    progress_total: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


# ── Model Registry / Deployment ─────────────────────────────────────────────

class RegisterModelRequest(BaseModel):
    version_name: str = Field(..., min_length=1, max_length=128)
    base_model: str | None = None
    lora_path: str | None = None
    merged_path: str = Field(..., min_length=1)
    dataset_version: str | None = None


class ConfigureServingRequest(BaseModel):
    mode: str = Field(default="real", pattern="^(mock|real)$")
    base_url: str = ""
    model_name: str = Field(..., min_length=1)
    slot: str = Field(default="green", pattern="^(blue|green|mock)$")


class DeploymentRequest(BaseModel):
    environment: str = Field(default="production", pattern="^(staging|production)$")
    actor: str | None = None


class RollbackRequest(BaseModel):
    actor: str | None = None


class ModelVersionResponse(BaseModel):
    id: int
    version_name: str
    base_model: str | None
    lora_path: str | None
    merged_path: str | None
    dataset_version: str | None
    eval_status: str
    deployment_status: str
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class DeploymentResponse(BaseModel):
    id: int
    model_version_id: int
    environment: str
    status: str
    deployed_at: datetime | None
    rollback_model_version_id: int | None
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db: bool
    redis: bool
    vllm_mode: str
    version: str = "1.0.0"
