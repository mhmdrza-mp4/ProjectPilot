# 02 - Architecture

> **Historical design record (early stage).** The CURRENT architecture
> description lives in [docs/architecture.md](architecture.md). This
> page documents the original layered design from the founding steps.

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
    application/      use-case logic (analysis, state loading)
    infrastructure/   Microsoft Project adapter (implemented, read-only)
    presentation/     Streamlit dashboard (implemented, initial version)
```

## Layers and Responsibilities

### Domain (`src/project_decision/domain/`)

- Owns the business concepts: `Project`, `Task`, `Resource`, `Milestone`,
  `Constraint`, `ProjectState`, `DecisionProblem`, `Decision` (used as a
  decision *alternative*), `EvaluationCriterion`, `DecisionAssumption`,
  `DecisionEvaluation`, `DecisionRecommendation`, plus task status
  classification.
- Contains **no** Microsoft Project, JEV, Streamlit, or framework code.
- Uses only the **Python standard library** (dataclasses, `datetime`,
  `enum`, `uuid`).
- Enforces basic invariants (valid ranges, referential integrity between
  tasks/resources, feasibility consistency, non-empty titles/names, ...).
- A `DecisionProblem` records the posed question: title, description,
  objective, alternatives, criteria, constraints (reusing the same
  `Constraint` model the project uses), assumptions, and — optionally — the
  linked `ProjectState` snapshot. `evaluation_gaps()` states what is still
  missing before evaluation (>= 2 alternatives, >= 1 criterion); the problem
  itself contains no evaluation, ranking, or effect estimation.

### Application (`src/project_decision/application/`)

- **Implemented**:
  - `project_analysis.py` — basic project indicators over a
    `ProjectState` (see "Project Analysis" below). Pure functions:
    domain in, dataclasses out, no external systems.
  - `project_state_service.py` — loads a `ProjectState` through the MSP
    facade and translates expected infrastructure errors
    (MSP not available, no active project, unreadable data) into
    `LoadedProjectState` results with actionable messages, for the
    dashboard to display. Expected failures become UI data, not crashes;
    an error never degrades into an empty project.
  - `decision_problem_store.py` — local JSON persistence for decision
    problems (`data/decision_problems/<id>.json`, one file per problem;
    no database) plus the structured problem representation
    (`problem_to_dict` / `problem_from_dict`): the clean, serializable
    form the future JEV mapping will consume. All domain validation
    applies on deserialization, and unreadable files are surfaced, not
    hidden.
- **Not yet implemented**: the JEV evaluation use case itself (sending a
  posed problem to JEV and turning its response into
  `DecisionEvaluation` results). No API key handling, no invented
  endpoints.

### Infrastructure (`src/project_decision/infrastructure/`)

- **Implemented**: the Microsoft Project adapter (read-only, see the
  "Microsoft Project Integration" section below) **and** the official
  TypeSafe/Jev integration (`infrastructure/jev/`, see the "Jev
  Integration (infrastructure/jev/)" section below).
- **Not yet implemented**: the *decision-flow* side of Jev — mapping a
  posed `DecisionProblem` into Jev questions and Jev answers into
  `DecisionEvaluation` results.
- Depends **inward** on domain/application terms; the domain never
  imports infrastructure.

### Presentation (`src/project_decision/presentation/`)

- **Implemented**: the Streamlit dashboard
  (`presentation/dashboard.py`) with three tabs:
  - *Project overview* — the read-only project view (state loading via
    `project_state_service`, metrics via `project_analysis`).
  - *Decision problems* (`presentation/decision_problem_page.py`) — the
    decision-problem authoring UI: title/description/objective;
    add/edit/remove alternatives, criteria, constraints, and
    assumptions; a readiness review; a JSON structured preview (always
    generated from the actual domain data); and save/load of problems
    via the application store. It knows only about the application
    layer, never touches COM, contains no business rules, and never
    modifies the Microsoft Project file.
  - *Jev test (dev)* (`presentation/jev_panel.py`) — a clearly labeled
    development/verification panel: shows whether `TYPESAFE_API_KEY` is
    configured (never the value), sends the predefined example state
    with one official question of each type (Choice, Score, Noul), and
    displays structured answers (probabilities, legend, confidence) or
    safe error messages. Not connected to the project decision
    workflow.

## Project State and the Analysis Flow

The runtime flow for the current stage:

```
Microsoft Project
    ↓  MspComClient          (COM, read-only)
    ↓  MspProjectAdapter     (explicit field mapping)
    ↓  XxxProject / ProjectState   (domain)
    ↓  application: project_state_service + project_analysis
    ↓  presentation: Streamlit dashboard
