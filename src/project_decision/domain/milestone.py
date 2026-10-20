"""Milestone concept: a zero-effort point in time the project must hit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Milestone:
    """A named date of importance in the project."""

    id: str
    name: str
    date: date

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Milestone.id must not be empty")
        if not self.name:
            raise ValueError("Milestone.name must not be empty")
