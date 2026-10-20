"""Manual read-only test against a real Microsoft Project (Windows only).

Prerequisites:
- Windows with Microsoft Project installed
- a project open in Microsoft Project
- pywin32 installed in this environment (pip install pywin32)

Run from the project root:

    python scripts\\manual_msp_read.py

This script only READS. It never writes, saves, or closes anything in
Microsoft Project, and disconnect() never quits the application.
"""

from __future__ import annotations

import pathlib
import sys

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from project_decision.infrastructure.msp import (  # noqa: E402
    MspComClient,
    MspProjectAdapter,
)
from project_decision.infrastructure.msp.exceptions import MspError  # noqa: E402


def main() -> int:
    client = MspComClient()
    try:
        client.connect()
    except MspError as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Connected to Microsoft Project")
    try:
        adapter = MspProjectAdapter(client)
        project = adapter.read_active_project()
    except MspError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        client.disconnect()

    print(f"Active Project: {project.name}")
    print(f"Tasks: {len(project.tasks)}  Resources: {len(project.resources)}  "
          f"Milestones: {len(project.milestones)}  Constraints: {len(project.constraints)}")
    print()
    print("Tasks:")
    for index, task in enumerate(project.tasks, start=1):
        start = task.start.isoformat() if task.start else "-"
        finish = task.finish.isoformat() if task.finish else "-"
        progress = f"{task.progress * 100:.0f}%"
        print(
            f"{index}. {task.name} | "
            f"id={task.id} | "
            f"duration={task.duration_days:g}d | "
            f"start={start} | "
            f"finish={finish} | "
            f"progress={progress}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
