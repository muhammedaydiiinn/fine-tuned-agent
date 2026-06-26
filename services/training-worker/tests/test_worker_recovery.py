from types import SimpleNamespace
from unittest.mock import Mock

from job_lifecycle import (
    PROCESSING_QUEUE_NAME,
    QUEUE_NAME,
    is_terminal_status,
    next_version_name,
    requeue_interrupted_jobs,
)


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.result


def test_next_version_uses_clean_generation_when_available():
    db = Mock()
    db.query.return_value = _Query(None)

    assert next_version_name(db, "anrufblocker-v14", 7) == "anrufblocker-v15"


def test_next_version_adds_job_suffix_on_generation_collision():
    db = Mock()
    db.query.return_value = _Query(SimpleNamespace(id=3))

    assert next_version_name(db, "anrufblocker-v14", 7) == "anrufblocker-v15-job7"


def test_requeue_interrupted_jobs_restores_every_unacked_message():
    client = Mock()
    client.rpoplpush.side_effect = ["job-1", "job-2", None]

    recovered = requeue_interrupted_jobs(client)

    assert recovered == 2
    assert client.rpoplpush.call_count == 3
    client.rpoplpush.assert_called_with(PROCESSING_QUEUE_NAME, QUEUE_NAME)


def test_completed_and_failed_jobs_are_terminal():
    assert is_terminal_status("completed")
    assert is_terminal_status("failed")
    assert not is_terminal_status("running")
