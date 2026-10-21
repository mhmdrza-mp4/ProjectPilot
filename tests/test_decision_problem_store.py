"""Tests for decision problem persistence (JSON files, no database)."""

import json
from dataclasses import replace
from datetime import date, datetime

import pytest

from project_decision.application.decision_problem_store import (
    DEFAULT_STORE_DIRECTORY,
    DecisionProblemFileStore,
    problem_from_dict,
    problem_to_dict,
)
from project_decision.domain import (
    Constraint,
    ConstraintKind,
    Decision,
    DecisionKind,
    DecisionProblem,
    EvaluationCriterion,
    ProjectState,
    Resource,
    Task,
)


def make_full_problem() -> DecisionProblem:
    state = ProjectState(
        project_id="Example",
        captured_at=datetime(2026, 10, 20, 9, 0),
        project_name="Example",
        tasks=[
            Task(
                id="1",
                name="Design",
                duration_days=2.0,
                progress=0.5,
                start=date(2026, 6, 1),
                finish=date(2026, 6, 3),
                dependency_ids=[],
                assigned_resource_ids=["101"],
                total_slack_days=0.5,
                is_critical=True,
                notes="handoff to QA after completion",
            )
        ],
        resources=[Resource(id="101", name="Alice", capacity_units=1.0)],
        project_start=date(2026, 6, 1),
        project_finish=date(2026, 6, 10),
        status_date=None,
        baseline_start=date(2026, 6, 1),
        baseline_finish=None,
    )
    return DecisionProblem(
        current_state=state,
        objective="reduce schedule delay",
        title="Schedule recovery",
        description="A potential schedule issue needs a control decision.",
        id="problem-1",
        alternatives=[
            Decision(
                id="D1",
                name="Add resources",
                kind=DecisionKind.ADD_RESOURCE_CAPACITY,
                description="add a second engineer to Design",
            ),
            Decision(
                id="D2",
                name="Resequence",
                kind=DecisionKind.RESEQUENCE_TASKS,
                description="move upstream rows ahead",
            ),
        ],
        criteria=[
            EvaluationCriterion(id="C1", name="Schedule impact", description="expected days gained")
        ],
        constraints=[
            Constraint(
                id="K1",
                kind=ConstraintKind.RESOURCE_CAPACITY,
                description="Design must stay within existing team capacity",
            )
        ],
        assumptions=[],
        context="",
    )


@pytest.fixture
def store(tmp_path) -> DecisionProblemFileStore:
    return DecisionProblemFileStore(tmp_path / "problems")


