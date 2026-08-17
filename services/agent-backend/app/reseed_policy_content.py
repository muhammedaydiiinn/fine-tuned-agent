"""Reseed panel-editable policy_content sections from the current code defaults.

Unlike the startup bootstrap (which only fills MISSING sections), this OVERWRITES
the given sections so a prompt/facts change in code takes effect on a DB that was
already seeded. Run inside the agent-backend environment (needs DB access):

    python -m app.reseed_policy_content                     # default sections
    python -m app.reseed_policy_content system_instruction  # specific section(s)

In Docker:

    docker compose ... exec agent-backend python -m app.reseed_policy_content

Panel edits to these rows will be replaced — reseed only the sections you intend to.
"""
import sys

from app.core import content_store
from app.db import SessionLocal
from app.models import PolicyContent

DEFAULT_SECTIONS = ("system_instruction", "product_facts")


def main(sections: tuple[str, ...]) -> None:
    db = SessionLocal()
    try:
        for section in sections:
            value = content_store.default_value(section)
            row = db.query(PolicyContent).filter(PolicyContent.section == section).first()
            if row is None:
                db.add(PolicyContent(section=section, value_json=value, updated_by="reseed"))
                print(f"inserted: {section}")
            else:
                row.value_json = value
                row.updated_by = "reseed"
                db.add(row)
                print(f"updated:  {section}")
        db.commit()
        content_store.invalidate()
        print("done — cache invalidated")
    finally:
        db.close()


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or DEFAULT_SECTIONS)
