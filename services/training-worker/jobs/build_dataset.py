"""Build a JSONL training dataset from multiple sources.

Sources (merged in order):
  1. Approved TrainingCandidate rows from the database (required)
  2. data_dir/golden/*.jsonl — stable golden examples (optional)
  3. data_dir/base/*.jsonl   — balanced base dataset (optional)

If golden/base directories are absent or empty they are silently skipped.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from models import TrainingCandidate

logger = logging.getLogger(__name__)


def _write_jsonl_files(fh, directory: Path) -> int:
    """Append all JSONL lines from *.jsonl files in directory. Returns row count."""
    if not directory.exists():
        return 0
    count = 0
    for f in sorted(directory.glob("*.jsonl")):
        with open(f, encoding="utf-8") as src:
            for line in src:
                line = line.strip()
                if line:
                    fh.write(line + "\n")
                    count += 1
    return count


def build(db: Session, output_path: str, dataset_version: str, data_dir: str = "/data") -> dict:
    """Write merged dataset to a JSONL file and return source stats.

    Each line: {"messages": [{role, content}, ...]}
    """
    # 1. Approved training candidates
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

    candidate_count = 0
    golden_count = 0
    base_count = 0

    with open(out, "w", encoding="utf-8") as fh:
        # Source 1: candidates
        for c in candidates:
            fh.write(json.dumps({"messages": c.messages_json}, ensure_ascii=False) + "\n")
            candidate_count += 1

        # Source 2: golden examples
        golden_dir = Path(data_dir) / "golden"
        golden_count = _write_jsonl_files(fh, golden_dir)

        # Source 3: balanced base dataset
        base_dir = Path(data_dir) / "base"
        base_count = _write_jsonl_files(fh, base_dir)

    total = candidate_count + golden_count + base_count
    logger.info(
        "Dataset built: candidates=%d golden=%d base=%d total=%d → %s",
        candidate_count, golden_count, base_count, total, output_path,
    )

    return {
        "dataset_version": dataset_version,
        "output_path": output_path,
        "row_count": total,
        "candidates": candidate_count,
        "golden": golden_count,
        "base": base_count,
    }
