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
    training_job_columns = (
        "ADD COLUMN IF NOT EXISTS logs_path VARCHAR(256)",
        "ADD COLUMN IF NOT EXISTS progress_current INTEGER DEFAULT 0 NOT NULL",
        "ADD COLUMN IF NOT EXISTS progress_total INTEGER DEFAULT 0 NOT NULL",
        "ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
        "ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP WITH TIME ZONE",
    )
    turn_columns = (
        "ADD COLUMN IF NOT EXISTS final_policy_json JSONB",
    )
    with engine.begin() as connection:
        for clause in eval_run_columns:
            connection.execute(text(f"ALTER TABLE eval_runs {clause}"))
        for clause in training_job_columns:
            connection.execute(text(f"ALTER TABLE training_jobs {clause}"))
        for clause in turn_columns:
            connection.execute(text(f"ALTER TABLE turns {clause}"))
        for clause in (
            "ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}'::jsonb NOT NULL",
            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        ):
            connection.execute(text(f"ALTER TABLE deployments {clause}"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_deployments_one_active_per_env "
            "ON deployments (environment) WHERE status = 'active'"
        ))
        # Faz M12: generational pipeline links
        for clause in (
            "ADD COLUMN IF NOT EXISTS training_job_id INTEGER",
            "ADD COLUMN IF NOT EXISTS model_version_id INTEGER",
        ):
            connection.execute(text(f"ALTER TABLE training_candidates {clause}"))
        connection.execute(text(
            "ALTER TABLE training_jobs ADD COLUMN IF NOT EXISTS model_version_id INTEGER"
        ))
        connection.execute(text(
            "ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS parent_model_version_id INTEGER"
        ))
        # Indexes for the new generational pipeline columns (idempotent)
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_training_candidates_training_job_id "
            "ON training_candidates (training_job_id)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_training_candidates_model_version_id "
            "ON training_candidates (model_version_id)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_model_versions_parent_model_version_id "
            "ON model_versions (parent_model_version_id)"
        ))
    _bootstrap_active_model()


def _bootstrap_active_model() -> None:
    """Register and publish the configured initial production model when available."""
    from datetime import datetime, timezone
    from pathlib import Path

    from app.core import model_runtime
    from app.models import Deployment, ModelVersion

    if not Path(settings.model_merged_path).is_dir():
        return
    # Cheap check only — full hashing (inspect_artifact) over ~19 GB must not block startup.
    artifact = model_runtime.artifact_is_valid(settings.model_merged_path)
    if not artifact["valid"]:
        if settings.vllm_mode == "real":
            raise RuntimeError(f"Configured bootstrap model is invalid: {artifact['error']}")
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
            if settings.vllm_mode == "real":
                # The actual promote + vLLM readiness wait runs in a background
                # thread (started from the lifespan); see serving_orchestrator. Here
                # we only keep the registry metadata current so the panel can show
                # the active model immediately.
                model = active.model_version
                metadata = dict(model.metadata_json or {})
                metadata["production_serving"] = model_runtime.production_serving_target()
                model.metadata_json = metadata
                db.commit()
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
                    "artifact_manifest": artifact,
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
                    "production_serving": model_runtime.production_serving_target(),
                },
            )
            db.add(model)
            db.flush()
        if settings.vllm_mode == "real":
            # Promote + readiness wait happen in the background (serving_orchestrator);
            # commit the deployment now so the active model is visible immediately.
            metadata = dict(model.metadata_json or {})
            metadata["artifact_manifest"] = artifact
            metadata["production_serving"] = model_runtime.production_serving_target()
            model.metadata_json = metadata
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
