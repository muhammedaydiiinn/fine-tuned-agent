import json
import pathlib
import sys
import unittest

from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ui_feedback import (
    FLASH_COOKIE_NAME,
    build_toast,
    load_toast,
    set_toast_cookie,
    toast_redirect,
)


class UIFeedbackTests(unittest.TestCase):
    def _request_with_cookie(self, value: str) -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"{FLASH_COOKIE_NAME}={value}".encode())],
        }
        return Request(scope)

    def test_build_toast_keeps_optional_fields(self) -> None:
        payload = build_toast(
            "Saved",
            kind="success",
            title="Review saved",
            duration_ms=2500,
        )
        self.assertEqual(
            payload,
            {
                "message": "Saved",
                "kind": "success",
                "title": "Review saved",
                "durationMs": 2500,
            },
        )

    def test_set_toast_cookie_writes_flash_cookie(self) -> None:
        response = set_toast_cookie(Response(), build_toast("Queued", kind="info"))
        cookie_header = response.headers["set-cookie"]
        self.assertIn(f"{FLASH_COOKIE_NAME}=", cookie_header)
        self.assertIn("Queued", cookie_header)

    def test_load_toast_reads_valid_cookie_payload(self) -> None:
        payload = {"message": "Started", "kind": "success"}
        request = self._request_with_cookie(json.dumps(payload))
        self.assertEqual(load_toast(request), payload)

    def test_load_toast_ignores_invalid_payload(self) -> None:
        request = self._request_with_cookie("not-json")
        self.assertIsNone(load_toast(request))

    def test_toast_redirect_sets_status_and_cookie(self) -> None:
        response = toast_redirect("/review", "Review saved", kind="success")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/review")
        self.assertIn(f"{FLASH_COOKIE_NAME}=", response.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()
