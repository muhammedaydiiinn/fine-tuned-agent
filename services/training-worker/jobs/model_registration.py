"""Model registry commit helpers for reversible candidate publication."""
from __future__ import annotations

from typing import Any

from jobs.artifacts import DirectoryPublication


def commit_model_version(
    db: Any,
    model: Any,
    publication: DirectoryPublication | None,
) -> int:
    """Commit model metadata and finalize or revert its serving publication."""
    db.add(model)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if publication is not None:
            publication.rollback()
        raise

    if publication is not None:
        publication.finalize()
    db.refresh(model)
    return model.id
