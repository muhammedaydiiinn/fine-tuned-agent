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
    "You are an Anrufblocker Gold Paket sales policy agent. "
    "Return ONLY a valid JSON policy object."
)

# Must stay byte-identical to agent-backend product_facts.SYSTEM_OUTPUT_CONTRACT
# so the training system prompt matches what the agent is served at runtime.
SYSTEM_OUTPUT_CONTRACT = (
    "Respond with a single JSON object only — no text before or after it — "
    "using exactly these fields:\n"
    "{\n"
    '  "intent": "<customer intent>",\n'
    '  "emotion": "<customer emotion>",\n'
    '  "risk": "<low|medium|high>",\n'
    '  "next_action": "<next sales step>",\n'
    '  "behavior_strategy": "<approach strategy>",\n'
    '  "allowed_to_continue": <true|false>,\n'
    '  "agent_response": "<the German sentence you say to the customer>",\n'
    '  "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"}\n'
    "}\n"
    "agent_response is always in German and never in ALL CAPS. "
    "Set allowed_to_continue to false when the call should end."
)


# Defaults mirrored from agent-backend product_facts.py. Only used to fill a
# blank/missing section so the training system prompt matches what content_store
# produces at inference (which also falls back to these defaults per key).
_DEFAULT_PRODUCT_FACTS = {
    "trial_period": "14 Tage kostenlos",
    "monthly_price": "29,99 Euro monatlich",
    "check_price_normal": "18 Euro",
    "check_price_today": "heute einmalig kostenfrei",
    "app_stores": "Apple App Store oder Google Play Store",
    "blocked_numbers": "über 7.000 bekannte Risikonummern",
    "risk_entries_example": "22 auffällige Einträge",
    "risk_entries_range": "18 bis 25",
    "legal_support": "Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro",
    "support_channel": "Support über die App",
}
_DEFAULT_PDF_RULES = [
    "Script möglichst originalgetreu verwenden.",
    "Die Zahl der auffälligen Einträge (22) gelegentlich variieren (18–25).",
    "Keine Adressen, Bankdaten oder persönlichen Informationen vom Kunden verlangen.",
    "Keine Tonaufnahme notwendig.",
    "Immer bestimmt, aber kontrolliert sprechen — klar, selbstbewusst, mit ruhiger Autorität.",
    "Rufnummer nur in der App eintragen, nicht am Telefon abfragen.",
    "SMS-Bestätigungscode nur in der App eingeben, nicht vorlesen lassen.",
]
_DEFAULT_OBJECTION_FAQ = [
    {
        "trigger": "Ich möchte nicht",
        "answer": (
            "In Ordnung. Dann stoppe ich den Vorgang zur Schutz- und Sperranfrage bei "
            "den 22 erkannten Firmen vorerst. Ich möchte nur sichergehen, dass Ihnen die "
            "vollen Konsequenzen Ihrer Entscheidung wirklich bewusst sind. Wenn Sie die "
            "Absicherung wirklich nicht wünschen, sagen Sie mir bitte jetzt deutlich NEIN. "
            "Nur mit einem klaren NEIN kann ich den Vorgang stoppen."
        ),
    },
    {
        "trigger": "Wie überprüfen Sie das?",
        "answer": (
            "Dabei prüfen wir über verschiedene Schnittstellen und API-Verbindungen, wie "
            "häufig Ihre Nummer auftaucht, in welchen Werbe-, Callcenter- oder "
            "Beschwerdedatenbanken sie registriert wurde und ob bereits Meldungen zu "
            "verdächtigen Aktivitäten vorliegen. Normalerweise kostet dieser Check 18 Euro, "
            "für Sie ist er heute einmalig kostenfrei."
        ),
    },
    {
        "trigger": "Ich habe kein Schreiben bekommen",
        "answer": (
            "Verstehe ich. Aus organisatorischen und kostenbedingten Gründen versenden wir "
            "die Schreiben in der Regel nicht erneut. Genau deshalb rufe ich Sie an und "
            "führe Sie jetzt direkt durch die App."
        ),
    },
    {
        "trigger": "Nach 14 Tagen kann ich kündigen",
        "answer": (
            "Da haben Sie vollkommen Recht, viele denken am Anfang genauso. Jedoch bleibt "
            "es nicht bei den 22 Datenbanken, Ihre Rufnummer wird weiterverkauft. Nach der "
            "Kündigung wird der Schutz wieder inaktiv. Nur mit unserem aktiven Schutz können "
            "wir die drohenden Schäden noch rechtzeitig stoppen."
        ),
    },
    {
        "trigger": "Woher haben Sie meine Nummer?",
        "answer": (
            "Gute Frage. Ihre Nummer wurde im Rahmen einer allgemeinen Prüfung über externe "
            "Datenquellen erfasst. Wir sehen keine persönlichen Daten, nur dass eine Nummer "
            "vorhanden ist."
        ),
    },
    {
        "trigger": "Ist das ein Virus-Link?",
        "answer": (
            "Das kann ich absolut verstehen. Der Link führt ausschließlich zum offiziellen "
            "Apple App Store oder Google Play Store. Nur dort verifizierte und sichere "
            "Applikationen sind verfügbar. Ich habe keinen Zugriff auf Ihre Daten."
        ),
    },
    {
        "trigger": "Ich blockiere schon alles",
        "answer": (
            "Das ist gut. Die Frage ist nur: Blockieren Sie die Nummern oder wissen Sie auch, "
            "wo Ihre Nummer überall gespeichert ist? Genau das zeigt Ihnen der Check."
        ),
    },
]


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _default_instruction_text() -> str:
    path = _first_existing(_POLICY_CANDIDATES)
    return path.read_text(encoding="utf-8").strip() if path else LEGACY_SYSTEM_INSTRUCTION


