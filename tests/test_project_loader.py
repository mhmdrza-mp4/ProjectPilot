"""Tests for the application-facing project loading boundary (fully offline)."""

import ast
import inspect
from datetime import date, datetime

import pytest

import project_decision.application.project_loader as loader_module
from project_decision.application.project_loader import load_active_project, load_project
from project_decision.domain.constraint import ConstraintKind
from project_decision.domain.project_state import ProjectState
from project_decision.infrastructure.msp.exceptions import (
    MspNoActiveProjectError,
    MspNotAvailableError,
    MspReadError,
)
from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter
from tests.fakes.fake_msp import (
    FakeMspDependency,
    FakeMspProject,
    FakeMspResource,
    FakeMspTask,
)

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)


# -- offline MSP stand-ins (same pattern as test_msp_adapter) -------------------


class FakeComClient:
    """Duck-typed MspComClient stand-in (the loader's DI surface)."""

    def __init__(self, project: FakeMspProject) -> None:
        self._project = project
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def get_active_project(self) -> FakeMspProject:
        return self._project

    def get_minutes_per_day(self) -> float:
        return self._project.MinutesPerDay


def demo_project() -> FakeMspProject:
    """Mirrors a small realistic MSP schedule, with rich task fields:

        1  Planning          summary          -> skipped
        2  Design            1 day, 40 % done, slack 240 min, critical,
                             assigned to Alice, notes "soil report"
        3  Implementation    depends on Design, deadline 2026-07-01,
                             empty notes -> None
        4  archived row      inactive         -> skipped
    """
    alice = FakeMspResource(UniqueID=501, Name="Alice", MaxUnits=1.0)
    return FakeMspProject(
        Name="Example Project",
        MinutesPerDay=480.0,
        Tasks=[
            FakeMspTask(UniqueID=1, Name="Planning", Summary=True),
            FakeMspTask(
                UniqueID=2,
                Name="Design",
                Duration=480.0,
                PercentComplete=40,
                Start=datetime(2026, 6, 1, 8, 0),
                Finish=datetime(2026, 6, 2, 17, 0),
                Resources=[alice],
                TotalSlack=240.0,
                Critical=True,
                Notes="soil report",
            ),
            FakeMspTask(
                UniqueID=3,
                Name="Implementation",
                Duration=1440.0,
                Start=datetime(2026, 6, 3, 8, 0),
                Finish=datetime(2026, 6, 5, 17, 0),
                TaskDependencies=[
                    FakeMspDependency(
                        From=FakeMspTask(UniqueID=2, Name="Design"),
                        To=FakeMspTask(UniqueID=3, Name="Implementation"),
                    )
                ],
                Deadline=datetime(2026, 7, 1, 17, 0),
                Notes="   ",
            ),
            FakeMspTask(UniqueID=4, Name="archived row", Active=False),
        ],
        Resources=[alice],
        Start=datetime(2026, 6, 1, 8, 0),
        Finish=datetime(2026, 6, 5, 17, 0),
        StatusDate=datetime(2026, 6, 3, 9, 0),
        BaselineStart=datetime(2026, 6, 1, 8, 0),
        BaselineFinish=None,
    )


class TestSuccessfulLoad:
    def test_injected_adapter_loads_a_domain_state(self):
        adapter = MspProjectAdapter(FakeComClient(demo_project()))

        state = load_project(adapter, captured_at=CAPTURED_AT)

        assert isinstance(state, ProjectState)
        assert state.captured_at == CAPTURED_AT  # forwarded unchanged
        assert state.project_name == "Example Project"
        assert [task.id for task in state.tasks] == ["2", "3"]

    def test_load_active_project_drives_the_managed_chain(self, monkeypatch):
        """The COM-free verification of the real-chain convenience."""
        import project_decision.infrastructure.msp as msp_package

        client = FakeComClient(demo_project())
        monkeypatch.setattr(msp_package, "MspComClient", lambda: client)

        state = load_active_project(captured_at=CAPTURED_AT)

        assert client.connect_calls == 1 and client.disconnect_calls == 1
        assert state.project_name == "Example Project"


