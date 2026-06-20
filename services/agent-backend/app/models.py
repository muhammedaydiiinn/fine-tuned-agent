"""SQLAlchemy ORM models — all 10 tables."""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_session_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    current_goal: Mapped[str | None] = mapped_column(String(128))
    state_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    turns: Mapped[list["Turn"]] = relationship("Turn", back_populates="session")
    corrections: Mapped[list["Correction"]] = relationship("Correction", back_populates="session")
    latency_metrics: Mapped[list["LatencyMetric"]] = relationship("LatencyMetric", back_populates="session")
    review: Mapped["SessionReview | None"] = relationship(
        "SessionReview",
        back_populates="session",
        uselist=False,
    )


class SessionReview(Base):
    __tablename__ = "session_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id"),
        unique=True,
        index=True,
    )
    rating: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    candidate_ids_json: Mapped[list] = mapped_column(JSONB, default=list)
    training_job_id: Mapped[int | None] = mapped_column(ForeignKey("training_jobs.id"))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    session: Mapped["Session"] = relationship("Session", back_populates="review")


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    customer_text: Mapped[str] = mapped_column(Text)
    agent_response: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64))
    emotion: Mapped[str | None] = mapped_column(String(32))
    risk: Mapped[str | None] = mapped_column(String(32))
    next_action: Mapped[str | None] = mapped_column(String(64))
    allowed_to_continue: Mapped[bool | None] = mapped_column(Boolean)
    state_before_json: Mapped[dict | None] = mapped_column(JSONB)
    state_after_json: Mapped[dict | None] = mapped_column(JSONB)
    raw_model_json: Mapped[dict | None] = mapped_column(JSONB)
    repaired_model_json: Mapped[dict | None] = mapped_column(JSONB)
    final_policy_json: Mapped[dict | None] = mapped_column(JSONB)
    latency_json: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session"] = relationship("Session", back_populates="turns")
    corrections: Mapped[list["Correction"]] = relationship("Correction", back_populates="turn")
    latency_metrics: Mapped[list["LatencyMetric"]] = relationship("LatencyMetric", back_populates="turn")


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[int | None] = mapped_column(ForeignKey("turns.id"), index=True)
    correction_type: Mapped[str] = mapped_column(String(64))  # response/policy/product_fact/style/safety
    old_agent_response: Mapped[str | None] = mapped_column(Text)
    corrected_agent_response: Mapped[str | None] = mapped_column(Text)
    old_next_action: Mapped[str | None] = mapped_column(String(64))
    corrected_next_action: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    apply_immediately: Mapped[bool] = mapped_column(Boolean, default=False)
    send_to_training: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session | None"] = relationship("Session", back_populates="corrections")
    turn: Mapped["Turn | None"] = relationship("Turn", back_populates="corrections")


class CorrectionMemory(Base):
    __tablename__ = "correction_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trigger_key: Mapped[str] = mapped_column(String(128), index=True)
    context_json: Mapped[dict | None] = mapped_column(JSONB)
    correct_response: Mapped[str | None] = mapped_column(Text)
    correct_next_action: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_correction_id: Mapped[int | None] = mapped_column(ForeignKey("corrections.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrainingCandidate(Base):
    __tablename__ = "training_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # correction / manual / session
    source_id: Mapped[int | None] = mapped_column(Integer)
    messages_json: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    exported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    input_json: Mapped[dict | None] = mapped_column(JSONB)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    logs_path: Mapped[str | None] = mapped_column(String(256))
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    base_model: Mapped[str | None] = mapped_column(String(128))
    lora_path: Mapped[str | None] = mapped_column(String(256))
    merged_path: Mapped[str | None] = mapped_column(String(256))
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    eval_status: Mapped[str] = mapped_column(String(32), default="pending")
    deployment_status: Mapped[str] = mapped_column(String(32), default="inactive")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    eval_runs: Mapped[list["EvalRun"]] = relationship("EvalRun", back_populates="model_version")
    deployments: Mapped[list["Deployment"]] = relationship(
        "Deployment",
        foreign_keys="[Deployment.model_version_id]",
        back_populates="model_version",
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    metrics_json: Mapped[dict | None] = mapped_column(JSONB)
    results_path: Mapped[str | None] = mapped_column(String(256))
    logs_path: Mapped[str | None] = mapped_column(String(256))
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model_version: Mapped["ModelVersion"] = relationship("ModelVersion", back_populates="eval_runs")


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), index=True)
    environment: Mapped[str] = mapped_column(String(32))  # staging / production
    status: Mapped[str] = mapped_column(String(32), default="pending")
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    model_version: Mapped["ModelVersion"] = relationship(
        "ModelVersion", foreign_keys=[model_version_id], back_populates="deployments"
    )


class LatencyMetric(Base):
    __tablename__ = "latency_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[int | None] = mapped_column(ForeignKey("turns.id"), index=True)
    metric_name: Mapped[str] = mapped_column(String(64))
    value_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session"] = relationship("Session", back_populates="latency_metrics")
    turn: Mapped["Turn | None"] = relationship("Turn", back_populates="latency_metrics")
