"""Session state yönetimi.

State, sessions.state_json JSONB sütununda saklanır.
Her turn sonrasında update() çağrılarak yeni state kaydedilir.
"""
import logging
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

# Varsayılan başlangıç state
DEFAULT_STATE: dict[str, Any] = {
    "stage": "initial",
    "goal": "sell_activation",
    "hard_decline_count": 0,
    "identity_confirmed": False,
    "offer_terms_explained": False,
    "price_explained": False,
    "link_sent": False,
    "last_next_actions": [],   # Son 5 next_action (tekrar tespiti için)
    "turn_count": 0,
}

# Kaç next_action geçmişi tutulacak
NEXT_ACTION_HISTORY_SIZE = 5


def load(session_model) -> dict[str, Any]:
    """Session modelinden state_json'u dict olarak döndürür.
    Eksik alanları DEFAULT_STATE'ten tamamlar.
    """
    raw: dict = session_model.state_json or {}
    state = {**DEFAULT_STATE, **raw}
    return state


def update(state: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Bir turn'ün policy çıktısına göre state'i günceller."""
    new_state = dict(state)
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    new_state["turn_count"] = new_state.get("turn_count", 0) + 1

    # Hard decline sayacı
    if intent == "hard_decline":
        new_state["hard_decline_count"] = new_state.get("hard_decline_count", 0) + 1
    else:
        # Yumuşak bir etkileşimde sayacı azaltma (isteğe bağlı)
        pass

    # Identity onayı
    if next_action == "confirm_identity" and policy.get("allowed_to_continue"):
        new_state["identity_confirmed"] = True

    # Fiyat/koşul açıklaması
    if intent in ("price_question", "free_question") and next_action in (
        "explain_price", "explain_trial"
    ):
        new_state["price_explained"] = True
        new_state["offer_terms_explained"] = True

    # Link gönderildi
    if next_action == "send_activation_link":
        new_state["link_sent"] = True

    # Stage güncelleme
    if next_action == "close_call":
        new_state["stage"] = "closing"
    elif new_state.get("stage") == "initial" and new_state["turn_count"] > 1:
        new_state["stage"] = "conversation"

    # Next action geçmişi
    history: list = list(new_state.get("last_next_actions", []))
    history.append(next_action)
    new_state["last_next_actions"] = history[-NEXT_ACTION_HISTORY_SIZE:]

    return new_state


def persist(db: DBSession, session_model, new_state: dict[str, Any]) -> None:
    """Güncel state'i veritabanına yazar."""
    session_model.state_json = new_state
    db.add(session_model)
    db.commit()
    db.refresh(session_model)
