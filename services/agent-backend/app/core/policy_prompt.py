"""Canonical system instruction — runtime and approved training rows."""
from app.core.product_facts import SYSTEM_OUTPUT_CONTRACT, format_for_prompt


def load_system_instruction() -> str:
    """Live (panel-editable) sales script/persona, falling back to the policy file."""
    from app.core import content_store

    return content_store.system_instruction()


def build_system_content() -> str:
    """Full system message for inference and approved training examples.

    Human-editable sales script + the fixed JSON output contract (kept out of the
    panel) + the live product facts / rules / FAQ block.
    """
    return (
        load_system_instruction().rstrip()
        + "\n\n" + SYSTEM_OUTPUT_CONTRACT
        + "\n\n" + format_for_prompt()
    )
