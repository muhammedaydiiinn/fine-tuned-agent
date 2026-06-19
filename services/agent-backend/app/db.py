from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: veritabanı oturumu sağlar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create tables and apply the small idempotent schema upgrades."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    eval_run_columns = (
        "ADD COLUMN IF NOT EXISTS logs_path VARCHAR(256)",
        "ADD COLUMN IF NOT EXISTS progress_current INTEGER DEFAULT 0 NOT NULL",
        "ADD COLUMN IF NOT EXISTS progress_total INTEGER DEFAULT 0 NOT NULL",
        "ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
    )
    with engine.begin() as connection:
        for clause in eval_run_columns:
            connection.execute(text(f"ALTER TABLE eval_runs {clause}"))
        for clause in (
            "ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}'::jsonb NOT NULL",
            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        ):
            connection.execute(text(f"ALTER TABLE deployments {clause}"))
    _bootstrap_active_model()


def _bootstrap_active_model() -> None:
    """Register the configured initial production model when its artifact exists."""
    from datetime import datetime, timezone
    from pathlib import Path

    from app.models import Deployment, ModelVersion

    if not Path(settings.model_merged_path).is_dir():
        return
    db = SessionLocal()
    try:
        active = (
            db.query(Deployment)
            .filter(
                Deployment.environment == "production",
                Deployment.status == "active",
            )
            .first()
        )
        if active:
            return
        model = (
            db.query(ModelVersion)
            .filter(ModelVersion.version_name == settings.model_active_version)
            .first()
        )
        if not model:
            model = ModelVersion(
                version_name=settings.model_active_version,
                base_model=settings.model_active_version,
                merged_path=settings.model_merged_path,
                eval_status="passed",
                deployment_status="active_production",
                metadata_json={
                    "lifecycle_status": "deployed",
                    "bootstrap": True,
                    "serving": {
                        "mode": settings.vllm_mode,
                        "base_url": (
                            settings.vllm_base_url
                            if settings.vllm_mode == "real"
                            else ""
                        ),
                        "model_name": settings.vllm_model_name,
                        "slot": "blue" if settings.vllm_mode == "real" else "mock",
                    },
                },
            )
            db.add(model)
            db.flush()
        deployment = Deployment(
            model_version_id=model.id,
            environment="production",
            status="active",
            deployed_at=datetime.now(timezone.utc),
            metadata_json={"action": "bootstrap"},
        )
        db.add(deployment)
        db.commit()
    finally:
        db.close()
