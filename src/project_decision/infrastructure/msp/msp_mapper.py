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
- COM durations (``Duration``) and slack (``TotalSlack``) are given in
  MINUTES; convert with ``duration_minutes_to_days`` /
  ``optional_total_slack_days``.
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


def optional_total_slack_days(total_slack: Any, minutes_per_day: float = 480.0) -> float | None:
    """Convert COM ``TotalSlack`` (minutes) into working days, or None.

    Unlike ``Duration``, a genuinely missing/unreadable TotalSlack maps
    to ``None`` (not to zero): zero slack is a meaningful, highly
    significant value (a critical task), so absence must stay
    distinguishable. Negative slack is valid — MSP reports it for
    activities that are already late — and is preserved, never clamped.
    """
    if total_slack is None or not minutes_per_day:
        return None
    try:
        minutes = float(total_slack)
    except (TypeError, ValueError):
        return None
    return minutes / float(minutes_per_day)


def optional_critical(critical: Any) -> bool | None:
    """Normalize MSP's ``Critical`` flag; None when missing/unreadable.

    COM exposes booleans as -1/0; MSP may also deliver 'NA'-style
    placeholders. Anything that is not a definite truth value maps to
    ``None`` instead of a guessed flag.
    """
    if critical is None:
        return None
    try:
        if isinstance(critical, bool):
            return critical
        return bool(int(critical))
    except (TypeError, ValueError):
        return None


def optional_notes(notes: Any) -> str | None:
    """Read MSP task ``Notes``; None when absent, empty, or unreadable.

    MSP returns an empty string for tasks without notes; empty and
    whitespace-only text carry no information, so both stay ``None``.
    """
    if notes is None:
        return None
    text = str(notes).strip()
    return text or None


# -- record selection ----------------------------------------------------------


def optional_date(msp_record: Any, property_name: str) -> date | None:
    """Read an optional date property; None when missing or unreadable.

    Used for project-level dates (Start, Finish, StatusDate,
    BaselineStart, BaselineFinish). Microsoft Project reports unset
    values in inconsistent ways (None, empty variants, or 'NA'); all of
    them become None here — never a guessed date.
    """
    try:
        return to_date(_get(msp_record, property_name))
    except MspMappingError:
        return None


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
        total_slack_days=optional_total_slack_days(
            _get(msp_task, "TotalSlack"), minutes_per_day
        ),
        is_critical=optional_critical(_get(msp_task, "Critical")),
        notes=optional_notes(_get(msp_task, "Notes")),
        # bool(...) keeps the same truthiness convention as
        # is_mappable_task: the Summary row check treats any truthy
        # value (including an 'NA' placeholder) as a summary row.
        is_summary=bool(_get(msp_task, "Summary", False)),
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

    ``Task.TaskDependencies`` is a BIDIRECTIONAL collection: it contains
    every link the task participates in, both as the successor (incoming
    links) and as the predecessor (outgoing links — where ``From`` is
    the task itself). Only links whose ``To`` is this task carry a
    predecessor; outgoing links are skipped so that a task never
    reports itself as its own predecessor. This direction is verified
    against real COM data (Step 14): for task 3 the collection returned
    one incoming (From=2) and two outgoing (From=3) links.

    All four link types (FS/SS/SF/FF) share From→To direction, so the
    ``To`` check is type-independent. Tasks skipped by
    ``is_mappable_task`` (e.g. summary rows) also appear here; the
    adapter removes those references once it knows which tasks were
    mapped.
    """
    own_id = _get(msp_task, "UniqueID")
    ids: list[str] = []
    for dependency in _iter(_get(msp_task, "TaskDependencies")):
        target = _get(dependency, "To")
        if own_id is not None and _get(target, "UniqueID") != own_id:
            continue
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
