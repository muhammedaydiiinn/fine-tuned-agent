"""Sales-policy content editor.

Edit the content that feeds the model — sales script/persona, product facts,
PDF rules, objection FAQ (arguments + sss), and canned answers (hazır cevaplar).
Writes the `policy_content` table that agent-backend reads (live, ~30s TTL) and
the training builders bake into candidates, so an edit reaches the model both at
inference and at training time. Every save snapshots the previous value into
`policy_content_history` for one-click rollback.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import PolicyContent, PolicyContentHistory
from app.ui_feedback import toast_redirect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

# Fixed keys per structured section — labels + order for the editor. The values
# are edited; the keys stay fixed so the prompt layout stays stable.
PRODUCT_FACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("trial_period", "Deneme süresi"),
    ("monthly_price", "Aylık ücret (deneme sonrası)"),
    ("check_price_normal", "Tek seferlik kontrol — normal fiyat"),
    ("check_price_today", "Tek seferlik kontrol — bugün"),
    ("app_stores", "Uygulama mağazaları"),
    ("blocked_numbers", "Engellenen numaralar"),
    ("risk_entries_example", "Tarama sonucu örneği"),
    ("risk_entries_range", "Tarama sonucu aralığı"),
    ("legal_support", "Hukuki destek"),
    ("support_channel", "Destek kanalı"),
)

CANNED_ANSWER_FIELDS: tuple[tuple[str, str], ...] = (
    ("price", "Fiyat / deneme — fiyat sorularında zorunlu"),
    ("check_price", "Tek seferlik kontrol fiyatı"),
    ("security", "Güvenlik / güvenli bağlantı — güvenlik itirazında zorunlu"),
    ("delay_deferral", "Erteleme — telefon girişini ertele (zorunlu)"),
    ("forbidden_data", "Yasak veri talebi"),
    ("closing_brief", "Kapanış vedası — kapanıştan sonra zorunlu"),
    ("check_explain", "Kontrol açıklaması"),
    ("problem_awareness", "Sorun farkındalığı"),
)

SECTION_LABELS: dict[str, str] = {
    "system_instruction": "Satış senaryosu ve persona",
    "product_facts": "Ürün bilgileri",
    "pdf_rules": "Kurallar",
    "objection_faq": "Argümanlar ve SSS",
    "canned_answers": "Hazır cevaplar",
}

SECTIONS = tuple(SECTION_LABELS)

_HISTORY_LIMIT = 10


def _current(db: DBSession) -> dict[str, dict]:
    return {row.section: (row.value_json or {}) for row in db.query(PolicyContent).all()}


def _history(db: DBSession) -> dict[str, list[PolicyContentHistory]]:
    out: dict[str, list[PolicyContentHistory]] = {s: [] for s in SECTIONS}
    rows = (
        db.query(PolicyContentHistory)
        .order_by(PolicyContentHistory.created_at.desc())
        .all()
    )
    for row in rows:
        bucket = out.setdefault(row.section, [])
        if len(bucket) < _HISTORY_LIMIT:
            bucket.append(row)
    return out


@router.get("/content", response_class=HTMLResponse)
def content_editor(request: Request, db: DBSession = Depends(get_db)):
    content = _current(db)
    faq_items = (content.get("objection_faq") or {}).get("items") or []
    rules = (content.get("pdf_rules") or {}).get("rules") or []
    return templates.TemplateResponse(
        "content.html",
        {
            "request": request,
            "content": content,
            "system_text": (content.get("system_instruction") or {}).get("text", ""),
            "product_facts": content.get("product_facts") or {},
            "product_fact_fields": PRODUCT_FACT_FIELDS,
            "product_fact_labels": dict(PRODUCT_FACT_FIELDS),
            "rules_text": "\n".join(rules),
            "faq_items": faq_items,
            "canned_answers": content.get("canned_answers") or {},
            "canned_answer_fields": CANNED_ANSWER_FIELDS,
            "canned_answer_labels": dict(CANNED_ANSWER_FIELDS),
            "section_labels": SECTION_LABELS,
            "history": _history(db),
        },
    )


def _snapshot(db: DBSession, section: str) -> PolicyContent | None:
    """Copy the current row into history and return it (for in-place update)."""
    row = db.query(PolicyContent).filter(PolicyContent.section == section).first()
    if row is not None:
        db.add(PolicyContentHistory(
            section=section,
            value_json=row.value_json or {},
            created_by=settings.admin_user,
        ))
    return row


def _persist(db: DBSession, section: str, value: dict) -> None:
    row = _snapshot(db, section)
    if row is None:
        row = PolicyContent(section=section)
        db.add(row)
    row.value_json = value
    row.updated_by = settings.admin_user
    db.commit()


def _build_value(form, section: str) -> dict | None:
    """Build the section's value_json from submitted form data. None → invalid.

    Pure (form in → dict out) so it can be unit-tested without a request/DB.
    ``form`` is any Starlette ``FormData``-like with ``.get`` and ``.getlist``.
    """
    if section == "system_instruction":
        text = (form.get("text") or "").strip()
        return {"text": text} if text else None
    if section == "product_facts":
        return {
            key: (form.get(f"fact__{key}") or "").strip()
            for key, _ in PRODUCT_FACT_FIELDS
        }
    if section == "pdf_rules":
        rules = [line.strip() for line in (form.get("rules") or "").splitlines() if line.strip()]
        return {"rules": rules} if rules else None
    if section == "objection_faq":
        triggers = form.getlist("trigger")
        answers = form.getlist("answer")
        items = [
            {"trigger": t.strip(), "answer": (a or "").strip()}
            for t, a in zip(triggers, answers)
            if t and t.strip()
        ]
        return {"items": items} if items else None
    if section == "canned_answers":
        return {
            key: (form.get(f"canned__{key}") or "").strip()
            for key, _ in CANNED_ANSWER_FIELDS
        }
    return None


@router.post("/content/{section}")
async def save_content(
    section: str,
    request: Request,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    if section not in SECTIONS:
        return toast_redirect("/content", "Bilinmeyen bölüm.", kind="error")
    value = _build_value(await request.form(), section)
    if value is None:
        return toast_redirect(
            "/content",
            "Hiçbir şey kaydedilmedi — bölüm boş bırakılamaz.",
            kind="warning",
        )
    _persist(db, section, value)
    logger.info("policy_content saved: section=%s by=%s", section, settings.admin_user)
    return toast_redirect(
        "/content",
        f"{SECTION_LABELS[section]} kaydedildi — yeni çağrılarda ~30 sn içinde etkin ve bir sonraki eğitim çalışmasına dahil.",
        title="İçerik güncellendi",
    )


@router.post("/content/{section}/rollback/{history_id}")
def rollback_content(
    section: str,
    history_id: int,
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    if section not in SECTIONS:
        return toast_redirect("/content", "Bilinmeyen bölüm.", kind="error")
    snapshot = (
        db.query(PolicyContentHistory)
        .filter(
            PolicyContentHistory.id == history_id,
            PolicyContentHistory.section == section,
        )
        .first()
    )
    if snapshot is None:
        return toast_redirect("/content", "Sürüm bulunamadı.", kind="error")
    _persist(db, section, snapshot.value_json or {})
    logger.info("policy_content rolled back: section=%s to history=%d", section, history_id)
    return toast_redirect(
        "/content",
        f"{SECTION_LABELS[section]} önceki bir sürüme geri alındı.",
        title="Geri alındı",
    )
