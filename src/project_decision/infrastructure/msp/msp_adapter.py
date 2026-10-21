"""The MSP adapter: turns Microsoft Project data into domain objects.

    Microsoft Project (COM)
        ↓   MspComClient (raw COM access)
    raw MSP objects
        ↓   msp_mapper (explicit field mapping)
    domain objects (Project / ProjectState)

Only this module and the rest of ``infrastructure/msp`` know COM exists.
Callers receive plain domain objects:

    client = MspComClient()
    client.connect()
    try:
        adapter = MspProjectAdapter(client)
        project = adapter.read_active_project()
    finally:
        client.disconnect()

Read-only guarantee: nothing in the pipeline writes to Microsoft Project.
The mapping can omit in-memory references to rows it could not map
(summary/inactive tasks, unmapped resources); those omissions are counted
and exposed as capture warnings - never invented or silently hidden.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Iterator

from project_decision.domain.constraint import Constraint
from project_decision.domain.milestone import Milestone
from project_decision.domain.project import Project
from project_decision.domain.project_state import ProjectState
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task
from project_decision.infrastructure.msp.com_client import MspComClient
from project_decision.infrastructure.msp.exceptions import MspMappingError, MspReadError
from project_decision.infrastructure.msp import msp_mapper as mapper


def _iter(collection: object) -> Iterator[object]:
    if collection is None:
        return iter(())
    return iter(collection)


class MspProjectAdapter:
    """Reads the active Microsoft Project into domain objects (read-only)."""

    def __init__(self, com_client: MspComClient) -> None:
        self._com = com_client
        self._last_capture_warnings: tuple[str, ...] = ()

    @property
    def last_capture_warnings(self) -> tuple[str, ...]:
        """Non-fatal omissions from the most recent read (empty = complete)."""
        return self._last_capture_warnings

    def read_active_project(self) -> Project:
        """Read the project currently active in Microsoft Project."""
        msp_project = self._com.get_active_project()
        minutes_per_day = self._com.get_minutes_per_day()

        name = self._read_name(msp_project)
        tasks, milestones = self._read_tasks(msp_project, minutes_per_day)
        resources = self._read_resources(msp_project)
        constraints = self._read_deadline_constraints(msp_project)
        dates = self._read_project_dates(msp_project)

        tasks, warnings = _drop_references_to_unmapped(
            tasks, {r.id for r in resources}
        )
        self._last_capture_warnings = warnings
        return Project(
            id=name or "msp-project",
            name=name or "Unnamed Microsoft Project",
            tasks=tasks,
            resources=resources,
            milestones=milestones,
            constraints=constraints,
            **dates,
        )

    def read_active_project_state(
        self, captured_at: datetime | None = None
    ) -> ProjectState:
        """Read the active project as an immutable state snapshot.

        The snapshot copies the project content, so later edits in
        Microsoft Project cannot change the returned state.
        """
        if captured_at is None:
            captured_at = datetime.now()
        project = self.read_active_project()
        state = project.capture_state(captured_at)
        return replace(state, capture_warnings=self._last_capture_warnings)

    # -- internal ----------------------------------------------------------

    def _read_name(self, msp_project: object) -> str:
        try:
            name = getattr(msp_project, "Name", None)
        except Exception as exc:
            raise MspReadError("cannot read the project name") from exc
        return str(name) if name else ""

    def _read_project_dates(self, msp_project: object) -> dict[str, date | None]:
        """Read optional project-level schedule/baseline dates.

        Every entry that Microsoft Project leaves unset (or reports as
        unreadable/'NA') stays None — the value is represented as
        "not available", never invented.
        """
        return {
            "project_start": mapper.optional_date(msp_project, "Start"),
            "project_finish": mapper.optional_date(msp_project, "Finish"),
            "status_date": mapper.optional_date(msp_project, "StatusDate"),
            "baseline_start": mapper.optional_date(msp_project, "BaselineStart"),
            "baseline_finish": mapper.optional_date(msp_project, "BaselineFinish"),
        }

    def _read_tasks(
        self, msp_project: object, minutes_per_day: float
    ) -> tuple[list[Task], list[Milestone]]:
        try:
            collection = msp_project.Tasks
        except Exception as exc:
            raise MspReadError("cannot read the task list") from exc

        tasks: list[Task] = []
        milestones: list[Milestone] = []
        for raw in _iter(collection):
            if not mapper.is_mappable_task(raw):
                continue
            task = mapper.map_task(raw, minutes_per_day=minutes_per_day)
            tasks.append(task)
            if mapper.is_milestone_task(raw):
                milestone = mapper.map_milestone(raw)
                if milestone is not None:
                    milestones.append(milestone)
        return tasks, milestones

    def _read_resources(self, msp_project: object) -> list[Resource]:
        try:
            collection = msp_project.Resources
        except Exception as exc:
            raise MspReadError("cannot read the resource list") from exc

        resources: list[Resource] = []
        for raw in _iter(collection):
            # Resources may contain the 'null' placeholder row.
            if not _resource_name(raw):
                continue
            resources.append(mapper.map_resource(raw))
        return resources

    def _read_deadline_constraints(self, msp_project: object) -> list[Constraint]:
        constraints: list[Constraint] = []
        for raw in _iter(getattr(msp_project, "Tasks", None)):
            if not mapper.is_mappable_task(raw):
                continue
            try:
                constraint = mapper.deadline_constraint(raw)
            except MspMappingError:
                # A single unreadable deadline must not abort the whole read.
                continue
            if constraint is not None:
                constraints.append(constraint)
        return constraints


def _resource_name(raw: object) -> str | None:
    try:
        return getattr(raw, "Name", None)
    except Exception:
        return None


def _drop_references_to_unmapped(
    tasks: list[Task], resource_ids: set[str]
) -> tuple[list[Task], tuple[str, ...]]:
    """Remove dependency/assignment references to tasks we skipped.

    MSP may legitimately show predecessors on summary or inactive tasks;
    those rows are not mapped, so the domain would reject the references.
    Dropping them is a deliberate limitation, but it is never silent: the
    returned warnings count the references omitted, by kind, and the
    adapter exposes them (``last_capture_warnings``) plus attaches them to
    the captured ``ProjectState``.
    """
    task_ids = {task.id for task in tasks}
    dropped_dependencies = 0
    dropped_assignments = 0
    for task in tasks:
        dependencies = [d for d in task.dependency_ids if d in task_ids]
        assignments = [
            r for r in task.assigned_resource_ids if r in resource_ids
        ]
        dropped_dependencies += len(task.dependency_ids) - len(dependencies)
        dropped_assignments += len(task.assigned_resource_ids) - len(assignments)
        task.dependency_ids = dependencies
        task.assigned_resource_ids = assignments

    warnings: list[str] = []
    if dropped_dependencies:
        warnings.append(
            f"{dropped_dependencies} predecessor reference(s) pointed at "
            "tasks that could not be mapped (summary/inactive rows) and "
            "were omitted from the captured snapshot."
        )
    if dropped_assignments:
        warnings.append(
            f"{dropped_assignments} resource-assignment reference(s) pointed "
            "at resources that could not be mapped and were omitted from the "
            "captured snapshot."
        )
    return tasks, tuple(warnings)
