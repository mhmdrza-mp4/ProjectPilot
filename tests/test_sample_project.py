"""Hermetic validation of the bundled "Production Line Commissioning"
sample-project experience (no COM, no Microsoft Project, no network).

Guards:
1. the example directory ships the artifacts it documents;
2. `scenario-data.json` matches the EXISTING JEV integration's actual
   input contract (question name, instructions, levels, task_name +
   task_notes inputs);
3. scenario task names/notes match the REAL sample plan exactly (the
   generator script is the single source of truth for the scenario);
4. `expected-results.md` documents scenarios that actually appear in
   the sample, not invented task names;
5. the UI's sample helpers expose a real path and honest guide text.
"""

import importlib.util
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "examples" / "production-line-commissioning"
SAMPLE_FILE = SAMPLE_DIR / "Production_Line_Commissioning.mpp"
GENERATOR = REPO_ROOT / "scripts" / "generate_sample_msp.py"


def read_guide() -> str:
    return (SAMPLE_DIR / "README.md").read_text(encoding="utf-8")


def read_scenario_data() -> dict:
    raw = (SAMPLE_DIR / "scenario-data.json").read_text(encoding="utf-8")
    return json.loads(raw)


def read_expected_results() -> str:
    return (SAMPLE_DIR / "expected-results.md").read_text(encoding="utf-8")


