"""Pure Redis queue recovery helpers for eval jobs."""
from __future__ import annotations

from typing import Any

QUEUE_NAME = "agent:eval_jobs"
PROCESSING_QUEUE_NAME = f"{QUEUE_NAME}:processing"


def is_terminal_status(status: str) -> bool:
    # "blocked" = infrastructure not ready (e.g. candidate model not served);
    # it is final for this run — the operator re-triggers a fresh eval once the
    # candidate is served, rather than this one being auto-reprocessed.
    return status in {"completed", "failed", "blocked"}


def requeue_interrupted_jobs(client: Any) -> int:
    """Move unacknowledged eval jobs back to the main queue after restart."""
    recovered = 0
    while True:
        item = client.rpoplpush(PROCESSING_QUEUE_NAME, QUEUE_NAME)
        if item is None:
            return recovered
        recovered += 1
