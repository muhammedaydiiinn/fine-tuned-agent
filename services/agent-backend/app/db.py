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
