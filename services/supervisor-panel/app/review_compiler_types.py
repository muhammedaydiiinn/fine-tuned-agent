"""Allowed correction types emitted by the deterministic review compiler."""

COMPILED_CORRECTION_TYPES = {
    "product_fact_correction",
    "missing_step",
    "wrong_next_action",
    "tone_correction",
}


def resolve_correction_type(compiled_type: str) -> str:
    return (
        compiled_type
        if compiled_type in COMPILED_CORRECTION_TYPES
        else "response_correction"
    )
