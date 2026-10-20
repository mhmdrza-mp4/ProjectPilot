"""Fake COM-like objects for MSP unit tests.

win32com dynamic dispatch exposes COM objects as ordinary Python objects
with attributes, so ``types.SimpleNamespace`` objects with the same
attribute names (UniqueID, Name, Duration, ...) exercise the exact code
paths the real COM objects will.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FakeMspResource:
    UniqueID: int
    Name: str
    MaxUnits: float = 1.0


@dataclass
class FakeMspDependency:
    From: object  # FakeMspTask


@dataclass
class FakeMspTask:
    UniqueID: int
    Name: str
    Duration: float | None = None          # COM unit: minutes
    PercentComplete: float | None = None   # 0..100
    Start: datetime | None = None
    Finish: datetime | None = None
    Milestone: bool = False
    Deadline: datetime | None = None
    Summary: bool = False
    Active: bool = True
    TaskDependencies: list[FakeMspDependency] = field(default_factory=list)
    Resources: list[FakeMspResource] = field(default_factory=list)


@dataclass
class FakeMspProject:
    Name: str = "Example Project"
    MinutesPerDay: float = 480.0
    Tasks: list[FakeMspTask] = field(default_factory=list)
    Resources: list[FakeMspResource] = field(default_factory=list)


Y2026 = datetime(2026, 6, 1, 8, 0)
