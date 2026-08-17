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
    """
    text = load_system_instruction().rstrip()
    name = (agent_name or "Anna Weber").strip()
    persona = (agent_role or "").strip() or agent_identity.role_for_name(name)
    return text.replace("{name}", name).replace("{persona}", persona)
