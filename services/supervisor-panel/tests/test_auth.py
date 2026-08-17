import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import auth


class CheckCredentialsTests(unittest.TestCase):
    def _creds(self, user, pw):
        return mock.patch.multiple(auth.settings, admin_user=user, admin_password=pw)

    def test_non_ascii_password_matches_without_typeerror(self):
        # Regression: hmac.compare_digest on str rejects non-ASCII → 500 on login.
        with self._creds("admin", "Şifreöü_123"):
            self.assertTrue(auth.check_credentials("admin", "Şifreöü_123"))

    def test_non_ascii_password_mismatch_is_false(self):
        with self._creds("admin", "Şifreöü_123"):
            self.assertFalse(auth.check_credentials("admin", "falsch"))

    def test_ascii_credentials_still_work(self):
        with self._creds("admin", "secret"):
            self.assertTrue(auth.check_credentials("admin", "secret"))
            self.assertFalse(auth.check_credentials("admin", "nope"))
            self.assertFalse(auth.check_credentials("wrong", "secret"))