# -- mapping is delegated to the existing mapper (representative fields) --------


class TestMappingPreservation:
    def test_representative_task_fields_come_from_the_mapper(self):
        adapter = MspProjectAdapter(FakeComClient(demo_project()))

        state = load_project(adapter, captured_at=CAPTURED_AT)
        by_id = {task.id: task for task in state.tasks}

        design = by_id["2"]
        assert design.name == "Design"
        assert design.start == date(2026, 6, 1)
        assert design.finish == date(2026, 6, 2)
        assert design.progress == pytest.approx(0.4)  # 40 % -> fraction
        assert design.duration_days == pytest.approx(1.0)  # 480 min -> 1 day
        assert design.total_slack_days == pytest.approx(0.5)  # 240 min -> days
        assert design.is_critical is True
        assert design.notes == "soil report"
        assert design.is_summary is False
        assert design.assigned_resource_ids == ["501"]

    def test_summary_and_inactive_rows_are_skipped_by_the_mapper(self):
        adapter = MspProjectAdapter(FakeComClient(demo_project()))

        state = load_project(adapter, captured_at=CAPTURED_AT)

        ids = {task.id for task in state.tasks}
        assert "1" not in ids  # summary row is presentation, not a work item
        assert "4" not in ids  # inactive row is skipped

    def test_dependencies_deadline_and_resources_flow_through(self):
        adapter = MspProjectAdapter(FakeComClient(demo_project()))

        state = load_project(adapter, captured_at=CAPTURED_AT)
        by_id = {task.id: task for task in state.tasks}
        implementation = by_id["3"]

        assert implementation.dependency_ids == ["2"]  # predecessor UniqueID
        assert implementation.notes is None  # whitespace-only stays None
        assert implementation.is_critical is None  # unset stays None
        assert [
            constraint.id for constraint in state.constraints
        ] == ["deadline-3"]  # deadline mapped as a constraint by the mapper
        assert state.constraints[0].kind is ConstraintKind.DEADLINE
        assert [resource.id for resource in state.resources] == ["501"]


# -- the read-only boundary ------------------------------------------------------


class AccessRecorder:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []      # (target, attribute set)
        self.method_calls: list[tuple[str, str]] = []  # (target, method called)


class RecordingProxy:
    """Wraps fake COM objects, recording writes and method calls.

    Reads go through ``__getattr__``; an attribute WRITE anywhere in the
    object graph is recorded (and would fail the read-only assertions).
    Plain values (numbers/strings/dates) pass through unwrapped.
    """

    def __init__(self, name: str, target: object, recorder: AccessRecorder) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_recorder", recorder)

    def __getattr__(self, attribute: str):
        value = getattr(self._target, attribute)
        if callable(value):
            def call(*args, **kwargs):
                self._recorder.method_calls.append((self._name, attribute))
                return _proxify(
                    value(*args, **kwargs), self._name, self._recorder
                )

            return call
        return _proxify(value, self._name, self._recorder)

    def __setattr__(self, attribute: str, value: object) -> None:
        self._recorder.writes.append((self._name, attribute))


def _proxify(value: object, name: str, recorder: AccessRecorder) -> object:
    if value is None or isinstance(value, (bool, int, float, str, bytes, date)):
        return value
    if isinstance(value, list):
        return [_proxify(item, name, recorder) for item in value]
    return RecordingProxy(name, value, recorder)


class RecordingComClient:
    """Fake COM client that hands the adapter read-recording proxies."""

    def __init__(self, project: FakeMspProject, recorder: AccessRecorder) -> None:
        self._project = project
        self._recorder = recorder

    def get_active_project(self):
        return _proxify(self._project, "project", self._recorder)

    def get_minutes_per_day(self) -> float:
        return self._project.MinutesPerDay


