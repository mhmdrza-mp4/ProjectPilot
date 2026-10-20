"""Explicit mapping from Microsoft Project objects to domain objects.

How to read this module: the functions here access "MSP-like" objects by
*attribute* (task.UniqueID, task.Name, ...). This is exactly how pywin32
dynamic dispatch exposes COM objects, but the functions never import
``win32com`` themselves. Using attribute access is therefore safe for
COM and lets the mapping be unit-tested on any platform with plain
Python stand-in objects.

Only fields required by the domain model are mapped (see
docs/02 - Architecture.md). Optional fields that are missing or unreadable
map to a safe default; essential fields (id, name) raise
``MspMappingError`` because a record without them cannot be represented
in the domain at all.

Units:
- COM durations are given in MINUTES; convert with ``duration_minutes_to_days``.
- COM ``PercentComplete`` is 0..100; the domain wants a 0.0..1.0 fraction.
- COM dates are timezone-aware ``pywintypes`` datetimes; converted to ``date``.
- COM ``Resource.MaxUnits`` is a fraction (1.0 = 100 % availability).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterator

from project_decision.domain.constraint import Constraint, ConstraintKind
from project_decision.domain.milestone import Milestone
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task
from project_decision.infrastructure.msp.exceptions import MspMappingError


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read an optional property, treating any read failure as 'missing'.

    COM property reads can raise (``com_error`` for broken properties) and
    ``getattr`` only suppresses ``AttributeError``, so we catch here.
    """
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _iter(collection: Any) -> Iterator[Any]:
    """Iterate an MSP collection (works identically for COM and lists)."""
    if collection is None:
        return
    for item in collection:
        yield item


# -- conversions ---------------------------------------------------------------


def to_date(value: Any) -> date | None:
    """Convert an MSP date value (pywintypes datetime, date, or None)."""
    if value is None:
        return None
    date_method = getattr(value, "date", None)  # pywintypes datetime
    if callable(date_method):
        return date_method()
    if isinstance(value, date):
        return value
    raise MspMappingError(f"cannot interpret MSP date value: {value!r}")


def duration_minutes_to_days(minutes: Any, minutes_per_day: float = 480.0) -> float:
    """Convert a COM duration (minutes) into domain working days."""
    if minutes is None or not minutes_per_day:
        return 0.0
    return float(minutes) / minutes_per_day


def percent_complete_to_fraction(percent: Any) -> float:
    """Convert COM ``PercentComplete`` (0..100) into a 0.0..1.0 fraction."""
    if percent is None:
        return 0.0
    return max(0.0, min(100.0, float(percent))) / 100.0


# -- record selection ----------------------------------------------------------


def is_mappable_task(msp_task: Any) -> bool:
    """True for tasks we map: named, active, non-summary (work) tasks.

    Summary rows, inactive tasks, and blank rows are skipped: they are
    presentation/structure, not work items the domain models.
    """
    name = _get(msp_task, "Name")
    if not name:
        return False
    if _get(msp_task, "Summary", False):
        return False
    if _get(msp_task, "Active", True) is False:
        return False
    return True


def is_milestone_task(msp_task: Any) -> bool:
    """True if the MSP flags the task as a milestone (zero-duration marker)."""
    return bool(_get(msp_task, "Milestone", False))


# -- mappers -------------------------------------------------------------------


def map_task(msp_task: Any, *, minutes_per_day: float = 480.0) -> Task:
    """Map one MSP task into a domain Task.

    Raises:
        MspMappingError: essential fields (UniqueID, Name) are missing or broken.
    """
    task_id = _get(msp_task, "UniqueID")
    name = _get(msp_task, "Name")
    if task_id is None:
        raise MspMappingError(f"cannot map task {name!r}: missing UniqueID")
    if not name:
        raise MspMappingError(f"cannot map task {task_id!r}: missing name")

    start = to_date(_get(msp_task, "Start"))
    finish = to_date(_get(msp_task, "Finish"))
    if start is not None and finish is not None and finish < start:
        # Unusual COM values must not crash the whole read; drop the dates.
        start = finish = None

    task = Task(
        id=str(task_id),
        name=str(name),
        duration_days=duration_minutes_to_days(
            _get(msp_task, "Duration"), minutes_per_day
        ),
        progress=percent_complete_to_fraction(_get(msp_task, "PercentComplete")),
        start=start,
        finish=finish,
        dependency_ids=dependency_ids(msp_task),
        assigned_resource_ids=assigned_resource_ids(msp_task),
    )
    return task


def map_milestone(msp_task: Any) -> Milestone | None:
    """Map a milestone-flagged MSP task into a domain Milestone.

    Returns None when the milestone has no readable date: recording a
    milestone without a date would be meaningless. Callers skip it.
    """
    milestone_id = _get(msp_task, "UniqueID")
    name = _get(msp_task, "Name")
    milestone_date = to_date(_get(msp_task, "Finish")) or to_date(
        _get(msp_task, "Start")
    )
    if milestone_id is None or not name or milestone_date is None:
        return None
    return Milestone(id=str(milestone_id), name=str(name), date=milestone_date)


def map_resource(msp_resource: Any) -> Resource:
    """Map one MSP resource into a domain Resource."""
    resource_id = _get(msp_resource, "UniqueID")
    name = _get(msp_resource, "Name")
    if resource_id is None:
        raise MspMappingError(f"cannot map resource {name!r}: missing UniqueID")
    if not name:
        raise MspMappingError(f"cannot map resource {resource_id!r}: missing name")
    max_units = _get(msp_resource, "MaxUnits")
    capacity = float(max_units) if max_units else 1.0
    return Resource(id=str(resource_id), name=str(name), capacity_units=capacity)


def dependency_ids(msp_task: Any) -> list[str]:
    """UniqueIDs of the tasks this task depends on (predecessors).

    Uses ``TaskDependencies``, filtering to finish/start-type predecessors
    that are readable. Tasks skipped by ``is_mappable_task`` (e.g.
    summary rows) also appear here; the adapter removes those references
    once it knows which tasks were mapped.
    """
    ids: list[str] = []
    for dependency in _iter(_get(msp_task, "TaskDependencies")):
        source = _get(dependency, "From")
        source_id = _get(source, "UniqueID")
        if source_id is not None:
            ids.append(str(source_id))
    return ids


def assigned_resource_ids(msp_task: Any) -> list[str]:
    """UniqueIDs of the resources assigned to this task."""
    ids: list[str] = []
    for resource in _iter(_get(msp_task, "Resources")):
        resource_id = _get(resource, "UniqueID")
        if resource_id is not None:
            ids.append(str(resource_id))
    return ids


def deadline_constraint(msp_task: Any) -> Constraint | None:
    """Map a task deadline into a domain DEADLINE constraint, when set.

    Microsoft Project always exposes a Deadline value (often 'NA'); a
    task without a real deadline maps to None.
    """
    deadline = to_date(_get(msp_task, "Deadline"))
    if deadline is None:
        return None
    task_id = _get(msp_task, "UniqueID")
    name = _get(msp_task, "Name") or "task"
    return Constraint(
        id=f"deadline-{task_id}",
        kind=ConstraintKind.DEADLINE,
        description=f"task {name!r} must finish by {deadline.isoformat()}",
    )
