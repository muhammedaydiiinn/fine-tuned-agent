"""Minimal ORM mirrors for the eval worker."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
