# Project Decision System

A **decision-support layer around Microsoft Project** that helps project
planners and controllers choose project-control decisions.

## Problem

A planner can see the current project state in Microsoft Project, but turning
that state into the **right control decision** is hard: candidate actions
(add capacity, resequence, cut scope, ...) interact with task dependencies,
resource availability, milestones, and deadlines.

## Purpose

The system converts a captured project state into a **decision problem**
(state + objective + constraints), evaluates **candidate decisions**, and
outputs a **recommendation with an explanation**.

**Initial objective:** reduce project schedule delay.

## Current Scope

Implemented today:

- **Domain model** — `Project`, `Task`, `Resource`, `Milestone`,
  `Constraint`, `ProjectState`, `DecisionProblem`, `Decision`,
  `DecisionEvaluation`, `DecisionRecommendation`.
- **Microsoft Project read integration (read-only, Windows)** —
  `MspComClient` + `MspProjectAdapter` convert the *active* project into
  domain objects via COM/pywin32. Nothing is written back to Microsoft
  Project.
- Unit tests for domain + mapping (no Microsoft Project needed), plus
  optional integration tests requiring Microsoft Project.

Not implemented yet (and **not claimed to work**):

- Writing changes back to Microsoft Project
- JEV integration (planned: JEV API)
- Streamlit UI
- Database, authentication, Docker

## Architecture Overview

Layered, dependencies point inward:

```
presentation (Streamlit, later)
    |
application  (use cases, later)
    |
domain       (pure business objects, implemented now)

infrastructure (MS Project COM + JEV API adapters, later)
    |
depends on domain/application interfaces
```

The domain uses only the Python standard library and knows nothing about
Microsoft Project, JEV, or Streamlit. See `docs/02 - Architecture.md`.

## Running the Tests

Requires Python 3.12+.

```bash
pip install pytest
python -m pytest            # unit tests only (no Microsoft Project needed)

pytest -m msp               # integration tests; require Windows + Microsoft Project
                            # + pywin32 + an open project; auto-skip if unavailable
```

## Manual Read-Only Smoke Test (Windows + Microsoft Project)

With Microsoft Project installed and a project open:

```bash
pip install pywin32
python scripts\manual_msp_read.py
```

Example output:

```text
Connected to Microsoft Project
Active Project: Example Project
Tasks: 4  Resources: 3  Milestones: 1  Constraints: 1

Tasks:
1. Project Planning | id=2 | duration=0d | start=2026-06-02 | finish=2026-06-02 | progress=100%
2. Design | id=3 | duration=2d | start=2026-06-03 | finish=2026-06-04 | progress=40%
...
```

The script is strictly read-only.

## Roadmap

1. **Done** — initial domain model + tests + documentation.
2. **Done** — Microsoft Project read integration (COM/pywin32, read-only).
3. Next — application use cases for posing and answering a decision problem.
4. JEV integration (API) to evaluate candidate decisions.
5. Write-back of confirmed decisions to Microsoft Project (separate step).
6. Streamlit UI for planner/controllers.
