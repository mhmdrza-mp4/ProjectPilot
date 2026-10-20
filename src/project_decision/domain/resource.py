"""Resource concept: a person, equipment, or material pool that tasks need.

Capacity/availability is kept deliberately simple: the planner states how
many units of the resource are available (e.g. a person = 1 unit, a team
or machine pool = several units). Calendar-specific availability
(nonworking days, partial shifts) is left to the future Microsoft Project
integration layer.
"""

from __future__ import annotations

from dataclasses import dataclass

_DELTA_BELOW_ZERO = "capacity must be >= 0"


@dataclass
class Resource:
    """A resource that tasks can be assigned to."""

    id: str
    name: str
    capacity_units: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Resource.id must not be empty")
        if not self.name:
            raise ValueError("Resource.name must not be empty")
        if self.capacity_units < 0:
            raise ValueError(_DELTA_BELOW_ZERO)
