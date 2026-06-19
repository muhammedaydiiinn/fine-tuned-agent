"""Build a JSONL training dataset from multiple sources.

Sources (merged in order):
  1. Approved TrainingCandidate rows from the database (required)
  2. data_dir/golden/*.jsonl — stable golden examples (optional)
  3. data_dir/base/*.jsonl   — balanced base dataset (optional)

If golden/base directories are absent or empty they are silently skipped.
"""
import json
import logging
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from models import TrainingCandidate

logger = logging.getLogger(__name__)

def _validate_messages(messages: list, source: str) -> None:
    if not isinstance(messages, list) or len(messages) < 3:
        raise ValueError(f"{source}: messages must contain system, user and assistant entries")
    assistant = messages[-1]
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        raise ValueError(f"{source}: final message must be assistant")
    try:
        policy = json.loads(assistant.get("content") or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{source}: assistant content must be valid JSON") from exc
    response = str(policy.get("agent_response") or "")
    intent = str(policy.get("intent") or "")
    response_folded = response.casefold()

    if "euro" in response_folded:
        compact = response.replace(".", "").replace(",", ".")
        import re
        stated_amounts = re.findall(r"\b\d+(?:\.\d+)?(?=\s*Euro)", compact, re.IGNORECASE)
        allowed = {"29.99", "2500"}
        invalid = [amount for amount in stated_amounts if amount not in allowed]
        if invalid:
            raise ValueError(
                f"{source}: unsupported Euro amount(s) in training response: {invalid}"
            )
    if intent in {"price_question", "free_question"}:
        if "14 tage kostenlos" not in response_folded or "29,99" not in response:
            raise ValueError(
                f"{source}: price/trial response must contain the approved 14-day and 29,99 terms"
            )
    if "50% rabatt" in response_folded or "50 % rabatt" in response_folded:
        raise ValueError(f"{source}: unapproved discount claim")
    if "unbegrenzt" in response_folded and "nummer" in response_folded:
        raise ValueError(f"{source}: unsupported unlimited-number claim")


def _write_jsonl_files(fh, directory: Path) -> tuple[int, list[dict]]:
    """Append JSONL files and return row count plus source manifests."""
    if not directory.exists():
        return 0, []
    count = 0
    manifests: list[dict] = []
    for f in sorted(directory.glob("*.jsonl")):
        file_count = 0
        digest = hashlib.sha256()
        with open(f, encoding="utf-8") as src:
            for line_number, line in enumerate(src, start=1):
                line = line.strip()
                if line:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{f}:{line_number}: invalid JSON") from exc
                    _validate_messages(
                        payload.get("messages"),
                        f"{f}:{line_number}",
                    )
                    fh.write(line + "\n")
                    count += 1
                    file_count += 1
                    digest.update((line + "\n").encode())
        manifests.append({
            "path": str(f),
            "rows": file_count,
            "sha256": digest.hexdigest(),
        })
    return count, manifests


def build(
    db: Session,
    output_path: str,
    dataset_version: str,
    data_dir: str = "/data",
    candidate_ids: list[int] | None = None,
) -> dict:
    """Write merged dataset to a JSONL file and return source stats.

    Each line: {"messages": [{role, content}, ...]}
    """
    # 1. Approved training candidates
    query = db.query(TrainingCandidate).filter(
        TrainingCandidate.approved == True  # noqa: E712
    )
    if candidate_ids:
        query = query.filter(TrainingCandidate.id.in_(candidate_ids))
    candidates = query.order_by(TrainingCandidate.created_at.asc()).all()

    if not candidates:
        raise ValueError("No approved training candidates found")
    if candidate_ids and len(candidates) != len(set(candidate_ids)):
        raise ValueError("One or more requested training candidates are unavailable")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    candidate_count = 0
    golden_count = 0
    base_count = 0
    golden_sources: list[dict] = []
    base_sources: list[dict] = []

    with open(out, "w", encoding="utf-8") as fh:
        # Source 1: candidates
        for c in candidates:
            _validate_messages(c.messages_json, f"training_candidate:{c.id}")
            fh.write(json.dumps({"messages": c.messages_json}, ensure_ascii=False) + "\n")
            candidate_count += 1

        # Source 2: golden examples
        golden_dir = Path(data_dir) / "golden"
        golden_count, golden_sources = _write_jsonl_files(fh, golden_dir)

        # Source 3: balanced base dataset
        base_dir = Path(data_dir) / "base"
        base_count, base_sources = _write_jsonl_files(fh, base_dir)

    total = candidate_count + golden_count + base_count
    logger.info(
        "Dataset built: candidates=%d golden=%d base=%d total=%d → %s",
        candidate_count, golden_count, base_count, total, output_path,
    )

    output_digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "dataset_version": dataset_version,
        "output_path": output_path,
        "dataset_sha256": output_digest,
        "row_count": total,
        "candidates": candidate_count,
        "candidate_ids": [candidate.id for candidate in candidates],
        "golden": golden_count,
        "golden_sources": golden_sources,
        "base": base_count,
        "base_sources": base_sources,
    }
