"""Minimal ORM mirrors for the eval worker."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_session_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    customer_text: Mapped[str] = mapped_column(Text)
    agent_response: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64))
    emotion: Mapped[str | None] = mapped_column(String(32))
    risk: Mapped[str | None] = mapped_column(String(32))
    next_action: Mapped[str | None] = mapped_column(String(64))
    allowed_to_continue: Mapped[bool | None] = mapped_column(Boolean)
    state_before_json: Mapped[dict | None] = mapped_column(JSONB)
    final_policy_json: Mapped[dict | None] = mapped_column(JSONB)
    repaired_model_json: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TurnEvaluation(Base):
    __tablename__ = "turn_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eval_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    turn_id: Mapped[int | None] = mapped_column(Integer, index=True)
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    model_version_id: Mapped[int | None] = mapped_column(Integer)
    judge_model: Mapped[str | None] = mapped_column(String(128))
    scores_json: Mapped[dict | None] = mapped_column(JSONB)
    overall: Mapped[float | None] = mapped_column(Float)
    suggestion: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    accepted_candidate_id: Mapped[int | None] = mapped_column(Integer)
    raw_judge_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingCandidate(Base):
    __tablename__ = "training_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    training_job_id: Mapped[int | None] = mapped_column(Integer)
    model_version_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    model_version_id: Mapped[int | None] = mapped_column(Integer)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    run_kind: Mapped[str] = mapped_column(String(32), default="scenario_gate")
    metrics_json: Mapped[dict | None] = mapped_column(JSONB)
    results_path: Mapped[str | None] = mapped_column(String(256))
    logs_path: Mapped[str | None] = mapped_column(String(256))
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    base_model: Mapped[str | None] = mapped_column(String(128))
    lora_path: Mapped[str | None] = mapped_column(String(256))
    merged_path: Mapped[str | None] = mapped_column(String(256))
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    eval_status: Mapped[str] = mapped_column(String(32), default="pending")
    deployment_status: Mapped[str] = mapped_column(String(32), default="inactive")
    parent_model_version_id: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyContent(Base):
    """Editable sales-policy content — mirror of agent-backend policy_content.

    Read here so template-adherence metrics score against the same canned
    answers the live agent is served, not a stale hardcoded copy.
    """

    __tablename__ = "policy_content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
