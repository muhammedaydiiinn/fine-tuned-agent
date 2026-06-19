"""Latency measurement and persistence."""
import time
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)


@contextmanager
def measure(label: str = "") -> Generator[dict, None, None]:
    """Context manager that yields a dict with an elapsed_ms key."""
    result: dict = {"elapsed_ms": 0.0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["elapsed_ms"] = (time.perf_counter() - start) * 1000
        if label:
            logger.debug("Latency [%s]: %.1f ms", label, result["elapsed_ms"])


def save_metrics(
    db: DBSession,
    session_id: int,
    turn_id: int | None,
    llm_ms: float,
    backend_ms: float,
    total_ms: float,
) -> None:
    """Write three latency_metrics rows to the database."""
    from app.models import LatencyMetric

    metrics = [
        LatencyMetric(session_id=session_id, turn_id=turn_id, metric_name="llm_ms",     value_ms=llm_ms),
        LatencyMetric(session_id=session_id, turn_id=turn_id, metric_name="backend_ms",  value_ms=backend_ms),
        LatencyMetric(session_id=session_id, turn_id=turn_id, metric_name="total_ms",    value_ms=total_ms),
    ]
    db.add_all(metrics)
    db.commit()
