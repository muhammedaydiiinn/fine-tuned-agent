"""Agent identity pool.

The agent name is a per-session runtime slot, not a memorised constant. The pool
mirrors exactly the 16 names used in the fine-tuning data (data/*.jsonl) so the
model, trained to use whatever name it is given, behaves consistently at runtime.
"""
import secrets

# (name, gender) — gender picks the German persona word.
AGENT_NAME_POOL: tuple[tuple[str, str], ...] = (
    ("Anna Weber", "f"),
    ("Julia Krüger", "f"),
    ("Laura Neumann", "f"),
    ("Sarah Müller", "f"),
    ("Nicole Fischer", "f"),
    ("Katrin Hoffmann", "f"),
    ("Sabine Becker", "f"),
    ("Lena Schäfer", "f"),
    ("Michael Wagner", "m"),
    ("Thomas Schneider", "m"),
    ("Stefan Bauer", "m"),
    ("Daniel Wolf", "m"),
    ("Markus Klein", "m"),
    ("Andreas Richter", "m"),
    ("Jan Neumann", "m"),
    ("Peter Hartmann", "m"),
)

PERSONA = {"f": "Sicherheitsberaterin", "m": "Sicherheitsberater"}


def persona_for(gender: str) -> str:
    return PERSONA.get(gender, "Sicherheitsberaterin")


def pick_identity() -> dict[str, str]:
    """Choose a random agent identity for a new session."""
    name, gender = AGENT_NAME_POOL[secrets.randbelow(len(AGENT_NAME_POOL))]
    return {"name": name, "role": persona_for(gender)}


def role_for_name(name: str) -> str:
    """Persona word for a known pool name; defaults to the female form."""
    for n, g in AGENT_NAME_POOL:
        if n == name:
            return persona_for(g)
    return PERSONA["f"]
