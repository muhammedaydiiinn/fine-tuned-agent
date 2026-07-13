"""DB-backed editable sales-policy content with an in-memory TTL cache.

Single source of truth for the content that feeds the model — the sales
script/persona (``system_instruction``), product facts, PDF rules, the
objection FAQ (arguments + sss), and the canned answers (hazır cevaplar) the
code enforces on the model output. The supervisor panel edits the
``policy_content`` table; this module reads it for the runtime prompt build,
the guardrails, and the training builders, so an edit reaches the model both at
inference time (prompt) and at training time (candidate/dataset system content).

Missing sections/keys fall back to the hardcoded defaults in ``product_facts``
and ``policy/system_instruction.txt`` so the system works before the table is
seeded and never crashes on an empty or partial row. A panel edit is picked up
within ``CACHE_TTL_SECONDS`` with no restart (the panel writes the same table
this cache reads).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.core import product_facts as pf

logger = logging.getLogger(__name__)

# Section keys — one row per section in the policy_content table.
SECTION_SYSTEM_INSTRUCTION = "system_instruction"
SECTION_PRODUCT_FACTS = "product_facts"
SECTION_PDF_RULES = "pdf_rules"
SECTION_OBJECTION_FAQ = "objection_faq"
SECTION_CANNED_ANSWERS = "canned_answers"

SECTIONS: tuple[str, ...] = (
    SECTION_SYSTEM_INSTRUCTION,
    SECTION_PRODUCT_FACTS,
    SECTION_PDF_RULES,
    SECTION_OBJECTION_FAQ,
    SECTION_CANNED_ANSWERS,
)

CACHE_TTL_SECONDS = 30.0

_POLICY_DIR = Path(__file__).resolve().parent / "policy"
_INSTRUCTION_PATH = _POLICY_DIR / "system_instruction.txt"

_LEGACY_SYSTEM_INSTRUCTION = (
    "You are Anna Weber, Sicherheitsberaterin for CallShield Gold Paket. "
    "Return ONLY a single JSON policy object."
)

_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cache_ts: float = 0.0


# ── Defaults ────────────────────────────────────────────────────────────────

def _default_system_instruction() -> str:
    if _INSTRUCTION_PATH.is_file():
        return _INSTRUCTION_PATH.read_text(encoding="utf-8").strip()
    return _LEGACY_SYSTEM_INSTRUCTION


def default_value(section: str) -> dict:
    """The hardcoded default for a section, in the stored JSON shape."""
    if section == SECTION_SYSTEM_INSTRUCTION:
        return {"text": _default_system_instruction()}
    if section == SECTION_PRODUCT_FACTS:
        return dict(pf.PRODUCT_FACTS)
    if section == SECTION_PDF_RULES:
        return {"rules": list(pf.PDF_RULES)}
    if section == SECTION_OBJECTION_FAQ:
        return {"items": [dict(item) for item in pf.OBJECTION_FAQ]}
    if section == SECTION_CANNED_ANSWERS:
        return dict(pf.CANNED_ANSWERS)
    raise ValueError(f"Unknown policy_content section: {section}")


def default_content() -> dict[str, dict]:
    """All section defaults — used to seed the table on startup."""
    return {section: default_value(section) for section in SECTIONS}


# ── Cache ───────────────────────────────────────────────────────────────────

def _load() -> dict[str, dict]:
    """Read all policy_content rows into {section: value_json}. Never raises."""
    try:
        from app.db import SessionLocal
        from app.models import PolicyContent

        db = SessionLocal()
        try:
            rows = db.query(PolicyContent).all()
            return {row.section: (row.value_json or {}) for row in rows}
        finally:
            db.close()
    except Exception:  # DB not ready / table missing — fall back to defaults.
        logger.warning("policy_content load failed; using hardcoded defaults", exc_info=True)
        return {}


def _get_cache() -> dict[str, dict]:
    global _cache, _cache_ts
    now = time.monotonic()
    with _lock:
        if _cache is None or (now - _cache_ts) >= CACHE_TTL_SECONDS:
            _cache = _load()
            _cache_ts = now
        return _cache


def invalidate() -> None:
    """Force the next accessor call to reload from the DB."""
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0


def _section(section: str) -> dict:
    """DB value for a section, or {} when absent — merge with defaults per accessor."""
    value = _get_cache().get(section)
    return value if isinstance(value, dict) else {}


# ── Accessors (default-merged, panel-editable) ────────────────────────────────

def system_instruction() -> str:
    text = _section(SECTION_SYSTEM_INSTRUCTION).get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return _default_system_instruction()


def product_facts() -> dict[str, str]:
    """Full facts dict — DB values layered over the defaults (all keys present)."""
    merged = dict(pf.PRODUCT_FACTS)
    for key, value in _section(SECTION_PRODUCT_FACTS).items():
        if isinstance(value, str) and value.strip():
            merged[key] = value
    return merged


def pdf_rules() -> list[str]:
    rules = _section(SECTION_PDF_RULES).get("rules")
    if isinstance(rules, list) and rules:
        cleaned = [str(r).strip() for r in rules if str(r).strip()]
        if cleaned:
            return cleaned
    return list(pf.PDF_RULES)


def objection_faq() -> list[dict[str, str]]:
    items = _section(SECTION_OBJECTION_FAQ).get("items")
    if isinstance(items, list) and items:
        cleaned = [
            {"trigger": str(i.get("trigger", "")).strip(), "answer": str(i.get("answer", "")).strip()}
            for i in items
            if isinstance(i, dict) and str(i.get("trigger", "")).strip()
        ]
        if cleaned:
            return cleaned
    return [dict(item) for item in pf.OBJECTION_FAQ]


def canned_answers() -> dict[str, str]:
    """Full canned-answer map — DB values layered over the defaults."""
    merged = dict(pf.CANNED_ANSWERS)
    for key, value in _section(SECTION_CANNED_ANSWERS).items():
        if isinstance(value, str) and value.strip():
            merged[key] = value
    return merged


def canned(key: str) -> str:
    """The effective (panel-editable) text for one canned answer."""
    return canned_answers().get(key, pf.CANNED_ANSWERS.get(key, ""))
