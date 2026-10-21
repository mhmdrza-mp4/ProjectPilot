"""WRITE-CAPABLE, one-time test-fixture authoring for the real-MSP validation suite.

WARNING - THIS SCRIPT MODIFIES MICROSOFT PROJECT.
It ADDS tasks and resources and SETS fields on the CURRENTLY ACTIVE
Microsoft Project document. It is intended ONLY for deliberate creation
of the controlled test fixture used by the live-MSP validation suite.
Running it against a real project would change that project in memory.
Nothing here saves, closes, or quits Microsoft Project: saving is left
entirely to the user.

Required opt-in:

    python scripts/msp_fixture_author.py --i-understand

Without ``--i-understand`` the script exits with status 2 BEFORE it
imports win32com or connects to Microsoft Project.

Deliberate boundaries:
- it talks RAW COM directly (win32com), never through MspComClient;
- it is NEVER used by the product, the tests, or the loader;
- the product's MSP chain (com_client/adapter/mapper/loader) contains no
  call to this module and performs no Microsoft Project write;
- the controlled fixture is the standard test data Step 14 prescribes
  (chains, branches, overlaps, notes, resources).

Structure authored (task numbers as MSP displays them):
  1  "Phase A"            summary
  2    Setup              2d, Alice, note
  3    Install line       3d, pred 2, Alice        (two successors: 4, 9)
  4    Configure          2d, pred 3
  5    Acceptance         1d, pred 4, 100%
  6  "Phase B"            summary
  7    Prepare site doc   2d, Bob, 100%, no note   (unlinked -> starts today)
  8    Training           3d, Bob                  (unlinked -> overlaps 7)
  9    Certification      2d, preds 3,7, Alice+Bob, whitespace-only note
  10 Parking lot          1d, no predecessors, regular note
"""

from __future__ import annotations

import argparse
import sys

OPT_IN_FLAG = "--i-understand"

_REFUSAL = (
    "REFUSING TO RUN: this script WRITES tasks and resources into the "
    "CURRENTLY ACTIVE Microsoft Project document.\n"
    "It is intended only for deliberate test-fixture authoring.\n"
    f"Re-run with {OPT_IN_FLAG} to confirm that you intend to modify the "
    "active project. No Microsoft Project connection was made."
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "WRITE-CAPABLE one-time test-fixture authoring: adds tasks and "
            "resources to the currently active Microsoft Project document."
        )
    )
    parser.add_argument(
        OPT_IN_FLAG,
        action="store_true",
        help=(
            "Required: confirms that the active Microsoft Project document "
            "may be modified."
        ),
    )
    return parser.parse_args(argv)


def _author_fixture() -> int:
    import win32com.client as win32

    app = win32.Dispatch("MSProject.Application")
    project = app.ActiveProject
    if project is None:
        print("ERROR: no active project")
        raise SystemExit(1)

    project.Resources.Add("Alice")
    project.Resources.Add("Bob")

    tasks = project.Tasks

    def add(name: str, minutes: int = 480, outline: int = 1) -> object:
        task = tasks.Add(name)
        task.OutlineLevel = outline
        task.Duration = minutes
        return task

    # Phase A (summary 1) - the critical chain 2 -> 3 -> 4 -> 5.
    add("Phase A", outline=1)
    _setup = add("Setup", 2 * 480, 2)
    _setup.ResourceNames = "Alice"
    _setup.Notes = "Site preparation before install"
    _install = add("Install line", 3 * 480, 2)
    _install.Predecessors = "2"
    _install.ResourceNames = "Alice"
    add("Configure", 2 * 480, 2).Predecessors = "3"
    _acceptance = add("Acceptance", 480, 2)
    _acceptance.Predecessors = "4"
    _acceptance.PercentComplete = 100

    # Phase B (summary 6) - two unlinked branches that overlap on Bob.
    add("Phase B", outline=1)
    _docs = add("Prepare site docs", 2 * 480, 2)
    _docs.ResourceNames = "Bob"
    _docs.PercentComplete = 100  # no note on purpose
    _training = add("Training", 3 * 480, 2)
    _training.ResourceNames = "Bob"
    add("Certification", 2 * 480, 2).Predecessors = "3,7"
    _cert = project.Tasks(9)
    _cert.ResourceNames = "Alice;Bob"
    _cert.Notes = "   "  # whitespace-only on purpose
    _parking = add("Parking lot", 480, 2)
    _parking.Notes = "Optional cleanup later"

    print("Fixture authored into", project.Name)
    for index in range(1, project.Tasks.Count + 1):
        task = project.Tasks.Item(index)
        print(
            f"{task.ID:>3} | {task.Name:<20} | dur={task.Duration} | "
            f"outline={task.OutlineLevel} | pred={task.Predecessors} | "
            f"res={task.ResourceNames} | prog={task.PercentComplete}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.i_understand:
        print(_REFUSAL, file=sys.stderr)
        return 2
    return _author_fixture()


if __name__ == "__main__":
    sys.exit(main())
