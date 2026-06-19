"""Build a JSONL training dataset from approved TrainingCandidate rows."""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from models import TrainingCandidate

logger = logging.getLogger(__name__)


def build(db: Session, output_path: str, dataset_version: str) -> dict:
    """Write approved candidates to a JSONL file and return stats.

    Format per line: {"messages": [{role, content}, ...]}
    Mirrors the agent-backend export_jsonl format exactly.
    """
    candidates = (
        db.query(TrainingCandidate)
        .filter(TrainingCandidate.approved == True)  # noqa: E712
        .order_by(TrainingCandidate.created_at.asc())
        .all()
    )

    if not candidates:
        raise ValueError("No approved training candidates found")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(out, "w", encoding="utf-8") as fh:
        for c in candidates:
            line = json.dumps({"messages": c.messages_json}, ensure_ascii=False)
            fh.write(line + "\n")
            count += 1

    logger.info("Dataset built: %s rows → %s", count, output_path)
    return {"dataset_version": dataset_version, "output_path": output_path, "row_count": count}
