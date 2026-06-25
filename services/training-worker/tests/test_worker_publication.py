from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jobs.model_registration import commit_model_version


def test_model_commit_finalizes_candidate_publication():
    db = Mock()
    model = SimpleNamespace(id=42)
    publication = Mock()

    model_id = commit_model_version(db, model, publication)

    assert model_id == 42
    db.add.assert_called_once_with(model)
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    publication.finalize.assert_called_once_with()
    publication.rollback.assert_not_called()
    db.refresh.assert_called_once_with(model)


def test_model_commit_failure_restores_candidate_publication():
    db = Mock()
    db.commit.side_effect = RuntimeError("database unavailable")
    model = SimpleNamespace(id=None)
    publication = Mock()

    with pytest.raises(RuntimeError, match="database unavailable"):
        commit_model_version(db, model, publication)

    db.rollback.assert_called_once_with()
    publication.rollback.assert_called_once_with()
    publication.finalize.assert_not_called()
    db.refresh.assert_not_called()