class TestReadOnlyBoundary:
    def test_loading_never_writes_or_saves_anything(self):
        recorder = AccessRecorder()
        adapter = MspProjectAdapter(RecordingComClient(demo_project(), recorder))

        state = load_project(adapter, captured_at=CAPTURED_AT)

        assert isinstance(state, ProjectState)
        assert recorder.writes == []  # zero attribute writes anywhere
        save_like = {
            attribute
            for _target, attribute in recorder.method_calls
            if attribute.lower().lstrip("_") in {"save", "saveas"}
            or attribute.lower().startswith(("save", "close", "quit"))
        }
        assert save_like == set()  # never saved/saved-as/closed/quitted


# -- injection, errors, emptiness, hygiene ---------------------------------------


class CannedAdapter:
    """A fully fake adapter: only the duck-typed surface the loader uses."""

    def __init__(self, state: ProjectState) -> None:
        self._state = state
        self.seen_captured_at: datetime | bool = False

    def read_active_project_state(self, captured_at=None) -> ProjectState:
        self.seen_captured_at = captured_at
        return self._state


class TestAdapterInjection:
    def test_any_duck_typed_adapter_is_accepted_without_com(self):
        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            project_name="Canned",
        )
        adapter = CannedAdapter(state)

        loaded = load_project(adapter, captured_at=CAPTURED_AT)

        assert adapter.seen_captured_at == CAPTURED_AT  # forwarded
        assert loaded is state  # the adapter's object, never copied/invented


class TestErrorPropagation:
    def test_load_failures_stay_errors_never_empty_states(self):
        state = ProjectState(project_id="P", captured_at=CAPTURED_AT, project_name="P")

        for error in (MspNotAvailableError("no msp"), MspNoActiveProjectError("open one"),
                      MspReadError("unreadable"), RuntimeError("unexpected bug")):
            class BrokenAdapter:
                def __init__(self, exc):
                    self._exc = exc

                def read_active_project_state(self, captured_at=None):
                    raise self._exc

            with pytest.raises(type(error)):
                load_project(BrokenAdapter(error), captured_at=CAPTURED_AT)

        # Sanity: the same machinery succeeds when nothing raises.
        assert load_project(CannedAdapter(state), captured_at=CAPTURED_AT) is state


class TestEmptyProject:
    def test_an_empty_project_is_a_valid_empty_state(self):
        empty = FakeMspProject(Name="", Tasks=[], Resources=[])
        adapter = MspProjectAdapter(FakeComClient(empty))

        state = load_project(adapter, captured_at=CAPTURED_AT)

        assert len(state.tasks) == 0
        assert len(state.resources) == 0
        assert isinstance(state, ProjectState)  # valid, NOT a failure


class TestSourceHygiene:
    @staticmethod
    def _imported_modules(source: str) -> list[str]:
        tree = ast.parse(source)
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        return modules

    def test_loader_never_imports_com_specific_modules(self):
        modules = self._imported_modules(inspect.getsource(loader_module))
        for module in modules:
            assert "win32com" not in module
            assert "pythoncom" not in module
            assert "pywintypes" not in module

    def test_loader_checks_run_without_msproject_or_pywin32(self):
        # Everything in this file executes without Microsoft Project or
        # pywin32; the AST checks above pin that at the source level.
        from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter

        adapter = MspProjectAdapter(FakeComClient(demo_project()))
        state = load_project(adapter, captured_at=CAPTURED_AT)
        assert len(state.tasks) == 2  # full offline load works with zero COM

    def test_decision_pipeline_stays_independent_of_msp(self):
        import project_decision.application.decision_pipeline as pipeline_module

        modules = self._imported_modules(inspect.getsource(pipeline_module))
        for module in modules:
            assert "infrastructure" not in module  # domain/application only

    def test_loader_delegates_mapping_and_states_its_read_only_contract(self):
        module_doc = inspect.getdoc(loader_module)
        assert "READ-ONLY" in module_doc.upper()  # explicit read-only statement
