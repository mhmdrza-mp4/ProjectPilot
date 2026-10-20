# 02 - Architecture

## Overview

The Project Decision System uses a **layered architecture** with a
technology-independent domain at its center:

```
presentation
    |
application
    |
domain           <- pure business logic, no external imports

infrastructure
    |
implemented via interfaces in domain/application terms
```

## Source Layout

```
src/project_decision/
    domain/           pure business objects (Project, Task, Decision, ...)
    application/      use-case logic (empty for now)
    infrastructure/   Microsoft Project + JEV adapters (empty for now)
    presentation/     Streamlit UI (empty for now)
```

## Layers and Responsibilities

### Domain (`src/project_decision/domain/`)

- Owns the business concepts: `Project`, `Task`, `Resource`, `Milestone`,
  `Constraint`, `ProjectState`, `DecisionProblem`, `Decision`,
  `DecisionEvaluation`, `DecisionRecommendation`.
- Contains **no** Microsoft Project, JEV, Streamlit, or framework code.
- Uses only the **Python standard library** (dataclasses, `datetime`,
  `enum`).
- Enforces basic invariants (valid ranges, referential integrity between
  tasks/resources, feasibility consistency, ...).

### Application (`src/project_decision/application/`)

- Will contain the use cases: building a `DecisionProblem` from a
  `ProjectState`, obtaining candidate `Decision` objects, having them
  evaluated, and producing a `DecisionRecommendation`.
- Coordinates domain objects and infrastructure interfaces.
- Currently **empty** — implemented together with the JEV integration.

### Infrastructure (`src/project_decision/infrastructure/`)

- Will contain adapters to external systems:
  - **Microsoft Project**: COM integration (pywin32) that maps MS Project
    data into domain objects and captures a `ProjectState`.
  - **JEV**: API client that receives a `DecisionProblem` (plus candidate
    decisions) and returns `DecisionEvaluation` results.
- Depends **inward** on domain/application interfaces; the domain never
  imports infrastructure.
- Currently **empty**.

### Presentation (`src/project_decision/presentation/`)

- A future **Streamlit** UI for planner/controllers.
- Depends on the application layer only.
- Currently **empty**.

## Dependency Direction

Dependencies point **inward**: `presentation -> application -> domain`.
Infrastructure implements interfaces defined in domain/application terms and
therefore also depends inward. Nothing below the presentation layer knows
that Streamlit, COM, or JEV exist.

## Why the Domain Is Isolated

- **From Microsoft Project**: MS Project has its own scheduling semantics
  (dependency types, constraint types, calendars, timesheets). Mapping those
  into a small mirror vocabulary owned by the domain (`Task`, `Constraint`,
  `Milestone`, ...) keeps the decision model stable if we later adapt the
  import layer, switch data sources, or test with hand-made projects.
- **From JEV**: JEV will evaluate decisions, but its request/response shape
  may change. If the domain spoke JEV's API, every JEV change would ripple
  into the core. Instead, the application layer will translate between
  domain objects (`DecisionProblem`, `DecisionEvaluation`) and JEV's API.
- **Practical benefit**: the entire domain can be unit-tested with plain
  Python, without COM, a running JEV, or a browser.

## Microsoft Project Integration (infrastructure/msp/)

### Why Microsoft Project code belongs in infrastructure

Microsoft Project is an external Windows application reached through COM.
That is a transport detail: the decision model (domains, use cases, later
JEV) must not change if Microsoft Project's automation quirks are handled
differently, or if schedules ever come from another source. Only
`src/project_decision/infrastructure/msp/` may import `win32com`,
`pythoncom`, or `pywintypes`.

### Why COM must not leak into the domain

- **Portability/testing**: the domain is unit-tested anywhere, without
  Windows or a Microsoft Project license. COM objects are patched-in,
  version-quirky, and side-effectful (connecting may even *start* the
  application).
- **Stability**: Microsoft Project's automation surface is enormous and
  version-dependent. A small, explicitly mapped vocabulary in the domain
  keeps the rest of the system stable across MSP versions.
- **Separation of concerns**: mapping is a translation problem, not
  business logic. The domain defines what a task *is*; the mapper decides
  how a COM row expresses that.

### How MSP data is mapped into domain objects

