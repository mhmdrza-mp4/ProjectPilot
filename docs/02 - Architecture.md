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

## Current Implementation Status

| Area | Status |
| --- | --- |
| Domain model (10 concepts, validated, tested) | **Implemented** |
| Application use cases | Not implemented |
| Microsoft Project integration (COM) | Not implemented |
| JEV integration (API) | Not implemented |
| Streamlit presentation | Not implemented |
| Database / persistence | Not implemented (none planned so far) |
| Authentication | Not implemented |

## Future Integration Points

1. **Capture**: infrastructure adapter reads a Microsoft Project file/COM
   session and produces a `ProjectState`.
2. **Pose problem**: application use case wraps a `ProjectState`, objective,
   and constraints into a `DecisionProblem`.
3. **Evaluate**: infrastructure JEV client evaluates candidate `Decision`
   objects and returns `DecisionEvaluation` results.
4. **Recommend**: application use case selects a `DecisionRecommendation`
   with reasoning.
5. **Present**: Streamlit screen shows candidates, evaluations, the
   recommendation, and its explanation.
