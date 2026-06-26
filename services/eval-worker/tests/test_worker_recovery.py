from unittest.mock import Mock

from queue_recovery import (
    PROCESSING_QUEUE_NAME,
    QUEUE_NAME,
    is_terminal_status,
    requeue_interrupted_jobs,
)


def test_requeue_interrupted_eval_jobs_restores_every_unacked_message():
    client = Mock()
    client.rpoplpush.side_effect = ["eval-1", None]

    recovered = requeue_interrupted_jobs(client)

    assert recovered == 1
    assert client.rpoplpush.call_count == 2
    client.rpoplpush.assert_called_with(PROCESSING_QUEUE_NAME, QUEUE_NAME)


def test_completed_and_failed_eval_runs_are_terminal():
    assert is_terminal_status("completed")
    assert is_terminal_status("failed")
    assert not is_terminal_status("pending")
