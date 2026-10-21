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
    # Real COM shape: a link has BOTH ends — From is the predecessor,
    # To is the successor. Task.TaskDependencies contains a task's
    # incoming AND outgoing links (verified against real MSP, Step 14),
    # so the fakes model both ends too.
    From: object  # FakeMspTask
    To: object  # FakeMspTask


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
    TotalSlack: float | None = None        # COM unit: minutes; may be negative
    Critical: bool | None = None
    Notes: str | None = None
    TaskDependencies: list[FakeMspDependency] = field(default_factory=list)
    Resources: list[FakeMspResource] = field(default_factory=list)


@dataclass
class FakeMspProject:
    Name: str = "Example Project"
    MinutesPerDay: float = 480.0
    Tasks: list[FakeMspTask] = field(default_factory=list)
    Resources: list[FakeMspResource] = field(default_factory=list)
    Start: datetime | None = None
    Finish: datetime | None = None
    StatusDate: datetime | None = None
    BaselineStart: datetime | None = None
    BaselineFinish: datetime | None = None


Y2026 = datetime(2026, 6, 1, 8, 0)