```

### What ProjectState represents

`domain.project_state.ProjectState` is a frozen snapshot of a project at
`captured_at` — the input for all analysis and (later) decisions. It
contains ordinary domain objects only, never COM objects:

| Field | Meaning |
| --- | --- |
| `project_id`, `project_name` | project identity; name from the source project |
| `captured_at` | snapshot creation time |
| `tasks`, `resources`, `milestones`, `constraints` | copied project content |
| `project_start`, `project_finish` | scheduled start/finish as reported; `None` = not available |
| `status_date` | MSP status date; `None` = none set |
| `baseline_start`, `baseline_finish` | saved baseline dates; `None` = **no baseline saved** |

Design decisions:

- **`None` is the explicit representation of "unavailable"** (e.g. no
  baseline has been saved, no status date set). Missing values are never
  guessed, defaulted to today's date, or fabricated anywhere in the
  pipeline.
- Created via `Project.capture_state(captured_at)`; content is copied so
  later edits to the live project cannot change a snapshot that decisions
  will be evaluated against.
- Fields duplicated from `Project` are exactly the fields analysis (and
  later JEV) needs plus the capture metadata — nothing more.

### Project analysis (application/project_analysis.py)
Indicators implemented today (classification rules documented in
`domain/task_status.py` and the module docstring):

| Indicator | Definition |
| --- | --- |
| Total / Completed / In-progress / Not-started tasks | classified by **progress only**: `>= 1.0` completed, `0..1` exclusive in progress, `== 0` not started. No date comparison — a 0 % task is *not started*, never "overdue" |
| Tasks with missing dates | start AND finish both unavailable in the state |
| Planned start / finish / status date | exactly the project-level values reported by the source; `None` when unavailable |
| Overall completion | duration-weighted mean task progress; `None` when the total duration is zero (not definable) |
| Schedule delay vs. baseline | scheduled finish minus baseline finish in days (0 = on/ahead of baseline); **available only when a baseline exists** |

Deliberately honest behaviors:

- Without baseline data the delay metric reports "cannot currently be
  calculated" with an explanation — it is not estimated from today's date.
- Whether the scheduled finish is later than *today* is never interpreted
  as delay.
- No optimization, recommendations, candidate decisions, JEV calls, ML,
  or prediction exists in this stage.

### Decision problem authoring

The second Flow (implemented in this stage):

```
ProjectState (loaded snapshot, optionally linked)
    ↓  planner authoring (Streamlit "Decision problems" tab)
DecisionProblem  (title, description, objective, alternatives,
                  criteria, constraints reused from domain.Constraint,
                  assumptions — all validated by the domain)
    ↓  application.decision_problem_store (JSON, no database)
data/decision_problems/<id>.json   +  JSON preview in the UI
    ↓  (later stage)