class TestSerialization:
    def test_round_trip_preserves_everything(self):
        problem = make_full_problem()

        restored = problem_from_dict(problem_to_dict(problem))

        assert restored.id == problem.id
        assert restored.title == problem.title
        assert restored.description == problem.description
        assert restored.objective == problem.objective
        assert restored.context == problem.context
        assert [a.name for a in restored.alternatives] == ["Add resources", "Resequence"]
        assert restored.alternatives[0].kind is DecisionKind.ADD_RESOURCE_CAPACITY
        assert restored.criteria[0].name == "Schedule impact"
        assert restored.constraints[0].kind is ConstraintKind.RESOURCE_CAPACITY
        assert restored.assumptions == []
        state = restored.current_state
        assert state.project_name == "Example"
        assert state.captured_at == problem.current_state.captured_at
        assert state.tasks[0].finish == date(2026, 6, 3)
        assert state.tasks[0].assigned_resource_ids == ["101"]
        assert state.tasks[0].total_slack_days == 0.5
        assert state.tasks[0].is_critical is True
        assert state.tasks[0].notes == "handoff to QA after completion"
        assert state.tasks[0].is_summary is False
        assert state.project_start == date(2026, 6, 1)
        assert state.baseline_finish is None  # unset values stay unset

    def test_capture_warnings_round_trip_and_old_snapshots_default_empty(self):
        problem = make_full_problem()
        problem = replace(
            problem,
            current_state=replace(
                problem.current_state,
                capture_warnings=("1 predecessor reference omitted",),
            ),
        )

        restored = problem_from_dict(problem_to_dict(problem))
        assert restored.current_state.capture_warnings == (
            "1 predecessor reference omitted",
        )

        # Older snapshots predate this diagnostic: absence means empty.
        data = problem_to_dict(make_full_problem())
        del data["project"]["capture_warnings"]
        assert problem_from_dict(data).current_state.capture_warnings == ()

    def test_older_snapshot_without_scheduling_fields_loads_as_none(self):
        # Snapshots saved before total_slack_days/is_critical/notes existed
        # must stay loadable; missing keys map to None = "not available",
        # and a missing is_summary maps to plain False.
        data = problem_to_dict(make_full_problem())
        for task in data["project"]["tasks"]:
            for key in ("total_slack_days", "is_critical", "notes", "is_summary"):
                del task[key]

        restored = problem_from_dict(data)

        task = restored.current_state.tasks[0]
        assert task.total_slack_days is None
        assert task.is_critical is None
        assert task.notes is None
        assert task.is_summary is False
        assert task.duration_days == 2.0  # untouched fields unchanged

    def test_problem_without_state_round_trips(self):
        problem = make_full_problem()
        problem = DecisionProblem(
            current_state=None,
            objective=problem.objective,
            title=problem.title,
            description=problem.description,
            id=problem.id,
            alternatives=problem.alternatives,
            criteria=problem.criteria,
        )
        restored = problem_from_dict(problem_to_dict(problem))
        assert restored.current_state is None

    def test_serialized_data_is_json_compatible(self):
        payload = json.dumps(problem_to_dict(make_full_problem()), ensure_ascii=False)
        assert json.loads(payload)["title"] == "Schedule recovery"

    def test_flag_is_honest_about_readiness(self):
        incomplete = make_full_problem()
        incomplete_dict = problem_to_dict(incomplete)
        assert incomplete_dict["ready_for_evaluation"] is True

    def test_gaps_reported_when_not_ready(self):
        data = problem_to_dict(make_full_problem())
        data["criteria"] = []
        data["alternatives"] = data["alternatives"][:1]

        parsed = problem_from_dict(data)

        assert parsed.is_ready_for_evaluation() is False
        assert parsed.evaluation_gaps()

    def test_invalid_kind_value_rejected(self):
        data = problem_to_dict(make_full_problem())
        data["alternatives"][0]["kind"] = "travelpedia"
        with pytest.raises(ValueError, match="not valid"):
            problem_from_dict(data)

    def test_missing_required_field_rejected(self):
        data = problem_to_dict(make_full_problem())
        del data["title"]
        with pytest.raises(ValueError, match="not valid"):
            problem_from_dict(data)

    def test_corrupt_state_rejected(self):
        data = problem_to_dict(make_full_problem())
        data["project"]["tasks"][0]["progress"] = 5.0  # outside 0..1
        with pytest.raises(ValueError, match="not valid"):
            problem_from_dict(data)

    def test_default_store_directory_is_a_local_folder(self):
        from pathlib import Path

        assert DEFAULT_STORE_DIRECTORY == Path("data/decision_problems")


class TestFileStore:
    def test_save_and_load_round_trip(self, store, tmp_path):
        problem = make_full_problem()

        path = store.save(problem)
        loaded = store.load(problem.id)

        assert path.name == "problem-1.json"
        assert path.parent == tmp_path / "problems"
        assert loaded.id == problem.id
        assert loaded.current_state.tasks == problem.current_state.tasks
        assert loaded.is_ready_for_evaluation()

    def test_load_missing_problem_returns_none(self, store):
        assert store.load("does-not-exist") is None

    def test_directory_created_on_save(self, store, tmp_path):
        assert not (tmp_path / "problems").exists()
        store.save(make_full_problem())
        assert (tmp_path / "problems" / "problem-1.json").exists()

    def test_list_problems_reports_ready_flag(self, store):
        store.save(make_full_problem())
        summaries = store.list_problems()

        assert len(summaries) == 1
        assert summaries[0].id == "problem-1"
        assert summaries[0].title == "Schedule recovery"
        assert summaries[0].ready_for_evaluation is True
        assert summaries[0].loadable is True

    def test_corrupt_file_is_reported_not_crashed(self, store, tmp_path):
        problems_dir = tmp_path / "problems"
        problems_dir.mkdir(parents=True)
        (problems_dir / "broken.json").write_text("{not json", encoding="utf-8")

        summaries = store.list_problems()

        assert len(summaries) == 1
        assert summaries[0].loadable is False
        assert summaries[0].id == "broken"

    def test_load_corrupt_file_raises_value_error(self, store, tmp_path):
        problems_dir = tmp_path / "problems"
        problems_dir.mkdir(parents=True)
        (problems_dir / "broken.json").write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError, match="broken.json"):
            store.load("broken")

    def test_delete_removes_file(self, store):
        problem = make_full_problem()
        store.save(problem)

        assert store.delete(problem.id) is True
        assert store.load(problem.id) is None
        assert store.delete(problem.id) is False

    def test_save_overwrites_existing_file(self, store):
        problem = make_full_problem()
        store.save(problem)
        problem.title = "Updated title"
        store.save(problem)

        assert store.load(problem.id).title == "Updated title"
