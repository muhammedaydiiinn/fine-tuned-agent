import pathlib
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.backend import AgentBackend, BackendError
from app.config import Settings


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://agent-backend:8010/agent-turn")
            response = httpx.Response(
                self.status_code,
                request=request,
                text=self.text or "error",
            )
            raise httpx.HTTPStatusError(
                "backend error",
                request=request,
                response=response,
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def request(self, method, url, headers=None, **kwargs):
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class AgentBackendCircuitBreakerTests(unittest.IsolatedAsyncioTestCase):
    def make_backend(self):
        settings = Settings(
            backend_circuit_breaker_failures=2,
            backend_circuit_breaker_reset_seconds=5.0,
        )
        return AgentBackend(settings)

    async def test_circuit_breaker_opens_after_threshold_request_errors(self):
        backend = self.make_backend()
        failing_client = _FakeAsyncClient(
            [
                httpx.ConnectError("down"),
                httpx.ConnectError("still down"),
            ]
        )

        with patch("app.backend.httpx.AsyncClient", return_value=failing_client):
            with self.assertRaises(BackendError):
                await backend.agent_turn("s1", "Hallo")
            with self.assertRaises(BackendError):
                await backend.agent_turn("s1", "Hallo")

        with self.assertRaisesRegex(BackendError, "circuit breaker is open"):
            await backend.agent_turn("s1", "Hallo")

    async def test_success_resets_failure_counter(self):
        backend = self.make_backend()
        first_client = _FakeAsyncClient([httpx.ConnectError("down")])
        second_client = _FakeAsyncClient([_FakeResponse(payload={"ok": True})])

        with patch("app.backend.httpx.AsyncClient", return_value=first_client):
            with self.assertRaises(BackendError):
                await backend.create_session("s1")

        with patch("app.backend.httpx.AsyncClient", return_value=second_client):
            result = await backend.create_session("s1")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(backend._breaker.consecutive_failures, 0)

    async def test_half_open_allows_request_after_cooldown(self):
        backend = self.make_backend()
        backend._breaker.consecutive_failures = 2
        backend._breaker.opened_until = 101.0
        ok_client = _FakeAsyncClient([_FakeResponse(payload={"turn_id": 1})])

        with patch.object(backend, "_now", side_effect=[100.0, 102.0]):
            with self.assertRaisesRegex(BackendError, "circuit breaker is open"):
                await backend.agent_turn("s1", "Hallo")
            with patch("app.backend.httpx.AsyncClient", return_value=ok_client):
                result = await backend.agent_turn("s1", "Hallo")

        self.assertEqual(result, {"turn_id": 1})
        self.assertEqual(backend._breaker.opened_until, 0.0)

    async def test_server_errors_also_trip_breaker(self):
        backend = self.make_backend()
        failing_client = _FakeAsyncClient(
            [
                _FakeResponse(status_code=503, text="unavailable"),
                _FakeResponse(status_code=503, text="unavailable"),
            ]
        )

        with patch("app.backend.httpx.AsyncClient", return_value=failing_client):
            with self.assertRaisesRegex(BackendError, "503"):
                await backend.agent_turn("s1", "Hallo")
            with self.assertRaisesRegex(BackendError, "503"):
                await backend.agent_turn("s1", "Hallo")

        with self.assertRaisesRegex(BackendError, "circuit breaker is open"):
            await backend.agent_turn("s1", "Hallo")
