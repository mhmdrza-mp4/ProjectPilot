# Architecture

A layered decision-support application. Dependencies point inward;
technology lives at the edges only. This page describes what the code
actually does today — nothing planned, nothing aspirational.

```
        ┌──────────────────────────────────────────────────────┐
        │  Terminal UI (tui/, Textual + Rich)  thin consumer   │
        │  widgets, formatting, workers, key bindings only     │
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
        │ → ranking → review    │   │                             │
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
task status. Standard library only; no MSP, JEV, or Textual imports;
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

Also in the package: `tui/` — the terminal presentation layer (see
below), and `config.py` — the minimal
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

### `tui/` — the product UI (thin, deliberately)

The Textual + Rich terminal dashboard (plus the `tui/data.py` bridge
that is the ONLY layer touching the application functions). Iron rules:

- imports Textual/Rich, domain models, and an enumerated bridge of
  workflow/loader/evaluator functions — nothing else;
- **never** imports or calls pipeline internals directly
  (`run_decision_pipeline`, `score_session_with_evaluations`,
  `create_decision_result`, `review_decision_result`,
  `analyze_schedule`, `analyze_resource_conflicts`) or any MSP/SDK
  library; COM runs inside `@work(thread=True)` workers around the
  existing loader;
- contains **no business rules**: every displayed number comes from an
  immutable result object; a stale result (changed session) is refused
  and never displayed with mismatched context;
- the connection status is real: CONNECTED only after an actual read;
  failures surface as structured WHAT/WHY/NEXT text, and the JEV mode
  toggle stays OFFLINE unless a key actually resolves.

## Why `DecisionWorkflow` exists

every user-visible action (load, set period, add/remove candidate,
confirm, run) is a **function returning an immutable new
`DecisionWorkflowState`** — a frozen dataclass of pointers to existing
models. Consequences:

- no hidden mutation: a prior result references its own snapshot and
  cannot be silently invalidated; instead the workflow *knows* staleness
  (`result_is_stale` compares sessions), and the UI surfaces it;
- the terminal UI cannot reach around the workflow: every display
  action goes through the same seam;
- injectable everything: the loader and the evaluator are plain
  callables, which is why the same workflow runs against live MSP COM
  or a fake in tests — one product, no special cases.

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

**Read-only scope.** The no-write guarantee applies to the product's
Microsoft Project integration, including the terminal UI. The
repository is not write-free: `scripts/msp_fixture_author.py` is a
deliberately write-capable test tool
that refuses to run without its explicit `--i-understand` opt-in. It
is not used by the product flow.

## Testing posture

520+ hermetic tests (domain/application + Textual `run_test` UI tests)
run on any platform
without MSP, JEV, network, or keys; `pytest -m msp` / `pytest -m jev`
opt-in integration suites exist for machines that have those
integrations, and skip themselves automatically otherwise. The MSP path
was additionally validated against a real Microsoft Project (read-only)
in a dedicated validation pass, including live runs of the terminal UI
on the bundled sample project.
