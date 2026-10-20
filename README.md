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
- Unit tests for the domain.

Not implemented yet (and **not claimed to work**):

- Microsoft Project integration (planned: COM/pywin32)
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
python -m pytest
```

## Roadmap

1. **Done** — initial domain model + tests + documentation.
2. Next — application use cases for posing and answering a decision problem.
3. Microsoft Project integration (COM/pywin32) to capture `ProjectState`.
4. JEV integration (API) to evaluate candidate decisions.
5. Streamlit UI for planner/controllers.
