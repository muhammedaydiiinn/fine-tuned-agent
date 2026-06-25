from app.review_compiler_types import resolve_correction_type


def test_compiler_correction_types_are_preserved():
    assert resolve_correction_type("product_fact_correction") == "product_fact_correction"
    assert resolve_correction_type("missing_step") == "missing_step"
    assert resolve_correction_type("wrong_next_action") == "wrong_next_action"
    assert resolve_correction_type("tone_correction") == "tone_correction"


def test_unknown_compiler_type_falls_back_to_manual_correction():
    assert resolve_correction_type("unsafe_custom_type") == "response_correction"
