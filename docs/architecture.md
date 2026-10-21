# Architecture

A layered decision-support application. Dependencies point inward;
technology lives at the edges only. This page describes what the code
actually does today — nothing planned, nothing aspirational.

```
        ┌──────────────────────────────────────────────────────┐
        │  Streamlit UI (app/)              thin consumer      │
        │  widgets, formatting, charts, session state only    │
        └──────────────────────────┬───────────────────────────┘
                                   ↓ (workflow functions only)
        ┌──────────────────────────────────────────────────────┐
        │  DecisionWorkflow (application/decision_workflow.py) │
        │  immutable states: load → period → session → run     │
        └──────┬───────────────────────────────┬───────────────┘
               ↓                               ↓
        ┌──────────────────────┐   ┌────────────────────────────┐
        │ DecisionPipeline      │   │ loaders / evaluators        │
        │ schedule → conflicts  │   │ MSP read-only COM adapter   │
        │ → consequence → score │   │ JEV consequence evaluation  │
        │ → ranking → review    │   │ Demo Mode fixture           │
        └──────────┬────────────┘   └────────────────────────────┘
                   ↓
        ┌──────────────────────────────────────────────────────┐
        │  Domain (domain/)  — pure models, stdlib only         │
        └──────────────────────────────────────────────────────┘
```

(Layer summary, not a call graph; arrows read "consumes".)

## Layers

### `domain/` — pure business models

`Project`, `Task`, `Resource`, `Milestone`, `Constraint`,
`PlanningPeriod`, `PlanningSession`, `ProjectState` (the snapshot
concept), `Decision`/`DecisionEvaluation`/`DecisionRecommendation`,
task status. Standard library only; no MSP, JEV, or Streamlit imports;
invariants enforced in `__post_init__`.

### `application/` — use cases, no I/O

- `decision_workflow.py` — the **one** user-level state machine
  (see below).
- `project_loader.py` — turns a loader callable into a `ProjectState`
  snapshot; MSP errors propagate, never fake states.
- `schedule_analysis.py`, `resource_conflicts.py` — deterministic
  analysis over a snapshot.
- `candidate_discovery.py` / `planning_session.py` — default funnel +
  immutable add/remove.
- `consequence_evaluation.py` — the JEV boundary application service:
  builds the `{"task": {"name", "notes"}}` question, applies
  `CD = score / 3`, keeps structured failures, no retries.
- `priority_scoring.py` / `priority_ranking.py` / `decision_review.py`
  — the deterministic index, order, and review aggregation
  (see `docs/decision-model.md`).
- `decision_pipeline.py` — orchestrates the stages in exactly one
  pass each; result objects are immutable data.

Also in the package: `demo/` — the offline Demo Mode fixture (data
only, built from domain models), and `config.py` — the minimal
`.env`/`TYPESAFE_API_KEY` reader (key never logged or exposed).

### `infrastructure/` — technology adapters

- `msp/` — `MspComClient` (raw COM isolation), `MspProjectAdapter`
  (read + build), `msp_mapper.py` (field mapping, minutes→days),
  typed `MspError` hierarchy. **No Microsoft Project writes**: no Save,
  FileSave, or MSP task/resource mutation anywhere. The adapter may omit
  in-memory references to rows it cannot map; those omissions are counted
  and attached to the snapshot as `capture_warnings` (never invented).
- `jev/` — the TypeSafe SDK client: one `post` per System One
  request, typed answers, structured errors; a missing key is a
  configuration state, not a crash.

### `presentation/` — the legacy dashboard

Kept as a historical viewer for the active project and the JEV dev test
panel; the product flow lives in `app/` (Streamlit). Its MSP view is
read-only, but its **Decision problems** tab creates/updates/deletes
local JSON records under `data/decision_problems/` — local application
persistence, not a Microsoft Project write.

### `app/` — the product UI (thin, deliberately)

Widgets, formatting, charts, helpers that only *display* what the
application layer produced, and Streamlit session state. Iron rules,
enforced by AST tests (`tests/test_ui_app.py`):

- imports `streamlit`, `plotly`, a stdlib handful, domain models, and
  an enumerated allow-list of application modules — nothing else;
- **never** imports or calls pipeline functions
  (`run_decision_pipeline`, `score_session_with_evaluations`,
  `create_decision_result`, `review_decision_result`,
  `validate_decision_result`, `analyze_schedule`,
  `analyze_resource_conflicts`) or any MSP/SDK library;
- contains **no business rules**: results live in session state, and
  a stale result (changed session or parameters) is cleared with a
  message, never displayed with mismatched context.

## Why `DecisionWorkflow` exists

every user-visible action (load, set period, add/remove candidate,
confirm, run) is a **function returning an immutable new
`DecisionWorkflowState`** — a frozen dataclass of pointers to existing
models. Consequences:

- no hidden mutation: a prior result references its own snapshot and
  cannot be silently invalidated; instead the workflow *knows* staleness
  (`result_is_stale` compares sessions), and the UI surfaces it;
- UI cannot reach around the workflow: the layering test forbids direct
  pipeline calls, so all decisions flow through the same seam;
- injectable everything: the loader and the evaluator are plain
  callables, which is why the same workflow runs against live MSP COM,
  a fake in tests, or the Demo Mode fixture — one product, three
  sources, zero special cases.

## Why the pipeline is a separate object

`run_decision_pipeline(state, session, evaluator,
scoring_parameters=?)` executes schedule analysis → conflict analysis →
consequence evaluation → scoring → ranking → review, each stage exactly
once, deterministically, with no try/except to hide failures. The result
(`DecisionPipelineResult`) deliberately does not store the scoring
parameters, so the UI can honestly detect "parameters changed → rerun
required" instead of guessing.

## External boundaries

- **MSP (read-only):** the application defines a loader boundary
  (`Callable[[], ProjectState]`); only `infrastructure/msp` knows COM
  exists. Failures are typed (`MspNoActiveProjectError`,
  `MspNotAvailableError`, ...) and surface as structured UI messages.
  References to rows that cannot be mapped are omitted from the snapshot
  and reported as capture warnings — never invented.
- **JEV:** the application defines the evaluator boundary
  (`ConsequenceEvaluator`). The JEV question carries ONLY task name and
  notes — slack, criticality, successors, dates, resources, and scores
  never leave the process. Live failures become structured
  per-task states (`failed` / `unavailable`), never crashes.
- **Demo Mode** (`project_decision.demo`): the third loader — a fixed
  in-repo snapshot through the *same* boundary, so the product is fully
  explorable offline with the identical workflow and pipeline.

**Read-only scope.** The no-write guarantee applies to the product's
Microsoft Project integration. The repository is not write-free: the
legacy Decision-problems UI writes local JSON under `data/`, and
`scripts/msp_fixture_author.py` is a deliberately write-capable test tool
that refuses to run without its explicit `--i-understand` opt-in. Neither
is used by the product flow; local JSON persistence is application data,
never a Microsoft Project write.

## Testing posture

550+ hermetic tests (domain/application/UI/AppTest) run on any platform
without MSP, JEV, network, or keys; `pytest -m msp` / `pytest -m jev`
opt-in integration suites exist for machines that have those
integrations, and skip themselves automatically otherwise. The MSP path
was additionally validated against a real Microsoft Project (read-only)
in a dedicated validation pass, including a live Streamlit run.
