"""Canonical system instruction — runtime and approved training rows."""
from pathlib import Path

from app.core.product_facts import format_for_prompt

_POLICY_DIR = Path(__file__).resolve().parent / "policy"
_INSTRUCTION_PATH = _POLICY_DIR / "system_instruction.txt"


def load_system_instruction() -> str:
    if _INSTRUCTION_PATH.is_file():
        return _INSTRUCTION_PATH.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Missing policy file: {_INSTRUCTION_PATH}")


def build_system_content() -> str:
    """Full system message for inference and approved training examples."""
    return load_system_instruction() + "\n\n" + format_for_prompt()
