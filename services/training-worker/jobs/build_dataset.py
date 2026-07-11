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

from models import TrainingCandidate, Turn

logger = logging.getLogger(__name__)

_RESOLVED = Path(__file__).resolve()
# Dev source tree: services/training-worker/jobs/build_dataset.py -> repo root is
# parents[3]. In the container the tree is flattened to /app/jobs/... where
# parents[3] does not exist, so fall back to the highest available parent. The
# container ships its policy files under parents[1]/policy (see _*_CANDIDATES).
_REPO_ROOT = _RESOLVED.parents[3] if len(_RESOLVED.parents) > 3 else _RESOLVED.parents[-1]
_POLICY_CANDIDATES = (
    _REPO_ROOT / "agent-backend" / "app" / "core" / "policy" / "system_instruction.txt",
    Path(__file__).resolve().parents[1] / "policy" / "system_instruction.txt",
)
_FACTS_CANDIDATES = (
    _REPO_ROOT / "agent-backend" / "app" / "core" / "policy" / "product_facts_prompt.txt",
    Path(__file__).resolve().parents[1] / "policy" / "product_facts_prompt.txt",
)

LEGACY_SYSTEM_INSTRUCTION = (
    "You are an CallShield Gold Paket sales policy agent. "
    "Return ONLY a valid JSON policy object."
)


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _canonical_system_content() -> str:
    instruction_path = _first_existing(_POLICY_CANDIDATES)
    facts_path = _first_existing(_FACTS_CANDIDATES)
    instruction = (
        instruction_path.read_text(encoding="utf-8").strip()
        if instruction_path
        else LEGACY_SYSTEM_INSTRUCTION
    )
    facts = facts_path.read_text(encoding="utf-8").strip() if facts_path else ""
    return instruction + ("\n\n" + facts if facts else "")

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


def _normalize_legacy_candidate(candidate: TrainingCandidate, db: Session) -> list:
    """Upgrade legacy plain-text assistant examples to JSON policy format."""
    messages = candidate.messages_json or []
    if not isinstance(messages, list) or len(messages) < 3:
        raise ValueError(f"training_candidate:{candidate.id}: messages must contain system, user and assistant entries")

    assistant = messages[-1]
    assistant_text = ""
    if isinstance(assistant, dict):
        assistant_text = str(assistant.get("content") or "")

    turn = None
    if candidate.source_id is not None:
        turn = db.query(Turn).filter(Turn.id == candidate.source_id).first()
    if turn is None:
        raise ValueError(
            f"training_candidate:{candidate.id}: assistant content must be valid JSON"
        )

    normalized = [
        {
            "role": "system",
            "content": _canonical_system_content(),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "customer_message": turn.customer_text or "",
                    "state": turn.state_before_json or {},
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "intent": turn.intent or "unknown",
                    "emotion": turn.emotion or "neutral",
                    "risk": turn.risk or "low",
                    "next_action": turn.next_action or "",
                    "behavior_strategy": "corrected",
                    "allowed_to_continue": (
                        turn.allowed_to_continue
                        if turn.allowed_to_continue is not None
                        else True
                    ),
                    "agent_response": assistant_text or turn.agent_response or "",
                    "voice_style": {
                        "tone": "clear",
                        "pace": "normal",
                        "confidence": "high",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    candidate.messages_json = normalized
    metadata = dict(candidate.metadata_json or {})
    metadata["normalized_from_legacy"] = True
    candidate.metadata_json = metadata
    logger.info(
        "Normalized legacy training candidate id=%d source_turn=%s",
        candidate.id,
        candidate.source_id,
    )
    return normalized


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


def _force_canonical_system(messages: list) -> list:
    """Ensure the FIRST message is the canonical training system prompt.

    Fixes train/serve prompt drift: some candidates (esp. the supervisor-panel
    stub) carry a divergent or missing system prompt. We overwrite index 0 (or
    prepend) so every training row matches what the agent is served at runtime.
    """
    if not isinstance(messages, list) or not messages:
        return messages
    result = list(messages)
    if isinstance(result[0], dict) and result[0].get("role") == "system":
        result[0] = {"role": "system", "content": _canonical_system_content()}
    else:
        result = [{"role": "system", "content": _canonical_system_content()}, *result]
    return result


def _is_synthetic(messages: list) -> bool:
    """Detect clearly-synthetic candidates to keep them out of training.

    Conservative: drops only rows whose customer_name is literally "gpt" or whose
    assistant reply is empty. An empty customer_message alone is NOT synthetic —
    it is a legitimate opening turn — so it is never used as the sole criterion.
    """
    if not isinstance(messages, list) or len(messages) < 3:
        return False
    user = next((m for m in messages if isinstance(m, dict) and m.get("role") == "user"), None)
    assistant = next((m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"), None)
    if user is None or assistant is None:
        return False

    a_content = assistant.get("content")
    agent_response_empty = False
    try:
        parsed = json.loads(a_content) if isinstance(a_content, str) else a_content
        if isinstance(parsed, dict):
            agent_response_empty = not str(parsed.get("agent_response") or "").strip()
        else:
            agent_response_empty = not str(a_content or "").strip()
    except (json.JSONDecodeError, TypeError):
        agent_response_empty = not str(a_content or "").strip()

    name_synthetic = False
    try:
        u = json.loads(user.get("content")) if isinstance(user.get("content"), str) else {}
        state = u.get("state") if isinstance(u, dict) and isinstance(u.get("state"), dict) else {}
        name_synthetic = str(state.get("customer_name") or "").strip().lower() == "gpt"
    except (json.JSONDecodeError, TypeError):
        name_synthetic = False

    return name_synthetic or agent_response_empty


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
    filtered_synthetic = 0
    golden_count = 0
    base_count = 0
    golden_sources: list[dict] = []
    base_sources: list[dict] = []

    with open(out, "w", encoding="utf-8") as fh:
        # Source 1: candidates
        for c in candidates:
            if _is_synthetic(c.messages_json):
                logger.info("Dropping synthetic training candidate id=%s", c.id)
                c.approved = False
                filtered_synthetic += 1
                continue
            # Force the canonical system prompt on EVERY candidate (fixes drift).
            messages = _force_canonical_system(c.messages_json)
            try:
                _validate_messages(messages, f"training_candidate:{c.id}")
            except ValueError as exc:
                if "assistant content must be valid JSON" not in str(exc):
                    raise
                messages = _normalize_legacy_candidate(c, db)
                _validate_messages(messages, f"training_candidate:{c.id}")
            fh.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
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
    db.commit()

    output_digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "dataset_version": dataset_version,
        "output_path": output_path,
        "dataset_sha256": output_digest,
        "row_count": total,
        "candidates": candidate_count,
        "filtered_synthetic": filtered_synthetic,
        "candidate_ids": [candidate.id for candidate in candidates],
        "golden": golden_count,
        "golden_sources": golden_sources,
        "base": base_count,
        "base_sources": base_sources,
    }