```
MspComClient (com_client.py)   raw COM: connect, ActiveProject, minutes-per-day,
   |                           lazy win32com imports, release-only disconnect
   v
msp_mapper.py (pure functions) explicit field mapping; no COM import, unit-
   |                           testable with COM-like stand-in objects
   v
MspProjectAdapter (msp_adapter.py)
   |                           skips summary/inactive/blank rows, maps milestone
   v                           tasks twice (0-duration task + Milestone), maps
domain objects                 task deadlines to DEADLINE constraints
```

Mapped fields (the smallest useful set):

| MSP (COM) | Domain |
| --- | --- |
| `Project.Name` | `Project.id` / `Project.name` |
| `Task.UniqueID` | `Task.id` (also dependency/resource reference keys) |
| `Task.Name`, `Duration`, `PercentComplete`, `Start`, `Finish` | `Task.name`, `duration_days` (minutes → days), `progress` (0–100 → 0–1), `start`, `finish` (pywintypes datetime → `date`) |
| `Task.TaskDependencies[].From.UniqueID` | `Task.dependency_ids` |
| `Task.Resources[].UniqueID` | `Task.assigned_resource_ids` |
| `Resource.UniqueID`, `Name`, `MaxUnits` | `Resource.id`, `name`, `capacity_units` |
| milestone-flagged tasks | domain `Milestone` (date = finish date) |
| `Task.Deadline` | `Constraint(kind=DEADLINE)` |

Deliberate behavior for imperfect COM data:

- **Skipped** (not errors): summary tasks, inactive tasks, blank rows,
  missing optional values (`PercentComplete` → 0, no deadline → no constraint).
- **Dropped references**: dependencies/assignments pointing at skipped rows
  (e.g. a predecessor that is a summary task) are removed, because the
  domain requires references to resolve.
- **Errors** (`MspMappingError`): work tasks without UniqueID or Name can
  never become domain objects — this is serious data *inside a mapped row*,
  so it surfaces.
- **Errors** (`MspNotAvailableError`, `MspNoActiveProjectError`,
  `MspReadError`): COM-level failures are translated at the boundary;
  raw `com_error` never escapes `infrastructure/msp`.

### Current read-only scope

The first integration **reads only**. Nothing writes to Microsoft Project:
no date changes, no duration changes, no resource assignments, no saves.
`disconnect()` releases COM references only and never quits the user's
application or closes their document. Writing decisions back into
Microsoft Project is a later, separate step after the read pipeline is
proven.

### Limitations of the COM integration

- Windows-only; requires Microsoft Project installed and licensed.
- Reading the **active project** only; opening project files is not
  supported yet.
- COM durations are minutes; the day conversion uses
  `Application.MinutesPerDay` (fallback: 8-hour day).
- Dependency types (FS/SS/...), lags, constraint types (SNET/SNLT, ...),
  calendars, and cost data are not mapped yet.
- Dependencies on non-mapped rows are dropped (see above), so a schedule
  with predecessors on summary rows loses those edges.
- `Project.id` currently equals the project Name (COM Projects have no
  stable short id exposed in the fields we read).
- Task fields are read once; the integration does **not** subscribe to
  change events (the planner re-runs the read for fresh state).
- Errors in one milestone's date skip that milestone; a task whose
  essential fields are missing aborts the read with ``MspMappingError``.

### Running the integration manually

See "Manual read-only smoke test" in `README.md`, or run:

```text
python scripts\manual_msp_read.py
```

## Current Implementation Status

| Area | Status |
| --- | --- |
| Domain model (10 concepts, validated, tested) | **Implemented** |
| Microsoft Project read integration (COM, read-only) | **Implemented** (unit-tested; integration tests require MS Project) |
| Application use cases | Not implemented |
| Microsoft Project integration (COM) | Not implemented |
| JEV integration (API) | Not implemented |
| Streamlit presentation | Not implemented |
| Database / persistence | Not implemented (none planned so far) |
| Authentication | Not implemented |

## Future Integration Points

1. **Capture (implemented, read-only)**: `MspProjectAdapter` reads the
   active Microsoft Project (COM/pywin32) and produces a domain `Project`
   / `ProjectState`.
2. **Pose problem**: application use case wraps a `ProjectState`, objective,
   and constraints into a `DecisionProblem`.
3. **Evaluate**: infrastructure JEV client evaluates candidate `Decision`
   objects and returns `DecisionEvaluation` results.
4. **Recommend**: application use case selects a `DecisionRecommendation`
   with reasoning.
5. **Write-back (not implemented)**: later, apply the confirmed decision
   to Microsoft Project via COM (explicit second write layer).
6. **Present**: Streamlit screen shows candidates, evaluations, the
   recommendation, and its explanation.
