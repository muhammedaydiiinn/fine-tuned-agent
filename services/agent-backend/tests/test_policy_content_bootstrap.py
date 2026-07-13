"""Bootstrap seeding for policy_content — DB-backed, skips when no DB is available.

Restores any row it mutates so it never leaks edited content into the tests that
read canned answers from the same DB (guardrails / response_repair / review_compiler).
"""
import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy.exc import OperationalError

from app.core import content_store


class BootstrapPolicyContentTests(TestCase):
    def setUp(self):
        from app.db import SessionLocal, _bootstrap_policy_content
        from app.models import PolicyContent, PolicyContentHistory

        try:
            from app.db import engine

            PolicyContent.__table__.create(bind=engine, checkfirst=True)
            PolicyContentHistory.__table__.create(bind=engine, checkfirst=True)
        except OperationalError:
            self.skipTest("Postgres not available")

        _bootstrap_policy_content()
        # Snapshot the row we mutate so we can restore it in tearDown.
        db = SessionLocal()
        try:
            row = (
                db.query(PolicyContent)
                .filter(PolicyContent.section == "canned_answers")
                .first()
            )
            self._orig_value = dict(row.value_json or {})
            self._orig_by = row.updated_by
        finally:
            db.close()

    def tearDown(self):
        from app.db import SessionLocal
        from app.models import PolicyContent

        db = SessionLocal()
        try:
            row = (
                db.query(PolicyContent)
                .filter(PolicyContent.section == "canned_answers")
                .first()
            )
            if row is not None:
                row.value_json = self._orig_value
                row.updated_by = self._orig_by
                db.commit()
        finally:
            db.close()
        content_store.invalidate()

    def test_bootstrap_is_idempotent_and_seeds_every_section(self):
        from app.db import SessionLocal, _bootstrap_policy_content
        from app.models import PolicyContent

        db = SessionLocal()
        try:
            rows = {r.section: r for r in db.query(PolicyContent).all()}
            for section in content_store.SECTIONS:
                self.assertIn(section, rows)

            # A prior (panel) edit must survive re-bootstrap untouched.
            rows["canned_answers"].value_json = {"price": "MANUAL EDIT — keep me"}
            rows["canned_answers"].updated_by = "TESTMARK"
            db.commit()
        finally:
            db.close()

        _bootstrap_policy_content()  # run again

        db = SessionLocal()
        try:
            again = (
                db.query(PolicyContent)
                .filter(PolicyContent.section == "canned_answers")
                .first()
            )
            self.assertEqual(again.updated_by, "TESTMARK")
            self.assertEqual(again.value_json, {"price": "MANUAL EDIT — keep me"})
            # Exactly one row per section (unique constraint + skip-existing logic).
            counts = {}
            for r in db.query(PolicyContent).all():
                counts[r.section] = counts.get(r.section, 0) + 1
            for section in content_store.SECTIONS:
                self.assertEqual(counts.get(section), 1)
        finally:
            db.close()