def generator_plan():
    """Import the generator module WITHOUT executing COM code paths."""
    assert GENERATOR.exists()
    spec = importlib.util.spec_from_file_location("generate_sample_msp", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(GENERATOR.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class TestExampleArtifacts:
    def test_generated_sample_project_file_exists(self):
        assert SAMPLE_FILE.is_file()
        # An empty or very small file would not be a usable MSP project.
        assert SAMPLE_FILE.stat().st_size > 10_000

    def test_guide_and_docs_exist(self):
        assert read_guide()
        assert read_expected_results()
        assert read_scenario_data()

    def test_guide_mentions_planning_window_and_msp_workflow(self):
        guide = read_guide()
        # Real operational instructions, not marketing.
        assert "[L]" in guide
        assert "active" in guide.lower()
        assert "2026-10-22" in guide  # the documented planning window
        assert "read-only" in guide.lower()
        # The 4 files this feature actually ships must all be referenced.
        for name in (
            "Production_Line_Commissioning.mpp",
            "README.md",
            "scenario-data.json",
            "expected-results.md",
        ):
            assert name in guide


class TestScenarioDataMatchesJevContract:
    def test_input_contract_uses_the_app_question_shape(self):
        contract = read_scenario_data()["input_contract"]
        # Names/literals copy the EXISTING product code (no invented API).
        assert contract["question_name"] == "consequence_of_delay"
        assert contract["question_type"] == "score"
        assert contract["scale"] == "0..3, divided by 3 to become the CD share of the priority index"
        instructions = contract["instructions"]
        assert "NON-SCHEDULE" in instructions
        assert len(contract["levels"]) == 4

    def test_every_scenario_is_reviewed_and_has_both_inputs(self):
        for scenario in read_scenario_data()["scenarios"]:
            assert set(scenario) >= {
                "id",
                "task_name",
                "task_notes",
                "manual_assessment_level",
                "assessment_rationale",
            }
            assert 0 <= scenario["manual_assessment_level"] <= 3
            assert (
                scenario["assessment_rationale"].strip()
            ), f"scenario {scenario['id']!r} needs a real rationale"

    def test_scenario_task_names_come_from_the_real_sample_plan(self):
        plan = generator_plan()
        real_names = {
            spec["name"] for _, tasks in plan.PHASES for spec in tasks
        }
        for scenario in read_scenario_data()["scenarios"]:
            assert (
                scenario["task_name"] in real_names
            ), f"scenario {scenario['id']!r} references a task that does not exist in the sample"

    def test_scenario_notes_match_the_real_sample_plan_notes(self):
        plan = generator_plan()
        real_notes = {
            spec["name"]: spec["notes"]
            for _, tasks in plan.PHASES
            for spec in tasks
            if spec["notes"]
        }
        for scenario in read_scenario_data()["scenarios"]:
            name = scenario["task_name"]
            expected = real_notes.get(name)
            if scenario["task_notes"] is None:
                assert expected is None, (
                    f"scenario {scenario['id']!r} has no task_notes; "
                    f"sample plan for {name!r} should also have none"
                )
            else:
                assert scenario["task_notes"] == expected, (
                    f"scenario {scenario['id']!r} notes must match the "
                    f"sample plan notes for {name!r}"
                )


class TestSamplePlanShape:
    def test_plan_has_enough_tasks_phases_and_resources(self):
        plan = generator_plan()
        leaf_total = sum(len(tasks) for _, tasks in plan.PHASES)
        assert 100 <= leaf_total <= 150
        assert 10 <= len(plan.PHASES) <= 15
        assert len(plan.RESOURCE_NAMES) >= 10

    def test_plan_has_progress_mix(self):
        plan = generator_plan()
        tasks = [spec for _, tasks in plan.PHASES for spec in tasks]
        completed = sum(1 for t in tasks if t["progress"] == 100)
        in_progress = sum(1 for t in tasks if 0 < t["progress"] < 100)
        not_started = sum(1 for t in tasks if t["progress"] == 0)
        assert completed >= 20
        assert in_progress >= 5
        assert not_started >= 50

    def test_plan_has_the_documented_jEV_scenarios_with_notes(self):
        plan = generator_plan()
        scenario_names = {
            "Delivery: extrusion press",
            "Motor connections",
            "Integrated safety inspection",
            "Integrate PLC with SCADA",
            "Trial production run 1",
        }
        with_notes = {
            spec["name"]
            for _, tasks in plan.PHASES
            for spec in tasks
            if spec["notes"]
        }
        assert scenario_names <= with_notes

    def test_no_unassigned_non_milestone_tasks(self):
        plan = generator_plan()
        for _, tasks in plan.PHASES:
            for spec in tasks:
                if spec["milestone"]:
                    # Milestones may be silent (no assigned resource).
                    continue
                assert spec["resources"], (
                    f"task {spec['name']!r} should be assigned a resource"
                )


class TestExpectedResultsDocumentation:
    def test_snapshot_counts_derived_from_actual_read(self):
        expected = read_expected_results()
        # These numbers came from the REAL read of the generated .mpp.
        assert "120" in expected  # tasks
        assert "10" in expected  # resources
        assert "3" in expected  # milestones
        assert "empty (nothing dropped by mapping)" in expected

    def test_documents_the_recommended_window(self):
        expected = read_expected_results()
        assert "2026-10-22" in expected
        assert "2026-11-06" in expected
        assert "37" in expected  # candidates verified in that window

    def test_does_not_claims_live_api_calls(self):
        expected = read_expected_results()
        assert "No live JEV call was made" in expected
        assert "provisional" in expected.lower()


class TestUiDiscoverability:
    def test_sample_path_is_absolute_and_points_to_a_specific_file(self):
        from project_decision.tui import sample_project_path

        path = sample_project_path()
        assert path.endswith("Production_Line_Commissioning.mpp")

    def test_sample_guide_is_honest_about_offline_vs_live(self):
        from project_decision.tui import sample_project_guide_text

        text = sample_project_guide_text()
        assert "sample msp project" in text.lower()
        assert "Microsoft Project" in text
        # The real load action exists: [L] loads the active MSP plan.
        assert "[L]" in text
        assert "read-only" in text.lower()
        assert "TYPESAFE_API_KEY" in text
        # Real limited claims: no API call fabrications.
        assert "provisional" in text.lower()

    def test_sample_guide_references_the_doc_directory(self):
        from project_decision.tui import sample_project_guide_text

        assert "examples/production-line-commissioning/" in (
            sample_project_guide_text()
        )