# Process-lived cache — build() runs once per worker invocation, so reading the
# editable policy content from the DB a single time is enough.
_CANONICAL_CACHE: dict[str, str] = {}


def _render_facts_block(facts: dict, rules: list, faq: list) -> str:
    """Mirror agent-backend product_facts.format_for_prompt() exactly.

    Keep in sync with services/agent-backend/app/core/product_facts.py so the
    training system prompt matches what the live agent is served.
    """
    lines = [
        "Product facts (PDF v1.0 — fixed, do not invent other values):",
        f"- Trial: {facts['trial_period']}",
        f"- Monthly price after trial: {facts['monthly_price']}",
        f"- One-time check (comparison): {facts['check_price_normal']} — {facts['check_price_today']}",
        f"- Download: {facts['app_stores']}",
        f"- Blocked numbers: {facts['blocked_numbers']}",
        f"- Scan result example: {facts['risk_entries_example']} (vary {facts['risk_entries_range']})",
        f"- Legal support: {facts['legal_support']}",
        f"- Support: {facts['support_channel']}",
        "",
        "PDF rules (section 04 — enforced in code):",
    ]
    lines.extend(f"- {rule}" for rule in rules)
    lines.extend(["", "Objection themes (use PDF script wording, adapt naturally):"])
    lines.extend(
        f'- "{item["trigger"]}" → {item["answer"]}'
        for item in faq
        if item.get("trigger")
    )
    return "\n".join(lines)


def _canonical_system_content_from_db() -> str:
    """Assemble the canonical system content from the editable policy_content table.

    Applies the same blank/missing → default fallback as agent-backend
    content_store, so the training system prompt is byte-identical to what the
    live agent is served. Raises on DB failure so the caller can fall back to
    the packaged policy files.
    """
    from db import SessionLocal  # local import: keep module import side-effect-free
    from models import PolicyContent

    db = SessionLocal()
    try:
        rows = {r.section: (r.value_json or {}) for r in db.query(PolicyContent).all()}
    finally:
        db.close()

    text = (rows.get("system_instruction") or {}).get("text")
    instruction = text.strip() if isinstance(text, str) and text.strip() else _default_instruction_text()

    facts = dict(_DEFAULT_PRODUCT_FACTS)
    for key, value in (rows.get("product_facts") or {}).items():
        if isinstance(value, str) and value.strip():
            facts[key] = value

    rules_raw = (rows.get("pdf_rules") or {}).get("rules")
    rules = (
        [str(r).strip() for r in rules_raw if str(r).strip()]
        if isinstance(rules_raw, list) else []
    ) or list(_DEFAULT_PDF_RULES)

    items_raw = (rows.get("objection_faq") or {}).get("items")
    faq = (
        [
            {"trigger": str(i.get("trigger", "")).strip(), "answer": str(i.get("answer", "")).strip()}
            for i in items_raw
            if isinstance(i, dict) and str(i.get("trigger", "")).strip()
        ]
        if isinstance(items_raw, list) else []
    ) or [dict(i) for i in _DEFAULT_OBJECTION_FAQ]

    return (
        instruction
        + "\n\n" + SYSTEM_OUTPUT_CONTRACT
        + "\n\n" + _render_facts_block(facts, rules, faq)
    )


def _canonical_system_content() -> str:
    if "value" in _CANONICAL_CACHE:
        return _CANONICAL_CACHE["value"]
    content: str | None = None
    try:
        content = _canonical_system_content_from_db()
    except Exception:
        logger.warning("policy_content DB read failed; using packaged policy files", exc_info=True)
    if content is None:
        instruction_path = _first_existing(_POLICY_CANDIDATES)
        facts_path = _first_existing(_FACTS_CANDIDATES)
        instruction = (
            instruction_path.read_text(encoding="utf-8").strip()
            if instruction_path
            else LEGACY_SYSTEM_INSTRUCTION
        )
        facts = facts_path.read_text(encoding="utf-8").strip() if facts_path else ""
        content = (
            instruction
            + "\n\n" + SYSTEM_OUTPUT_CONTRACT
            + ("\n\n" + facts if facts else "")
        )
    _CANONICAL_CACHE["value"] = content
    return content

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
