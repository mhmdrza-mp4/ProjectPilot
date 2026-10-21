"""Read-only field dump of the ACTIVE Microsoft Project, via the product stack.

Part of the real-world validation (Step 14). It reads the active project
through the application loader chain and prints a structure report that a
planner can compare against what Microsoft Project displays:

    python scripts/msp_field_dump.py

Strictly read-only: no Save/SaveAs/FileSave/FileClose and no task,
resource, or schedule mutation. It NEVER fabricates values: unavailable
fields are printed as "not available".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from project_decision.application.decision_workflow import load_project
    from project_decision.application.project_loader import load_active_project

    try:
        workflow = load_project(load_active_project)
    except Exception as exc:  # noqa: BLE001 - a validation tool prints the failure
        print(f"ERROR: could not read the active project: {exc}")
        raise SystemExit(1)

    state = workflow.project_state
    print("Connected to Microsoft Project")
    print(f"Project: {state.project_name}")
    print(f"Captured at: {state.captured_at}")
    print(f"Tasks: {len(state.tasks)}  Resources: {len(state.resources)}")
    print()
    for task in state.tasks:
        print(
            f"{task.id:>6} | {task.name[:36]:<36} | "
            f"start={task.start} finish={task.finish} | "
            f"dur={task.duration_days} | prog={task.progress:.0%} | "
            f"slack={task.total_slack_days} | crit={task.is_critical} | "
            f"summary={task.is_summary}"
        )
        if task.dependency_ids:
            print(f"        deps: {', '.join(task.dependency_ids)}")
        if task.assigned_resource_ids:
            print(f"        res:  {', '.join(task.assigned_resource_ids)}")
        if task.notes:
            print(f"        note: {task.notes[:80]!r}")
        elif task.notes is not None:
            print("        note: <whitespace-only>")
        else:
            print("        note: none")
    print()
    for resource in state.resources:
        print(f"R {resource.id:>6} | {resource.name}")


if __name__ == "__main__":
    main()
