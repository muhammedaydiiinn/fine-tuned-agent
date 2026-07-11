import pathlib
import sys
import types
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import autotrain


class _FakeScalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeDB:
    """Advisory-lock aware fake: execute() returns the lock verdict; close() no-op."""
    def __init__(self, lock=True):
        self._lock = lock
        self.closed = False

    def execute(self, *a, **k):
        return _FakeScalar(self._lock)

    def close(self):
        self.closed = True


def _patches(*, enabled=True, threshold=3, busy=False, batch=5, lock=True, core=None):
    core = core or mock.MagicMock(return_value=types.SimpleNamespace(id=42))
    return [
        mock.patch.object(autotrain.settings, "auto_train_enabled", enabled),
        mock.patch.object(autotrain.settings, "auto_train_threshold", threshold),
        mock.patch.object(autotrain, "SessionLocal", lambda: _FakeDB(lock=lock)),
        mock.patch.object(autotrain, "_pipeline_busy", return_value=busy),
        mock.patch.object(autotrain, "_count_active_batch", return_value=batch),
        mock.patch("app.routes.training.create_training_job_core", core),
    ], core


class AutotrainTickTests(TestCase):
    def _run(self, **kw):
        patches, core = _patches(**kw)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return autotrain.run_tick(), core

    def test_disabled_short_circuits(self):
        with mock.patch.object(autotrain.settings, "auto_train_enabled", False):
            self.assertFalse(autotrain.run_tick())

    def test_triggers_at_threshold(self):
        fired, core = self._run(enabled=True, threshold=3, batch=5, busy=False, lock=True)
        self.assertTrue(fired)
        core.assert_called_once()
        # auto_training=True must be passed so the job is badged.
        self.assertTrue(core.call_args.kwargs.get("auto_training"))

    def test_skips_when_pipeline_busy(self):
        fired, core = self._run(busy=True)
        self.assertFalse(fired)
        core.assert_not_called()

    def test_skips_below_threshold(self):
        fired, core = self._run(threshold=30, batch=5)
        self.assertFalse(fired)
        core.assert_not_called()

    def test_skips_when_lock_not_acquired(self):
        fired, core = self._run(lock=False)
        self.assertFalse(fired)
        core.assert_not_called()
