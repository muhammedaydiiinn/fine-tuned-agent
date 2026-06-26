"""Pure helpers for training job naming and Redis recovery."""
from __future__ import annotations

import re
from typing import Any

from models import ModelVersion

QUEUE_NAME = "agent:training_jobs"
PROCESSING_QUEUE_NAME = f"{QUEUE_NAME}:processing"


def is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed"}


def next_version_name(db: Any, parent_name: str, job_id: int) -> str:
    """Return a deterministic, collision-free child version name."""
    match = re.search(r"^(.*?)[-_]v(\d+)(?:[-_].*)?$", parent_name)
    if match:
        preferred = f"{match.group(1)}-v{int(match.group(2)) + 1}"
    else:
        preferred = f"{parent_name}-ft"

    existing = (
        db.query(ModelVersion.id)
        .filter(ModelVersion.version_name == preferred)
        .first()
    )
    return f"{preferred}-job{job_id}" if existing else preferred


def requeue_interrupted_jobs(client: Any) -> int:
    """Move unacknowledged jobs back to the main queue after worker restart."""
    recovered = 0
    while True:
        item = client.rpoplpush(PROCESSING_QUEUE_NAME, QUEUE_NAME)
        if item is None:
            return recovered
        recovered += 1
