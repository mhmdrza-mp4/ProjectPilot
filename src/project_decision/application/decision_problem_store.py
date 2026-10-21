"""Local persistence and serialization for decision problems.

Problems are stored as one JSON file per problem in
``data/decision_problems/`` (relative to the working directory). This is
a deliberate, simple local solution — no database.

``problem_to_dict`` produces a stable, structured representation of the
problem (including the captured ``ProjectState``). It serves two
purposes:
- file persistence (``DecisionProblemFileStore``),
- a clean starting point for the future JEV API mapping, whose request
  schema is not yet known. When JEV's contract is confirmed, its
  mapping module will live in ``infrastructure`` and consume this
  representation or richer mapping functions — the domain stays pure.

Round-trip guarantee: ``problem_from_dict(problem_to_dict(p))`` rebuilds
an equivalent problem; all domain validation applies on the way back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from project_decision.domain.constraint import Constraint, ConstraintKind
from project_decision.domain.decision import Decision, DecisionKind
from project_decision.domain.decision_assumption import DecisionAssumption
from project_decision.domain.decision_problem import DecisionProblem
from project_decision.domain.evaluation_criterion import EvaluationCriterion
from project_decision.domain.milestone import Milestone
from project_decision.domain.project_state import ProjectState
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task

SCHEMA_VERSION = 1
DEFAULT_STORE_DIRECTORY = Path("data/decision_problems")


# -- dict -> small conversion helpers -------------------------------------------


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def _parse_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(value)


# -- ProjectState serialization --------------------------------------------------


def project_state_to_dict(state: ProjectState) -> dict[str, Any]:
    return {
        "project_id": state.project_id,
        "project_name": state.project_name,
        "captured_at": state.captured_at.isoformat(),
        "project_start": _iso_date(state.project_start),
        "project_finish": _iso_date(state.project_finish),
        "status_date": _iso_date(state.status_date),
        "baseline_start": _iso_date(state.baseline_start),
        "baseline_finish": _iso_date(state.baseline_finish),
        "capture_warnings": list(state.capture_warnings),
        "tasks": [
            {
                "id": task.id,
                "name": task.name,
                "duration_days": task.duration_days,
                "progress": task.progress,
                "start": _iso_date(task.start),
                "finish": _iso_date(task.finish),
                "dependency_ids": list(task.dependency_ids),
                "assigned_resource_ids": list(task.assigned_resource_ids),
                "total_slack_days": task.total_slack_days,
                "is_critical": task.is_critical,
                "notes": task.notes,
                "is_summary": task.is_summary,
            }
            for task in state.tasks
        ],
        "resources": [
            {
                "id": resource.id,
                "name": resource.name,
                "capacity_units": resource.capacity_units,
            }
            for resource in state.resources
        ],
        "milestones": [
            {"id": milestone.id, "name": milestone.name, "date": _iso_date(milestone.date)}
            for milestone in state.milestones
        ],
        "constraints": [
            {
                "id": constraint.id,
                "kind": constraint.kind.value,
                "description": constraint.description,
            }
            for constraint in state.constraints
        ],
    }


def project_state_from_dict(data: dict[str, Any]) -> ProjectState:
    try:
        tasks = [
            Task(
                id=task["id"],
                name=task["name"],
                duration_days=task["duration_days"],
                progress=task["progress"],
                start=_parse_date(task.get("start")),
                finish=_parse_date(task.get("finish")),
                dependency_ids=list(task.get("dependency_ids", [])),
                assigned_resource_ids=list(task.get("assigned_resource_ids", [])),
                # Older saved snapshots predate these fields; .get keeps
                # them loadable, mapping to the domain's None = "not
                # available" (and is_summary to plain False).
                total_slack_days=task.get("total_slack_days"),
                is_critical=task.get("is_critical"),
                notes=task.get("notes"),
                is_summary=bool(task.get("is_summary", False)),
            )
            for task in data["tasks"]
        ]
        resources = [
            Resource(
                id=resource["id"],
                name=resource["name"],
                capacity_units=resource.get("capacity_units", 1.0),
            )
            for resource in data["resources"]
        ]
        milestones = [
            Milestone(id=m["id"], name=m["name"], date=_parse_date(m["date"]))
            for m in data["milestones"]
        ]
        constraints = [
            Constraint(
                id=c["id"], kind=ConstraintKind(c["kind"]), description=c["description"]
            )
            for c in data["constraints"]
        ]
        return ProjectState(
            project_id=data["project_id"],
            captured_at=_parse_datetime(data["captured_at"]),
            project_name=data.get("project_name", ""),
            tasks=tasks,
            resources=resources,
            milestones=milestones,
            constraints=constraints,
            project_start=_parse_date(data.get("project_start")),
            project_finish=_parse_date(data.get("project_finish")),
            status_date=_parse_date(data.get("status_date")),
            baseline_start=_parse_date(data.get("baseline_start")),
            baseline_finish=_parse_date(data.get("baseline_finish")),
            capture_warnings=tuple(data.get("capture_warnings", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"captured project state is not valid: {exc}") from exc


# -- DecisionProblem serialization ------------------------------------------------


def problem_to_dict(problem: DecisionProblem) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": problem.id,
        "title": problem.title,
        "description": problem.description,
        "objective": problem.objective,
        "context": problem.context,
        "project": (
            project_state_to_dict(problem.current_state)
            if problem.current_state is not None
            else None
        ),
        "alternatives": [
            {
                "id": alternative.id,
                "name": alternative.name,
                "kind": alternative.kind.value,
                "description": alternative.description,
            }
            for alternative in problem.alternatives
        ],
        "criteria": [
            {
                "id": criterion.id,
                "name": criterion.name,
                "description": criterion.description,
            }
            for criterion in problem.criteria
        ],
        "constraints": [
            {
                "id": constraint.id,
                "kind": constraint.kind.value,
                "description": constraint.description,
            }
            for constraint in problem.constraints
        ],
        "assumptions": [
            {"id": assumption.id, "description": assumption.description}
            for assumption in problem.assumptions
        ],
        "ready_for_evaluation": problem.is_ready_for_evaluation(),
        "evaluation_gaps": problem.evaluation_gaps(),
    }


def problem_from_dict(data: dict[str, Any]) -> DecisionProblem:
    try:
        alternatives = [
            Decision(
                id=a["id"],
                name=a["name"],
                kind=DecisionKind(a["kind"]),
                description=a["description"],
            )
            for a in data["alternatives"]
        ]
        criteria = [
            EvaluationCriterion(
                id=c["id"], name=c["name"], description=c["description"]
            )
            for c in data["criteria"]
        ]
        constraints = [
            Constraint(
                id=c["id"], kind=ConstraintKind(c["kind"]), description=c["description"]
            )
            for c in data["constraints"]
        ]
        assumptions = [
            DecisionAssumption(id=a["id"], description=a["description"])
            for a in data["assumptions"]
        ]
        return DecisionProblem(
            current_state=(
                project_state_from_dict(data["project"])
                if data.get("project") is not None
                else None
            ),
            objective=data["objective"],
            title=data["title"],
            description=data["description"],
            id=data["id"],
            alternatives=alternatives,
            criteria=criteria,
            constraints=constraints,
            assumptions=assumptions,
            context=data.get("context", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"decision problem data is not valid: {exc}") from exc


# -- file store -------------------------------------------------------------------


@dataclass(frozen=True)
class ProblemSummary:
    """Listing entry for the UI; unreadable files are reported, not hidden."""

    id: str
    title: str
    ready_for_evaluation: bool
    loadable: bool


class DecisionProblemFileStore:
    """Saves and loads decision problems as JSON files (one per problem)."""

    def __init__(self, directory: Path | str = DEFAULT_STORE_DIRECTORY) -> None:
        self._directory = Path(directory)

    def save(self, problem: DecisionProblem) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{problem.id}.json"
        path.write_text(
            json.dumps(problem_to_dict(problem), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def load(self, problem_id: str) -> DecisionProblem | None:
        path = self._directory / f"{problem_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"problem file {path.name} is not valid JSON: {exc}") from exc
        try:
            return problem_from_dict(data)
        except ValueError as exc:
            raise ValueError(f"problem file {path.name} is not a valid problem: {exc}") from exc

    def delete(self, problem_id: str) -> bool:
        path = self._directory / f"{problem_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_problems(self) -> list[ProblemSummary]:
        if not self._directory.exists():
            return []
        summaries: list[ProblemSummary] = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                problem = self.load(path.stem)
                summaries.append(
                    ProblemSummary(
                        id=problem.id,
                        title=problem.title,
                        ready_for_evaluation=problem.is_ready_for_evaluation(),
                        loadable=True,
                    )
                )
            except (ValueError, OSError):
                summaries.append(
                    ProblemSummary(
                        id=path.stem,
                        title="(file could not be read)",
                        ready_for_evaluation=False,
                        loadable=False,
                    )
                )
        return summaries
