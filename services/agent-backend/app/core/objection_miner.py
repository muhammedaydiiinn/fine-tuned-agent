"""Mine imported call recordings for objection→answer pairs (WP-5).

Employee recordings arrive as Sessions/Turns via recording_pipeline. This
module walks those turns, asks the production LLM which customer utterances
are library-worthy objections/questions and generalizes the seller's answer,
then filters the result through the dataset price rules and dedups it against
the existing panel-editable objection library.

Run inside the agent-backend container (avoids HTTP timeouts on long scans):

    python -m app.core.objection_miner                  # scan + print suggestions
    python -m app.core.objection_miner --json out.json  # also write JSON
    python -m app.core.objection_miner --apply out.json --ids 1,3,4
                                                        # merge picked items
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

RECORDING_MODEL_VERSION = "recording_import"

# Backchannels and micro-turns carry no reusable objection.
_MIN_CUSTOMER_CHARS = 18
_MIN_ANSWER_CHARS = 40

_EXTRACT_SYSTEM = (
    "Du analysierst einen Austausch aus einem ECHTEN, erfolgreichen Verkaufs"
    "gespräch für 'CallShield Gold Paket' (14 Tage kostenlos, danach 29,99 "
    "Euro monatlich; Check normalerweise 18 Euro, heute kostenfrei; Rechts"
    "schutz bis 2.500 Euro).\n\n"
    "Gegeben: eine Kundenäußerung und die Antwort des menschlichen Verkäufers. "
    "Entscheide, ob die Kundenäußerung ein EINWAND oder eine FRAGE ist, die in "
    "eine wiederverwendbare Einwand-Bibliothek gehört (Preis, Sicherheit, "
    "Zeit, Misstrauen, Technik, 'habe schon etwas', Datenschutz, ...).\n"
    "Wenn ja: formuliere 'trigger' als kurze, verallgemeinerte Kundenformulierung "
    "(ohne Namen/Nummern) und 'answer' als aufgeräumte, wiederverwendbare "
    "Version der Verkäuferantwort (ohne Namen, ohne Füllwörter, Preise nur "
    "29,99 Euro / 2.500 Euro / '18 Euro Check heute kostenfrei' wörtlich wie "
    "gegeben, nichts erfinden).\n"
    "Small Talk, Bestätigungen, App-Bedienschritte und Identitätsfragen sind "
    "NICHT bibliothekswürdig.\n\n"
    'Antworte NUR mit JSON: {"useful": true|false, "theme": "<kurz>", '
    '"trigger": "<kundenformulierung>", "answer": "<antwort>"}'
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "useful": {"type": "boolean"},
        "theme": {"type": "string"},
        "trigger": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["useful", "theme", "trigger", "answer"],
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zäöüß0-9]+", (text or "").lower()) if len(t) > 3}


def _similar(a: str, b: str, threshold: float = 0.55) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def collect_pairs(db: DBSession, session_ids: list[int] | None = None) -> list[dict]:
    """(customer_text, agent_response) pairs from imported recording turns."""
    from app.models import Turn

    query = db.query(Turn).filter(Turn.model_version == RECORDING_MODEL_VERSION)
    if session_ids:
        query = query.filter(Turn.session_id.in_(session_ids))
    pairs = []
    for turn in query.order_by(Turn.session_id, Turn.turn_index).all():
        customer = (turn.customer_text or "").strip()
        answer = (turn.agent_response or "").strip()
        if len(customer) < _MIN_CUSTOMER_CHARS or len(answer) < _MIN_ANSWER_CHARS:
            continue
        pairs.append({
            "session_id": turn.session_id,
            "turn_id": turn.id,
            "customer_text": customer,
            "agent_response": answer,
        })
    return pairs


def _extract(pair: dict, target: dict) -> dict | None:
    from app.core import json_repair, vllm_client

    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": json.dumps(
            {"kunde": pair["customer_text"], "verkaeufer": pair["agent_response"]},
            ensure_ascii=False,
        )},
    ]
    try:
        raw = vllm_client.chat(
            messages,
            target,
            temperature=0.1,
            max_tokens=350,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "mined", "schema": _EXTRACT_SCHEMA, "strict": True},
            },
        )
        return json_repair.extract_json(raw)
    except Exception:
        logger.exception("objection_miner: extraction failed turn_id=%s", pair["turn_id"])
        return None


def mine(db: DBSession, session_ids: list[int] | None = None, max_pairs: int = 300) -> list[dict]:
    """Scan imported recordings and return deduped library suggestions."""
    from app.core import content_store, model_runtime
    from app.core.recording_pipeline import validate_training_text

    target = model_runtime.production_serving_target()
    existing = content_store.objection_faq()
    pairs = collect_pairs(db, session_ids)[:max_pairs]
    logger.info("objection_miner: scanning %d pairs", len(pairs))

    suggestions: list[dict] = []
    for pair in pairs:
        mined = _extract(pair, target)
        if not mined or not mined.get("useful"):
            continue
        trigger = (mined.get("trigger") or "").strip()
        answer = (mined.get("answer") or "").strip()
        if not trigger or len(answer) < _MIN_ANSWER_CHARS:
            continue
        if any(_similar(answer, item["answer"]) or _similar(trigger, item["trigger"], 0.7)
               for item in existing):
            continue
        if any(_similar(answer, s["answer"]) or _similar(trigger, s["trigger"], 0.7)
               for s in suggestions):
            continue
        suggestions.append({
            "id": len(suggestions) + 1,
            "theme": (mined.get("theme") or "").strip(),
            "trigger": trigger,
            "answer": answer,
            "warnings": validate_training_text("", answer),
            "source_session_id": pair["session_id"],
            "source_turn_id": pair["turn_id"],
        })
    logger.info("objection_miner: %d suggestions", len(suggestions))
    return suggestions


def apply_suggestions(db: DBSession, items: list[dict], updated_by: str = "miner") -> int:
    """Merge picked suggestions into the objection_faq section (with history)."""
    from app.core import content_store
    from app.models import PolicyContent, PolicyContentHistory

    additions = [
        {"trigger": item["trigger"].strip(), "answer": item["answer"].strip()}
        for item in items
        if (item.get("trigger") or "").strip() and (item.get("answer") or "").strip()
    ]
    if not additions:
        return 0
    merged = content_store.objection_faq() + additions
    row = (
        db.query(PolicyContent)
        .filter(PolicyContent.section == content_store.SECTION_OBJECTION_FAQ)
        .first()
    )
    if row is not None:
        db.add(PolicyContentHistory(
            section=content_store.SECTION_OBJECTION_FAQ,
            value_json=row.value_json or {},
            created_by=updated_by,
        ))
    else:
        row = PolicyContent(section=content_store.SECTION_OBJECTION_FAQ)
        db.add(row)
    row.value_json = {"items": merged}
    row.updated_by = updated_by
    db.commit()
    content_store.invalidate()
    logger.info("objection_miner: %d entries merged into objection_faq", len(additions))
    return len(additions)


def main() -> None:
    import argparse

    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Mine imported recordings for objection library entries")
    parser.add_argument("--sessions", help="comma-separated session ids (default: all imports)")
    parser.add_argument("--json", dest="json_path", help="write suggestions to this JSON file")
    parser.add_argument("--apply", dest="apply_path", help="JSON file of suggestions to merge")
    parser.add_argument("--ids", help="with --apply: comma-separated suggestion ids to merge")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    db = SessionLocal()
    try:
        if args.apply_path:
            with open(args.apply_path, encoding="utf-8") as fh:
                items = json.load(fh)
            if args.ids:
                wanted = {int(x) for x in args.ids.split(",") if x.strip()}
                items = [i for i in items if int(i.get("id", -1)) in wanted]
            count = apply_suggestions(db, items)
            print(f"{count} kayıt objection_faq kütüphanesine eklendi.")
            return
        session_ids = None
        if args.sessions:
            session_ids = [int(x) for x in args.sessions.split(",") if x.strip()]
        suggestions = mine(db, session_ids)
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as fh:
                json.dump(suggestions, fh, ensure_ascii=False, indent=1)
        for item in suggestions:
            flag = " ⚠" + ";".join(item["warnings"]) if item["warnings"] else ""
            print(f"[{item['id']}] ({item['theme']}) {item['trigger']}{flag}")
            print(f"     → {item['answer']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
