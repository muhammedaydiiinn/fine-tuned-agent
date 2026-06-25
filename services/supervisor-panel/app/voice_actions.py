from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PreparedVoiceAction:
    command: dict
    correction_payload: dict | None
    audit_payload: dict


def prepare_voice_action(
    *,
    action: str,
    action_id: str,
    actor: str,
    external_session_id: str,
    session_id: int,
    latest_turn,
    replacement_text: str,
    corrected_next_action: str,
    notes: str,
) -> PreparedVoiceAction:
    # Live corrections always apply immediately and go to training.
    apply_immediately = True
    send_to_training = True
    action_name = action.strip()
    if action_name not in {"stop_agent", "replace_answer"}:
        raise ValueError("Unsupported voice action")

    command = {
        "action": action_name,
        "action_id": action_id,
        "actor": actor,
    }

    correction_payload = None
    if action_name == "replace_answer":
        clean_text = replacement_text.strip()
        if not clean_text:
            raise ValueError("Replacement text is required")
        if latest_turn is None:
            raise LookupError("No turn is available for replacement")
        clean_next_action = corrected_next_action.strip() or latest_turn.next_action
        correction_payload = {
            "session_id": session_id,
            "turn_id": latest_turn.id,
            "correction_type": "live_replace_answer",
            "old_agent_response": latest_turn.agent_response,
            "corrected_agent_response": clean_text,
            "old_next_action": latest_turn.next_action,
            "corrected_next_action": clean_next_action,
            "notes": notes.strip() or "Live supervisor replacement",
            "apply_immediately": apply_immediately,
            "send_to_training": send_to_training,
        }
        command["text"] = clean_text

    audit_payload = {
        "session_id": external_session_id,
        "event_id": f"supervisor:{action_id}:requested",
        "sequence": 0,
        "event_type": "supervisor_action_requested",
        "turn_id": latest_turn.id if latest_turn else None,
        "payload": {
            "action": action_name,
            "actor": actor,
            "replacement_text": command.get("text", ""),
            "apply_immediately": apply_immediately,
            "send_to_training": send_to_training,
            "correction_id": None,
        },
    }

    return PreparedVoiceAction(
        command=command,
        correction_payload=correction_payload,
        audit_payload=audit_payload,
    )
