"""Tests for the content editor's form → value_json parsing."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from starlette.datastructures import FormData

from app.routes.content import _build_value


def test_system_instruction_trims_and_wraps():
    form = FormData([("text", "  Be Anna.  ")])
    assert _build_value(form, "system_instruction") == {"text": "Be Anna."}


def test_empty_system_instruction_is_rejected():
    form = FormData([("text", "   ")])
    assert _build_value(form, "system_instruction") is None


def test_product_facts_collects_fixed_keys():
    form = FormData([("fact__monthly_price", "9,99 Euro"), ("fact__trial_period", "14 Tage")])
    value = _build_value(form, "product_facts")
    assert value["monthly_price"] == "9,99 Euro"
    assert value["trial_period"] == "14 Tage"
    # Missing fields become empty strings (store falls back per-key at read time).
    assert value["app_stores"] == ""


def test_pdf_rules_split_by_line_and_stripped():
    form = FormData([("rules", "  Rule one \n\n Rule two \n")])
    assert _build_value(form, "pdf_rules") == {"rules": ["Rule one", "Rule two"]}


def test_objection_faq_zips_triggers_and_answers():
    form = FormData([
        ("trigger", "Zu teuer"), ("answer", "Testphase betonen."),
        ("trigger", ""), ("answer", "waise"),          # blank trigger dropped
        ("trigger", "Kein Interesse"), ("answer", "Nachfragen."),
    ])
    value = _build_value(form, "objection_faq")
    assert value == {"items": [
        {"trigger": "Zu teuer", "answer": "Testphase betonen."},
        {"trigger": "Kein Interesse", "answer": "Nachfragen."},
    ]}


def test_canned_answers_collects_fixed_keys():
    form = FormData([("canned__price", "Neuer Preis."), ("canned__security", "Sicher.")])
    value = _build_value(form, "canned_answers")
    assert value["price"] == "Neuer Preis."
    assert value["security"] == "Sicher."
