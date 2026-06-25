from app.core.product_facts import PRICE_TEMPLATE, SECURITY_TEMPLATE
from app.core.review_compiler import compile_instruction


def test_price_instruction_uses_authoritative_product_fact_template():
    result = compile_instruction(
        "Burada fiyat sormuş, önce 14 gün ücretsiz demeli",
        agent_response="Das ist günstig.",
        current_next_action="present_offer",
    )

    assert result.matched is True
    assert result.correction_type == "product_fact_correction"
    assert result.corrected_agent_response == PRICE_TEMPLATE
    assert result.corrected_next_action == "explain_price"


def test_security_instruction_uses_authoritative_security_template():
    result = compile_instruction("Linkin güvenli olduğunu söyle")

    assert result.matched is True
    assert result.corrected_agent_response == SECURITY_TEMPLATE
    assert result.corrected_next_action == "address_security"


def test_missing_identity_step_is_structured():
    result = compile_instruction("Önce müşterinin kimliğini doğrula")

    assert result.correction_type == "missing_step"
    assert result.corrected_next_action == "confirm_identity"
    assert "richtigen Person" in result.corrected_agent_response


def test_wrong_next_action_is_mapped_without_rewriting_response():
    result = compile_instruction(
        "Next action yanlış, görüşmeyi kapat",
        agent_response="Ich verstehe.",
        current_next_action="present_offer",
    )

    assert result.correction_type == "wrong_next_action"
    assert result.corrected_agent_response == "Ich verstehe."
    assert result.corrected_next_action == "close_call"


def test_tone_instruction_is_deterministic():
    result = compile_instruction(
        "Daha kısa ve kibar cevap ver",
        agent_response="Das ist der erste Satz. Das ist der zweite Satz.",
        current_next_action="present_offer",
    )

    assert result.correction_type == "tone_correction"
    assert result.corrected_agent_response == "Gern. Das ist der erste Satz."


def test_unknown_instruction_does_not_create_unsafe_guess():
    result = compile_instruction(
        "Bunu bir şekilde düzelt",
        agent_response="Original",
        current_next_action="present_offer",
    )

    assert result.matched is False
    assert result.correction_type == ""
    assert result.corrected_agent_response == "Original"
