"""Pure selection rules for the supervisor pipeline UI."""
from __future__ import annotations

from typing import Iterable, TypeVar

T = TypeVar("T")


def select_actionable_candidate(models: Iterable[T]) -> T | None:
    """Return the newest non-retired candidate model from an ordered iterable."""
    return next(
        (
            model
            for model in models
            if (getattr(model, "metadata_json", None) or {}).get("lifecycle_status")
            not in {"retired", "deployed"}
        ),
        None,
    )
