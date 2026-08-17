import json
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import livekit_tokens


def _fake_api(capture, *, fail=False):
    class FakeSendDataRequest:
        def __init__(self, **kw):
            capture.update(kw)

    class FakeRoom:
        async def send_data(self, req):
            if fail:
                raise RuntimeError("boom")

    class FakeLiveKitAPI:
        def __init__(self, *a):
            self.room = FakeRoom()

        async def aclose(self):
            pass

    return SimpleNamespace(LiveKitAPI=FakeLiveKitAPI, SendDataRequest=FakeSendDataRequest)


class PublishControlTests(unittest.TestCase):
    def test_publishes_command_to_control_topic(self):
        cap = {}
        with mock.patch.object(livekit_tokens, "_api", return_value=_fake_api(cap)):
            ok = livekit_tokens.publish_control(
                "room-1", {"action": "stop_agent", "action_id": "abc"}
            )
        self.assertTrue(ok)
        self.assertEqual(cap["room"], "room-1")
        self.assertEqual(cap["topic"], "voice.control")
        self.assertEqual(
            json.loads(cap["data"].decode("utf-8")),
            {"action": "stop_agent", "action_id": "abc"},
        )

    def test_delivery_failure_returns_false(self):
        cap = {}
        with mock.patch.object(livekit_tokens, "_api", return_value=_fake_api(cap, fail=True)):
            ok = livekit_tokens.publish_control("room-1", {"action": "stop_agent"})
        self.assertFalse(ok)
