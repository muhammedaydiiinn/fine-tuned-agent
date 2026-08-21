"""Distill conversation STYLE from imported call recordings (WP-5, style mode).

The uploaded employee recordings may follow a different product script, so
their facts (prices, contract terms, banking steps) must never leak into the
agent. What they do carry is how good sellers talk: pacing, bridging
objections, keeping the lead, closing. This module extracts product-agnostic
technique observations per call, then distills them into a short set of
German conversation-style principles suitable for the mission persona.

Run inside the agent-backend container:

    python -m app.core.style_miner                 # print distilled principles
    python -m app.core.style_miner --json out.json # also write JSON
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

RECORDING_MODEL_VERSION = "recording_import"

_OBSERVE_SYSTEM = (
    "Du analysierst ein ECHTES Verkaufsgespräch (Transkript, Verkäufer vs. "
    "Kunde). Deine Aufgabe: beobachte NUR die GESPRÄCHSFÜHRUNG des Verkäufers "
    "— Ton, Tempo, Satzlänge, wie er Einwände überbrückt, wie er die Führung "
    "behält, wie er Verständnis bestätigt, wie er zum Abschluss führt.\n\n"
    "STRENG VERBOTEN in deinen Beobachtungen: Produktnamen, Preise, "
    "Euro-Beträge, Vertragsbedingungen, Bankdaten, konkrete Daten. Nur die "
    "TECHNIK, produktunabhängig formuliert.\n\n"
    'Antworte NUR mit JSON: {"observations": ["<kurze deutsche Beobachtung>", ...]} '
    "(3-6 Beobachtungen, jede höchstens 25 Wörter)."
)

_DISTILL_SYSTEM = (
    "Du bekommst Beobachtungen über die Gesprächsführung erfahrener "
    "Telefonverkäufer aus vielen echten Gesprächen. Verdichte sie zu höchstens "
    "8 klaren, umsetzbaren Stil-Prinzipien für einen KI-Verkaufsagenten.\n\n"
    "Regeln: produktunabhängig; keine Preise, keine Vertrags- oder Bankdetails; "
    "keine Dopplungen; jedes Prinzip eine Anweisung in Du-Form plus eine kurze "
    "neutrale Beispielformulierung (Platzhalter statt Produktdetails).\n\n"
    'Antworte NUR mit JSON: {"principles": [{"rule": "<Anweisung>", '
    '"example": "<Beispielformulierung>"}, ...]}'
)

_FORBIDDEN_LEAK = re.compile(
    r"\d+[.,]?\d*\s*(euro|eur|€)|iban|agb|widerruf|kündigungsfrist|abbuchung"
    r"|bankverbindung|kontonummer|zahlungsmethode|zahlung|lastschrift|vertragsbestätigung",
    re.IGNORECASE,
)


def collect_dialogues(db: DBSession, max_turns_per_session: int = 14) -> list[dict]:
    """Group imported recording turns into per-session dialogue transcripts."""
    from app.models import Turn

    turns = (
        db.query(Turn)
        .filter(Turn.model_version == RECORDING_MODEL_VERSION)
        .order_by(Turn.session_id, Turn.turn_index)
        .all()
    )
    sessions: dict[int, list[str]] = {}
    for turn in turns:
        lines = sessions.setdefault(turn.session_id, [])
        if len(lines) >= max_turns_per_session * 2:
            continue
        if (turn.customer_text or "").strip():
            lines.append(f"Kunde: {turn.customer_text.strip()}")
        if (turn.agent_response or "").strip():
            lines.append(f"Verkäufer: {turn.agent_response.strip()}")
    return [
        {"session_id": sid, "dialogue": "\n".join(lines)}
        for sid, lines in sessions.items()
        if len(lines) >= 4
    ]


def _chat_json(system: str, user: str, target: dict, max_tokens: int = 400) -> dict | None:
    from app.core import json_repair, vllm_client

    try:
        raw = vllm_client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            target,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return json_repair.extract_json(raw)
    except Exception:
        logger.exception("style_miner: LLM call failed")
        return None


def leaks_product_facts(text: str) -> bool:
    """True when a principle smuggles prices/contract/banking details."""
    return bool(_FORBIDDEN_LEAK.search(text or ""))


def mine_style(db: DBSession) -> dict:
    """Observe every imported call, then distill style principles."""
    from app.core import model_runtime

    target = model_runtime.production_serving_target()
    dialogues = collect_dialogues(db)
    logger.info("style_miner: %d dialogues", len(dialogues))

    observations: list[str] = []
    for item in dialogues:
        result = _chat_json(_OBSERVE_SYSTEM, item["dialogue"], target)
        for obs in (result or {}).get("observations") or []:
            obs = str(obs).strip()
            if obs and not leaks_product_facts(obs):
                observations.append(obs)
    logger.info("style_miner: %d observations", len(observations))
    if not observations:
        return {"observations": [], "principles": []}

    distilled = _chat_json(
        _DISTILL_SYSTEM,
        json.dumps(observations, ensure_ascii=False),
        target,
        max_tokens=700,
    )
    principles = []
    for entry in (distilled or {}).get("principles") or []:
        rule = str(entry.get("rule") or "").strip()
        example = str(entry.get("example") or "").strip()
        if rule and not leaks_product_facts(rule) and not leaks_product_facts(example):
            principles.append({"rule": rule, "example": example})
    return {"observations": observations, "principles": principles[:8]}


def main() -> None:
    import argparse

    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Distill conversation style from imported recordings")
    parser.add_argument("--json", dest="json_path", help="write result to this JSON file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    db = SessionLocal()
    try:
        result = mine_style(db)
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=1)
        print(f"\n{len(result['observations'])} gözlem → {len(result['principles'])} ilke\n")
        for i, p in enumerate(result["principles"], 1):
            print(f"[{i}] {p['rule']}")
            if p["example"]:
                print(f"    örn: {p['example']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