JEV API mapping  (planned; consumes the serialized representation)
```

Properties of the current authoring stage:
- A problem may be authored without any ranking; the dashboard never
  states that one alternative is better than another, and no schedule,
  cost, or risk effect is ever calculated in this stage.
- The JSON preview and the saved files are the same serialized
  representation (`application.decision_problem_store.problem_to_dict`),
  so what the user reviews is exactly what a future JEV mapping receives.
- Problems are plain local JSON files; there is no API key, no `.env`
  configuration, and no database.

## Jev Integration (infrastructure/jev/)

This stage connects the application to the **official** TypeSafe Jev
API (System One model `jev-latest`) using the official `typesafe-sdk`
Python SDK. Jev is TypeSafe's System One decision model: it receives
one *state* plus several typed *questions* in a single request and
returns one typed answer per question. In this project it is planned as
the evaluator of posed decision problems — it is **not** a schedule
optimizer, and this stage deliberately does **not** connect Jev to the
project decision workflow yet.

### Configuration (src/project_decision/config.py)

A deliberately minimal configuration layer:
- loads a local `.env` file (`KEY=VALUE` lines; environment variables
  already set are never overridden); no `.env` parser dependency,
- reads `TYPESAFE_API_KEY` (the documented SDK variable name) into
  `TypesafeSettings`, whose `repr` masks the key, so logs and
  tracebacks can't leak it. `.env` is git-ignored; `.env.example`
  carries only an empty placeholder.

### Package layout (infrastructure/jev/)

| Module | Content |
| - | - |
| `client.py` | `TypeSafeJevClient` — the **only** module importing `typesafe_sdk`; builds the official `TypeSafeClient` with the configured key and `model="jev-latest"`, sends one request (`system_one`), maps answers, translates exceptions |
| `models.py` | SDK-free neutral dataclasses: `JevNoulQuestion`/`JevChoiceQuestion`/`JevScoreQuestion` (questions) and `JevNoulAnswer`/`JevChoiceAnswer`/`JevScoreAnswer`/`JevAnswerSet` (answers, incl. probabilities/legend/confidence and token usage) |
| `exceptions.py` | typed failures: configuration, authentication (401), connection/timeout, invalid request (400/422), API error (other statuses, with status + request id), unexpected response |

Application-side, `application/jev_service.py` wraps the client with
the `LoadedProjectState` pattern: `send_jev_questions(...)` returns a
`JevCallResult` (answers *or* a safe `error_message` + `error_kind`);
expected failures never raise into the UI, and truly unexpected bugs
still propagate. The API key is redacted from anything echoed back in
error bodies.

### Guarantees and boundaries of this stage

- All information comes from the official TypeSafe documentation; no
  endpoints, fields, or SDK behavior are invented.
- Multiple questions share one request; each is answered against the
  same state, keyed by its question id.
- Probabilities and confidence are preserved, not discarded — they will
  matter later for "reliable enough to show or act on" decisions.
- The predefined example payload (a small project state + one question
  of each official type) is an integration-verification instrument
  used by dev panel and live test — its answers are **not** project
  decisions.
- Not implemented yet: mapping saved decision problems into Jev
  questions, answers into `DecisionEvaluation`, any automatic choice
  of project actions, or write-back.

### Live verification

- Streamlit: the "Jev test (dev)" tab sends the example payload on
  button press and renders structured answers.
- pytest: `pytest -m jev` runs the live integration test (requires
  `TYPESAFE_API_KEY`; auto-skipped otherwise and excluded from the
  default offline suite).

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
| `Project.Start`, `Project.Finish` | `Project.project_start` / `project_finish` |
| `Project.StatusDate` | `Project.status_date` (`None` when not set) |
| `Project.BaselineStart`, `Project.BaselineFinish` | `Project.baseline_start` / `baseline_finish` (`None` = no baseline saved) |
| `Task.UniqueID` | `Task.id` (also dependency/resource reference keys) |
| `Task.Name`, `Duration`, `PercentComplete`, `Start`, `Finish` | `Task.name`, `duration_days` (minutes → days), `progress` (0–100 → 0–1), `start`, `finish` (pywintypes datetime → `date`) |
| `Task.TaskDependencies[].From.UniqueID` | `Task.dependency_ids` |
| `Task.Resources[].UniqueID` | `Task.assigned_resource_ids` |
| `Resource.UniqueID`, `Name`, `MaxUnits` | `Resource.id`, `name`, `capacity_units` |
| milestone-flagged tasks | domain `Milestone` (date = finish date) |
| `Task.Deadline` | `Constraint(kind=DEADLINE)` |

Project-level date values that Microsoft Project leaves unset map to
`None` ("not available"); they are never invented. The same fields are
copied into every `ProjectState` snapshot, together with the project
name and capture time.

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

### Limitations of the current analysis and dashboard

- Indicators are counts and weighted progress only. Earned-value metrics
  (SPI/CPI) need baseline work/cost data and are out of scope so far.
- The delay indicator is baseline-based only; a delay relative to the
  *status date* or a milestone requires status-date data and defined
  comparison rules, and will be added only with real baseline coverage.
- Overall completion is duration-weighted across all tasks; it is not
  earned-value "% complete".
- Partial recoverability: the dashboard reloads the whole state on
  refresh; no caching or session history exists (each refresh creates a
  new snapshot).

### Running the integration manually

See "Manual read-only smoke test" in `README.md`, or run:

```text
python scripts\manual_msp_read.py
```

## Current Implementation Status

| Area | Status |
| --- | --- |
| Domain model (validated, tested) | **Implemented** |
| Microsoft Project read integration (COM, read-only) | **Implemented** (unit-tested; integration tests require MS Project) |
| ProjectState snapshots (incl. project dates and baseline) | **Implemented** |
| Project analysis (basic indicators) | **Implemented** |
| Streamlit dashboard (project view + decision problems) | **Implemented** |
| Decision problem authoring + local JSON persistence | **Implemented** |
| TypeSafe/Jev SDK integration (client, config, test panel) | **Implemented** (connection stage; not wired into the decision workflow) |
| Application decision use cases (problem → Jev → evaluations) | Not implemented |
| Write-back of decisions to Microsoft Project | Not implemented |
| Database / persistence | Not implemented (none planned so far) |
| Authentication | Not implemented |

## Launching the Dashboard

From the project root, with Microsoft Project installed (Windows) and a
project open:

```text
streamlit run src/project_decision/presentation/dashboard.py
```

The *Project overview* tab shows the active project's name, project
dates, task summary metrics, the task table (id, name, start, finish,
duration, progress, predecessors), and the delay indicator. Unavailable
values are displayed as "not available". Expected failures (Microsoft
Project not running, no project open, unreadable data) are shown as
actionable error messages — not tracebacks.

The *Decision problems* tab lets the planner author a decision problem
for the loaded project: title, description, objective, alternatives,
criteria, constraints, and assumptions; review each problem's readiness
before it is saved as a local JSON file
(data/decision_problems/<id>.json); the saved JSON doubles as the
structured preview for the later JEV integration. Nothing in this
dashboard modifies the Microsoft Project file.

The *Jev test (dev)* tab is a clearly labeled development panel: it
shows whether `TYPESAFE_API_KEY` is configured (value hidden), and on
button press sends the predefined example state — one official question
of each type (Choice, Score, Noul) — to the real TypeSafe API and
displays the structured answers (probabilities, legend, confidence) or
a safe error message. Expected failures are surfaced as messages, never
tracebacks, and never include the API key.

## Future Integration Points

1. **Capture (implemented, read-only)**: `MspProjectAdapter` reads the
   active Microsoft Project (COM/pywin32) and produces a domain `Project`
   / `ProjectState`.
2. **Pose problem (implemented)**: authoring stage wraps a
   `ProjectState`, title/description/objective, alternatives, criteria,
   constraints, and assumptions into a validated, locally persisted
   `DecisionProblem`.
3. **Connect (implemented)**: infrastructure Jev client evaluates
   arbitrary questions against arbitrary states through the official
   TypeSafe SDK — currently used by the dev panel and the live
   integration test only.
4. **Evaluate (next)**: application use case maps the saved
   `DecisionProblem` into Jev questions and Jev answers into
   `DecisionEvaluation` results, weighing probability/confidence
   before anything is shown or acted on. The mapping module will live
   in `infrastructure/` (or a dedicated application translator) and
   consume the serialized decision-problem representation produced by
   `decision_problem_store.problem_to_dict` — the domain stays free of
   JEV request/response shapes.
5. **Recommend**: application use case selects a `DecisionRecommendation`
   with reasoning.
6. **Present**: Streamlit screen shows candidates, evaluations, the
   recommendation, and its explanation.
7. **Write-back (not implemented)**: later, apply the confirmed decision
   to Microsoft Project via COM (explicit second write layer).
