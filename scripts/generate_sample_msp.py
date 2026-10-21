"""Generate the bundled "Production Line Commissioning" sample MSP project.

This tool is OPT-IN and heavily guarded, in the same spirit as the MSP
fixture tool:

- Windows with a locally installed Microsoft Project (and pywin32).
- Requires ``--i-understand`` BEFORE it touches COM at all, because it
  drives Microsoft Project via COM to create and save a NEW project.
- Never overwrites: the target file must not exist.
- Never touches existing projects: it only creates a fresh private
  project document, saves it to the sample path, and LEAVES THAT NEW
  project open so it can be inspected (the application's own MSP
  integration stays strictly read-only).
- Deterministic: every task, duration, dependency, resource, and
  progress value below is hard-coded from the documented industrial
  scenario. No random data, no fabricated dates, no invented values.

Run:

    python scripts/generate_sample_msp.py --i-understand

The generated file lands in:

    examples/production-line-commissioning/Production_Line_Commissioning.mpp

The user guide for the generated file lives next to it:

    examples/production-line-commissioning/README.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# -- paths --------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "examples"
    / "production-line-commissioning"
    / "Production_Line_Commissioning.mpp"
)

PROJECT_START_DATE = (2026, 9, 7)   # Monday — schedule anchor date
STATUS_DATE = (2026, 10, 21)        # Wednesday — mid-execution snapshot

RESOURCE_NAMES: tuple[str, ...] = (
    "Project Manager",
    "Site Engineer",
    "Mechanical Fitter",
    "Electrician",
    "Automation Engineer",
    "QA Inspector",
    "Safety Officer",
    "Logistics Coordinator",
    "Civil Contractor",
    "Operator Trainer",
)


def _spec(
    name: str,
    duration: float,
    resources: tuple[str, ...] = (),
    *,
    progress: int = 0,
    notes: str | None = None,
    deps: tuple[tuple[int, int], ...] = (),
    milestone: bool = False,
) -> dict:
    """One leaf task definition.

    Indices in ``deps`` reference LEAF tasks of the given phase
    (0-based), never summary rows.
    """
    return {
        "name": name,
        "duration": 0.0 if milestone else float(duration),
        "resources": resources,
        "progress": progress,
        "notes": notes,
        "deps": deps,
        "milestone": milestone,
    }


# The whole sample scenario, phase by phase. Every field below was
# hand-designed: names mirror real industrial commissioning vocabulary,
# dependencies express real operational logic (delivery → install →
# commission), and progress values capture a mid-execution snapshot as
# of STATUS_DATE. Percentages for later phases stay at 0 (not started).
PHASES: tuple[tuple[str, tuple[dict, ...]], ...] = (
    ("Project Initiation", (
        _spec("Kick-off meeting & project charter", 2, ("Project Manager",),
              progress=100),
        _spec("Define scope & acceptance criteria", 3,
              ("Project Manager", "Site Engineer"), progress=100),
        _spec("Initial risk register", 2,
              ("Project Manager", "Safety Officer"), progress=100),
        _spec("Budget approval", 1, ("Project Manager",), progress=100),
        _spec("Mobilize site team", 3,
              ("Site Engineer", "Logistics Coordinator"), progress=100),
    )),
    ("Planning & Engineering", (
        _spec("Develop commissioning plan", 5,
              ("Project Manager", "Site Engineer"), progress=100,
              deps=((0, 0), (0, 4))),
        _spec("Layout review & 3D model check", 4,
              ("Site Engineer", "Mechanical Fitter"), progress=100,
              deps=((1, 0),)),
        _spec("Equipment specifications & datasheets", 4,
              ("Site Engineer", "Mechanical Fitter"), progress=100,
              deps=((1, 1),)),
        _spec("Electrical load calculation", 3, ("Electrician",),
              progress=100, deps=((1, 2),)),
        _spec("Control philosophy design", 5, ("Automation Engineer",),
              progress=100, deps=((1, 3),)),
        _spec("Procurement requisitions", 2, ("Logistics Coordinator",),
              progress=100, deps=((1, 2), (1, 3))),
        _spec("Construction schedule baseline", 2, ("Project Manager",),
              progress=100, deps=((1, 0),)),
        _spec("Site safety plan", 3, ("Safety Officer",), progress=100,
              deps=((1, 6),)),
        _spec("Quality & inspection plan", 3, ("QA Inspector",),
              progress=100, deps=((1, 6),)),
        _spec("P&ID review with operations", 3,
              ("Project Manager", "QA Inspector"), progress=60,
              deps=((1, 0), (1, 3)),
              notes="Operations sign-off pending; owner confirms it does "
                    "not close production possibilities."),
    )),
    ("Equipment Procurement", (
        _spec("RFQ: extrusion press", 3, ("Logistics Coordinator",),
              progress=100, deps=((1, 5),)),
        _spec("Award contract: extrusion press", 2,
              ("Logistics Coordinator", "Project Manager"), progress=100,
              deps=((2, 0),)),
        _spec("Vendor build monitoring: press", 10,
              ("Logistics Coordinator",), progress=40,
              deps=((2, 1),),
              notes="Vendor supplier delay flagged; re-plan if factory "
                    "acceptance slips — production start depends on it."),
        _spec("RFQ: conveyor systems", 2, ("Logistics Coordinator",),
              progress=100, deps=((1, 5),)),
        _spec("Award contract: conveyors", 2,
              ("Logistics Coordinator", "Project Manager"), progress=100,
              deps=((2, 3),)),
        _spec("Conveyor fabrication & shipment", 8,
              ("Logistics Coordinator",), progress=10, deps=((2, 4),)),
        _spec("RFQ: MCC & switchgear", 2, ("Electrician",),
              progress=100, deps=((1, 3),)),
        _spec("Award contract: MCC vendor", 2,
              ("Electrician", "Project Manager"), progress=100,
              deps=((2, 6),)),
        _spec("MCC build & shipment", 10, ("Electrician",), progress=5,
              deps=((2, 7),)),
        _spec("Delivery: extrusion press", 1,
              ("Logistics Coordinator", "Mechanical Fitter"),
              deps=((2, 2),),
              notes="A late delivery of the press blocks every "
                    "mechanical installation task downstream and "
                    "jeopardizes the production start date."),
        _spec("Delivery: conveyor systems", 1, ("Logistics Coordinator",),
              deps=((2, 5),)),
        _spec("Delivery: MCC panels", 1, ("Electrician",),
              deps=((2, 8),)),
    )),
    ("Civil Works & Facility Preparation", (
        _spec("Site clearance & grading", 5, ("Civil Contractor",),
              progress=100),
        _spec("Foundations: press base", 4, ("Civil Contractor",),
              progress=100, deps=((3, 0),)),
        _spec("Foundations: conveyor supports", 3, ("Civil Contractor",),
              progress=100, deps=((3, 0),)),
        _spec("Foundations: MCC room", 2, ("Civil Contractor",),
              progress=100, deps=((3, 0),)),
        _spec("Structural steel erection", 6,
              ("Civil Contractor", "Mechanical Fitter"), progress=80,
              deps=((3, 1), (3, 2))),
        _spec("Roofing & cladding", 4, ("Civil Contractor",), progress=40,
              deps=((3, 4),)),
        _spec("Floor slabs & resin coating", 4, ("Civil Contractor",),
              deps=((3, 5),)),
        _spec("Drainage & pits", 3, ("Civil Contractor",), deps=((3, 1),)),
        _spec("Compressed-air mains", 3,
              ("Civil Contractor", "Mechanical Fitter"), deps=((3, 4),)),
        _spec("Lighting & small power", 4, ("Electrician",),
              deps=((3, 5),)),
        _spec("Fire protection mains", 3,
              ("Safety Officer", "Civil Contractor"), deps=((3, 4),)),
        _spec("Facility readiness check", 1, ("Site Engineer",),
              deps=((3, 6), (3, 8), (3, 9)),
              notes="Gate before mechanical installation; checklist "
                    "signed off by site engineering."),
    )),
    ("Mechanical Installation", (
        _spec("Receipt & staging: press", 1,
              ("Mechanical Fitter", "Logistics Coordinator"),
              deps=((2, 9),)),
        _spec("Position press on foundations", 3, ("Mechanical Fitter",),
              deps=((4, 0), (3, 11))),
        _spec("Align & anchor press", 2, ("Mechanical Fitter",),
              deps=((4, 1),)),
        _spec("Hydraulic piping: press", 3, ("Mechanical Fitter",),
              deps=((4, 2),)),
        _spec("Cooling-water lines to press", 2, ("Mechanical Fitter",),
              deps=((4, 3), (3, 8))),
        _spec("Conveyor assembly: line A", 4, ("Mechanical Fitter",),
              deps=((2, 10), (3, 11))),
        _spec("Conveyor assembly: line B", 4, ("Mechanical Fitter",),
              deps=((2, 10), (3, 11))),
        _spec("Install guards: conveyors", 2, ("Mechanical Fitter",),
              deps=((4, 5), (4, 6))),
        _spec("Pneumatic drops to stations", 3, ("Mechanical Fitter",),
              deps=((3, 8),)),
        _spec("Material handling hoists", 2, ("Mechanical Fitter",),
              deps=((3, 4),)),
        _spec("Install robotic cells", 5,
              ("Mechanical Fitter", "Automation Engineer"),
              deps=((2, 10), (3, 11))),
        _spec("Robot calibration", 3,
              ("Automation Engineer", "Mechanical Fitter"),
              deps=((4, 10),)),
        _spec("Lubrication & greasing", 1, ("Mechanical Fitter",),
              deps=((4, 2), (4, 5))),
        _spec("Mechanical completion punch list", 2,
              ("Site Engineer", "Mechanical Fitter"),
              deps=((4, 2), (4, 5), (4, 8))),
        _spec("Mechanical completion sign-off", 1,
              ("QA Inspector", "Site Engineer"), deps=((4, 13),)),
    )),
    ("Electrical Installation", (
        _spec("Stage MCC panels", 1,
              ("Electrician", "Logistics Coordinator"), deps=((2, 11),)),
        _spec("Install MCC & switchgear", 4, ("Electrician",),
              deps=((5, 0), (3, 9))),
        _spec("Cable trays & containment", 4,
              ("Electrician", "Civil Contractor"), deps=((3, 5),)),
        _spec("Feeder cables: MCC → press", 3, ("Electrician",),
              deps=((5, 1), (4, 2))),
        _spec("Feeder cables: MCC → conveyors", 3, ("Electrician",),
              deps=((5, 1), (4, 5))),
        _spec("Motor connections", 4, ("Electrician",),
              deps=((5, 3), (5, 4)),
              notes="Late motor connections stall every downstream "
                    "integration task; production start slips."),
        _spec("Instrument wiring", 3,
              ("Electrician", "Automation Engineer"),
              deps=((5, 2), (4, 10))),
        _spec("Lighting circuits (final)", 2, ("Electrician",),
              deps=((5, 2),)),
        _spec("Earthing & bonding", 2, ("Electrician",), deps=((5, 1),)),
        _spec("Cable pulling & terminations", 2, ("Electrician",),
              deps=((5, 5),)),
        _spec("Panel labeling & documentation", 1,
              ("Electrician", "QA Inspector"), deps=((5, 9),)),
        _spec("Pre-energization check", 1,
              ("Electrician", "Safety Officer"),
              deps=((5, 5), (5, 8), (5, 10))),
    )),
    ("Utilities & Supporting Infrastructure", (
        _spec("Chiller & HVAC install", 4,
              ("Civil Contractor", "Mechanical Fitter"), deps=((3, 5),)),
        _spec("Chilled-water piping", 3, ("Mechanical Fitter",),
              deps=((6, 0),)),
        _spec("Water-treatment skid", 2, ("Mechanical Fitter",),
              deps=((3, 7),)),
        _spec("DI-water lines", 2, ("Mechanical Fitter",), deps=((6, 2),)),
        _spec("Compressed-air dryer & receivers", 2, ("Mechanical Fitter",),
              deps=((3, 8),)),
        _spec("Nitrogen supply connection", 2,
              ("Mechanical Fitter", "Site Engineer"), deps=((6, 4),)),
        _spec("Natural gas & burners", 3,
              ("Mechanical Fitter", "Safety Officer"), deps=((3, 6),)),
        _spec("Cooling tower erection", 3,
              ("Civil Contractor", "Mechanical Fitter"), deps=((3, 5),)),
        _spec("Utility metering & monitoring", 2,
              ("Electrician", "Automation Engineer"), deps=((5, 2), (6, 1))),
        _spec("Utilities ready for commissioning", 1, ("Site Engineer",),
              deps=((6, 1), (6, 3), (6, 5), (6, 6))),
    )),
    ("Control System & Automation", (
        _spec("Develop PLC logic: line A", 6, ("Automation Engineer",),
              progress=30, deps=((1, 4),)),
        _spec("Develop PLC logic: line B", 6, ("Automation Engineer",),
              progress=20, deps=((1, 4),)),
        _spec("SCADA screens & historian", 4, ("Automation Engineer",),
              progress=10, deps=((1, 4),)),
        _spec("HMI design review", 2,
              ("Automation Engineer", "Project Manager"), deps=((7, 2),)),
        _spec("Network switches & cabling", 3,
              ("Electrician", "Automation Engineer"), deps=((5, 2),)),
        _spec("MCC control wiring", 3, ("Electrician",), deps=((5, 1),)),
        _spec("Load PLC programs", 2, ("Automation Engineer",),
              deps=((7, 0), (7, 1), (7, 5))),
        _spec("IO checkout: digital signals", 3,
              ("Automation Engineer", "Mechanical Fitter"),
              deps=((7, 6), (4, 10), (5, 5))),
        _spec("IO checkout: analog signals", 3,
              ("Automation Engineer", "Electrician"), deps=((7, 6), (5, 5))),
        _spec("Integrate PLC with SCADA", 3, ("Automation Engineer",),
              deps=((7, 7), (7, 8)),
              notes="Delays here stall the integration phase and the "
                    "production start; commercial impact of late "
                    "automation is high."),
        _spec("Safety PLC & interlocks", 3,
              ("Safety Officer", "Automation Engineer"), deps=((7, 7),)),
        _spec("Control-system dry run", 2,
              ("Automation Engineer", "Mechanical Fitter"),
              deps=((7, 9), (7, 10))),
    )),
    ("Safety Inspections & Compliance", (
        _spec("Hazard-analysis update", 2, ("Safety Officer",),
              deps=((1, 7),)),
        _spec("Lockout/tagout procedures", 2,
              ("Safety Officer", "Site Engineer"), deps=((8, 0),)),
        _spec("Machine-guarding inspection", 2,
              ("Safety Officer", "Mechanical Fitter"),
              deps=((4, 7), (8, 1))),
        _spec("Electrical-safety inspection", 2,
              ("Safety Officer", "Electrician"), deps=((5, 11), (8, 1))),
        _spec("Pressure-vessels certification", 2, ("Safety Officer",),
              deps=((6, 4),)),
        _spec("Fire-protection testing", 2,
              ("Safety Officer", "Civil Contractor"), deps=((3, 10), (6, 6))),
        _spec("Integrated safety inspection", 2,
              ("Safety Officer", "QA Inspector"),
              deps=((8, 2), (8, 3), (8, 4), (8, 5)),
              notes="Missing safety approval blocks the energization and "
                    "integration phases; regulatory and safety "
                    "consequences are material."),
        _spec("Safety sign-off (GO / NO-GO)", 0, (),
              milestone=True, deps=((8, 6),)),
    )),
    ("Integration & Commissioning", (
        _spec("Energize & initial power-up", 1,
              ("Electrician", "Site Engineer"), deps=((8, 7),)),
        _spec("Jog tests: individual drives", 2,
              ("Electrician", "Mechanical Fitter"), deps=((9, 0),)),
        _spec("Sequence tests: line A", 3,
              ("Mechanical Fitter", "Automation Engineer"),
              deps=((9, 1), (7, 9))),
        _spec("Sequence tests: line B", 3,
              ("Mechanical Fitter", "Automation Engineer"),
              deps=((9, 1), (7, 9))),
        _spec("Closed-loop tuning", 2, ("Automation Engineer",),
              deps=((9, 2), (9, 3))),
        _spec("Line interconnect integration", 2,
              ("Mechanical Fitter", "Automation Engineer"),
              deps=((9, 4),)),
        _spec("Quality gates setup", 2,
              ("QA Inspector", "Automation Engineer"), deps=((9, 2),)),
        _spec("Batch tracking validation", 2,
              ("QA Inspector", "Automation Engineer"), deps=((9, 6),)),
        _spec("Pre-trial readiness review", 1,
              ("Site Engineer", "QA Inspector", "Project Manager"),
              deps=((9, 5), (9, 7))),
        _spec("Integration test complete", 0, (),
              milestone=True, deps=((9, 8),)),
    )),
    ("Trial Production & Quality Validation", (
        _spec("Trial production run 1", 3,
              ("Mechanical Fitter", "QA Inspector", "Project Manager"),
              deps=((9, 9),),
              notes="A late trial run pushes back the production start; "
                    "customer commitments assume this window."),
        _spec("First-article inspection", 2, ("QA Inspector",),
              deps=((10, 0),)),
        _spec("Trial-run adjustments", 2,
              ("Mechanical Fitter", "Automation Engineer"),
              deps=((10, 1),)),
        _spec("Trial production run 2", 3,
              ("Mechanical Fitter", "QA Inspector", "Project Manager"),
              deps=((10, 2),),
              notes="Second trial validates the production ramp; delays "
                    "here compound customer commitments."),
        _spec("OEE / line-rate validation", 2,
              ("Site Engineer", "Project Manager"), deps=((10, 3),)),
        _spec("Operator training: shift A", 2, ("Operator Trainer",),
              deps=((9, 9),)),
        _spec("Operator training: shift B", 2, ("Operator Trainer",),
              deps=((9, 9),)),
        _spec("Handover to operations", 1,
              ("Project Manager", "Site Engineer"),
              deps=((10, 3), (10, 5), (10, 6))),
    )),
    ("Documentation & Handover", (
        _spec("As-built drawings", 3, ("QA Inspector", "Site Engineer"),
              deps=((10, 3),)),
        _spec("Maintenance manuals", 3,
              ("Mechanical Fitter", "QA Inspector"), deps=((10, 3),)),
        _spec("Spare-parts list", 1, ("Logistics Coordinator",),
              deps=((10, 3),)),
        _spec("Warranty & documentation package", 1, ("Project Manager",),
              deps=((11, 0), (11, 1), (11, 2))),
        _spec("Lessons-learned meeting", 1,
              ("Project Manager", "QA Inspector"), deps=((11, 3),)),
        _spec("Project closure & handover", 0, (),
              milestone=True, deps=((11, 4),)),
    )),
)


def validate_plan() -> None:
    """Fail fast if the authored scenario itself is malformed."""
    leaf_total = sum(len(tasks) for _, tasks in PHASES)
    if not (100 <= leaf_total <= 150):
        raise RuntimeError(
            f"sample must have 100–150 mappable tasks, got {leaf_total}"
        )
    if not (10 <= len(PHASES) <= 15):
        raise RuntimeError(
            f"sample must have 10–15 phases, got {len(PHASES)}"
        )
    resource_names = set(RESOURCE_NAMES)
    for phase_idx, (_, tasks) in enumerate(PHASES):
        for task_idx, spec in enumerate(tasks):
            if not spec["milestone"] and not spec["resources"]:
                # Only milestones may have no resource assignment.
                raise RuntimeError(
                    f"task ({phase_idx},{task_idx}) has no resource assignment"
                )
            if spec["milestone"] and spec["duration"] != 0.0:
                raise RuntimeError(
                    f"task ({phase_idx},{task_idx}) milestone must be 0d"
                )
            for resource in spec["resources"]:
                if resource not in resource_names:
                    raise RuntimeError(
                        f"task ({phase_idx},{task_idx}) references unknown "
                        f"resource {resource!r}"
                    )
            for dep_phase, dep_task in spec["deps"]:
                if not (0 <= dep_phase < len(PHASES)):
                    raise RuntimeError(
                        f"task ({phase_idx},{task_idx}) bad dep phase "
                        f"{dep_phase}"
                    )
                if not (0 <= dep_task < len(PHASES[dep_phase][1])):
                    raise RuntimeError(
                        f"task ({phase_idx},{task_idx}) bad dep task "
                        f"({dep_phase},{dep_task})"
                    )


def find_task(
    name: str,
) -> tuple[int, int, dict]:
    """Locate a leaf task by its exact name; fails loudly when absent."""
    for phase_idx, (_, tasks) in enumerate(PHASES):
        for task_idx, spec in enumerate(tasks):
            if spec["name"] == name:
                return phase_idx, task_idx, spec
    raise RuntimeError(f"sample plan has no task named {name!r}")


def _connect_com():
    """Import pywin32 lazily AFTER the explicit ``--i-understand`` guard."""
    import pythoncom
    import pywintypes
    import win32com.client

    pythoncom.CoInitialize()
    return win32com.client, pywintypes


def _set_outline_level(task, level: int) -> None:
    """Place a task at ``level`` (1-based) in the WBS hierarchy."""
    try:
        task.OutlineLevel = level
    except (AttributeError, TypeError):
        # Some MSP builds expose OutlineLevel read-only; indent instead.
        for _ in range(level - 1):
            task.IndentTask()


def build_project(app, pywintypes) -> object:
    """Create the sample project in Microsoft Project and return it.

    Never touches pre-existing open documents — only the NEW project
    this function itself creates.
    """
    import datetime as _dt

    pre_existing = [
        app.Projects.Item(i).Name for i in range(1, app.Projects.Count + 1)
    ]
    print(f"pre-existing open projects: {pre_existing or 'none'}")
    print("creating a fresh, private project document …")

    project = app.Projects.Add()
    # Activate() can fail on COM-created (invisibly hosted) projects; it
    # is optional — the saved file is what matters, not the UI focus.
    try:
        project.Activate()
    except pywintypes.com_error as exc:
        print(f"note: could not focus the new project window ({exc})")
    project.StatusDate = _dt.datetime(*STATUS_DATE, 8, 0, 0)

    print(f"adding {len(RESOURCE_NAMES)} resources …")
    for name in RESOURCE_NAMES:
        project.Resources.Add(name)

    print(f"adding tasks for {len(PHASES)} phases …")
    leaf_tasks: dict[tuple[int, int], object] = {}
    for phase_idx, (phase_name, tasks) in enumerate(PHASES):
        summary = project.Tasks.Add(phase_name)
        _set_outline_level(summary, 1)
        for task_idx, spec in enumerate(tasks):
            task = project.Tasks.Add(spec["name"])
            _set_outline_level(task, 2)
            task.Duration = spec["duration"] * 480  # minutes (8h/day)
            if spec["resources"]:
                task.ResourceNames = ", ".join(spec["resources"])
            if spec["progress"]:
                task.PercentComplete = spec["progress"]
            if spec["notes"]:
                task.Notes = spec["notes"]
            leaf_tasks[(phase_idx, task_idx)] = task

    print("creating predecessor links …")
    for phase_idx, (_, tasks) in enumerate(PHASES):
        for task_idx, spec in enumerate(tasks):
            task = leaf_tasks[(phase_idx, task_idx)]
            preds = [leaf_tasks[key] for key in spec["deps"]]
            if not preds:
                continue
            ids = [str(int(pred.ID)) for pred in preds]
            task.Predecessors = ",".join(ids)

    print(f"created {project.Tasks.Count} rows "
          f"({sum(len(tasks) for _, tasks in PHASES)} leaf tasks + "
          f"{len(PHASES)} summaries)")
    return project


def generate_sample(output_path: Path) -> Path:
    """Create, save, and leave open a NEW MSP project under ``output_path``."""
    if output_path.exists():
        raise RuntimeError(
            f"refusing to overwrite existing file {output_path}; move it "
            "aside first, then re-run the generator"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    win32com, pywintypes = _connect_com()
    app = win32com.Dispatch("MSProject.Application")
    project = build_project(app, pywintypes)
    app.FileSaveAs(str(output_path))
    print(f"saved sample to {output_path}")
    print(
        "NOTE: the generated project is left OPEN in Microsoft Project. "
        "Close it or keep it open — the application reads whatever "
        "project is currently active."
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the bundled 'Production Line Commissioning' sample "
            "Microsoft Project file (see module docstring)."
        )
    )
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help=(
            "Explicitly authorize driving Microsoft Project via COM to "
            "create and save a NEW project document. Required."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the authored scenario without touching Microsoft Project.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Target .mpp path (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    validate_plan()
    print("authored scenario validated.")

    if args.dry_run:
        leaves = sum(len(tasks) for _, tasks in PHASES)
        print(
            f"dry-run OK: {len(PHASES)} phases, {leaves} leaf tasks, "
            f"{len(RESOURCE_NAMES)} resources, target {args.output}"
        )
        return 0

    if not args.i_understand:
        parser.error(
            "refusing to drive Microsoft Project: pass --i-understand to "
            "authorize creating and saving a NEW project document"
        )
    try:
        generate_sample(args.output)
    except Exception as exc:
        print(f"generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
