"""Canonical system instruction — runtime and approved training rows.

The system message is the mission-driven persona (panel-editable), with the
agent name/persona filled in per session. Product facts, sales script and
objection data are NOT appended here — they travel in the user payload's
known_customer_data, matching the fine-tuning format.
"""
from app.core import agent_identity


def load_system_instruction() -> str:
    """Live (panel-editable) mission/persona text, falling back to the policy file."""
    from app.core import content_store

    return content_store.system_instruction()


def build_system_content(agent_name: str = "", agent_role: str = "") -> str:
    """System message for inference and approved training examples.

    Fills the {name}/{persona} placeholders in the mission persona. Uses str
    .replace (not .format) so the literal JSON braces in the contract survive.

    The intent/next_action legend is a technical contract (like
    SYSTEM_OUTPUT_CONTRACT) and is appended in code: the panel-editable mission
    text stays free of taxonomy jargon, and a content edit can never silently
    drop the enum guidance the guided decoding relies on.
    """
    from app.core import product_facts

    text = load_system_instruction().rstrip()
    name = (agent_name or "Anna Weber").strip()
    persona = (agent_role or "").strip() or agent_identity.role_for_name(name)
    text = text.replace("{name}", name).replace("{persona}", persona)
    if "intent=price_question" not in text:  # skip if the editable text already carries a legend
        text += product_facts.NEXT_ACTION_LEGEND
    return text
