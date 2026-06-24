import json
from types import SimpleNamespace

from jobs.build_dataset import _normalize_legacy_candidate, _validate_messages


class _FakeQuery:
    def __init__(self, turn):
        self._turn = turn

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._turn


class _FakeDB:
    def __init__(self, turn):
        self._turn = turn

    def query(self, _model):
        return _FakeQuery(self._turn)


def test_normalize_legacy_candidate_rewrites_plain_text_assistant():
    turn = SimpleNamespace(
        id=9,
        customer_text="Konnen Sie mir mehr Informationen schicken?",
        agent_response="Fallback response",
        next_action="close",
        intent="info_request",
        emotion="confused",
        risk="low",
        allowed_to_continue=True,
        state_before_json={"stage": "follow_up"},
    )
    candidate = SimpleNamespace(
        id=27,
        source_id=9,
        metadata_json={"correction_type": "next_action_override"},
        messages_json=[
            {"role": "system", "content": "Legacy system"},
            {"role": "user", "content": "legacy user text"},
            {"role": "assistant", "content": "Legacy corrected response"},
        ],
    )

    normalized = _normalize_legacy_candidate(candidate, _FakeDB(turn))
    _validate_messages(normalized, "training_candidate:27")

    assistant_policy = json.loads(normalized[-1]["content"])
    assert normalized[1]["role"] == "user"
    assert json.loads(normalized[1]["content"]) == {
        "customer_message": "Konnen Sie mir mehr Informationen schicken?",
        "state": {"stage": "follow_up"},
    }
    assert assistant_policy["agent_response"] == "Legacy corrected response"
    assert assistant_policy["next_action"] == "close"
    assert candidate.metadata_json["normalized_from_legacy"] is True
