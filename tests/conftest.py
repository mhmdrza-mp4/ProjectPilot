"""Shared builders for domain tests, so each test file stays small."""

from datetime import date, datetime

from project_decision.domain import (
    Constraint,
    ConstraintKind,
    Decision,
    DecisionKind,
    Milestone,
    Project,
    Resource,
    Task,
)


def make_task(task_id: str = "T1", **overrides) -> Task:
    defaults = dict(id=task_id, name=f"Task {task_id}", duration_days=5.0)
    defaults.update(overrides)
    return Task(**defaults)


def make_resource(resource_id: str = "R1", **overrides) -> Resource:
    defaults = dict(id=resource_id, name=f"Resource {resource_id}")
    defaults.update(overrides)
    return Resource(**defaults)


def make_milestone(milestone_id: str = "M1", **overrides) -> Milestone:
    defaults = dict(id=milestone_id, name=f"Milestone {milestone_id}", date=date(2026, 6, 1))
    defaults.update(overrides)
    return Milestone(**defaults)


def make_constraint(constraint_id: str = "C1", **overrides) -> Constraint:
    defaults = dict(
        id=constraint_id,
        kind=ConstraintKind.DEADLINE,
        description="finish before 2026-06-30",
    )
    defaults.update(overrides)
    return Constraint(**defaults)


def make_decision(decision_id: str = "D1", **overrides) -> Decision:
    defaults = dict(
        id=decision_id,
        name=f"Alternative {decision_id}",
        kind=DecisionKind.ADD_RESOURCE_CAPACITY,
        description="add a second engineer to T1",
    )
    defaults.update(overrides)
    return Decision(**defaults)


def make_project(project_id: str = "P1", **overrides) -> Project:
    task = overrides.pop("task", None) or make_task()
    resource = overrides.pop("resource", None) or make_resource()
    defaults = dict(
        id=project_id,
        name="Demo Project",
        tasks=[task],
        resources=[resource],
        milestones=[],
        constraints=[],
    )
    defaults.update(overrides)
    return Project(**defaults)


CAPTURED_AT = datetime(2026, 10, 20, 9, 0)
