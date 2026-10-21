"""Application layer: use-case logic.

Implemented:
- project analysis (``project_analysis``): basic indicators over a
  ProjectState (pure functions, no external systems).
- schedule analysis (``schedule_analysis``): successor relationships
  and data-quality issues over a ProjectState; reports captured slack
  and criticality verbatim, derives nothing else.
- resource-conflict analysis (``resource_conflicts``): resource-sharing
  task pairs with overlapping scheduled dates, and explicitly UNKNOWN
  pairs when dates are insufficient; reports time overlap only, since
  no assignment-level capacity demand exists in the data model.
- decision problems (``decision_problem_store``): JSON-file persistence
  for posed decision problems, including the captured ProjectState.
- JEV connection (``jev_service``): sends prepared questions through
  the official TypeSafe SDK and returns structured answers as a
  ``JevCallResult``; not yet connected to the decision workflow.

- planning (``candidate_discovery``, ``planning_session``): computes
  the default candidates for a PlanningPeriod by fixed inclusive
  overlap rules, and creates/adjusts immutable PlanningSessions
  (add/remove always return new sessions; the ProjectState is never
  mutated).
- priority scoring (``priority_scoring``): deterministic per-task
  scores (0..100 index) for a PlanningSession from schedule pressure,
  successor impact, and a pre-normalized consequence-of-delay input
  (JEV is NOT called here); missing criteria are marked, never
  invented, and weights are never re-normalized. Ranking is NOT part
  of this module.
- consequence of delay via Jev (``consequence_evaluation``): evaluates
  each candidate's worst non-schedule business consequence of delay
  through the existing ``jev_service``/TypeSafe boundary (task name +
  notes only, levels 0..3, CD = score/3); missing notes, Jev failures,
  and low confidence are structured, isolated review signals — never
  fabricated scores. Ranking and the decision-problem mapping are NOT
  part of this module.
- priority ranking (``priority_ranking``): orders the per-task scores
  into a deterministic ``DecisionResult`` of ``RankedTask`` entries —
  rounded score, then slack, successors, evaluated CD, and task id as
  the stable break; resource conflicts are attached as display
  warnings (never rank or score inputs), a provisional CD counts as
  missing at its tier ("known before missing"), and unscored
  candidates rank last as honestly unscored. No UI, no persistence.
- decision review (``decision_review``): pure validation/aggregation
  over a DecisionResult — structural invariants become INVALID issues,
  review signals (provisional CD, low JEV confidence, failed or
  unavailable evaluations, missing schedule data, resource-conflict
  warnings, unscored candidates) become REVIEW_REQUIRED with
  deterministic counts; valid flag-free results are READY. Never
  recomputes scores, never calls JEV/MSP, never mutates anything.
- decision pipeline (``decision_pipeline``): ONE deterministic use
  case (``run_decision_pipeline``) orchestrating the existing stages —
  schedule analysis, resource conflicts, INJECTED consequence
  evaluation, scoring, ranking, review — exactly once each, into a
  composed ``DecisionPipelineResult``. It contains no decision logic,
  never imports the TypeSafe SDK, and never swallows programming
  errors.
- project loading (``project_loader``): the application boundary for
  "load this project" — an injected (duck-typed) MSP adapter or a
  managed real-chain convenience produces a ProjectState through the
  existing ``infrastructure.msp`` adapter/mapper, strictly read-only.
  Load failures propagate as the existing MspError hierarchy; an
  empty project stays distinguishable from a failed load. No COM
  module is ever imported here.
- decision workflow (``decision_workflow``): the product-level flow
  the future UI depends on — load (injected loader), set the planning
  period (session comes from the EXISTING default candidate
  discovery), immutable add/remove candidate, lightweight
  confirmation, and ``run_decision`` delegating completely to
  ``run_decision_pipeline``. Every operation is an immutable
  transition; edits un-confirm; ``result_is_stale`` is the simple
  structural invalidation for prior results. No business logic, no
  UI imports.

Not implemented yet: the Streamlit UI, result persistence, and the
decision-problem-to-JEV mapping.
"""
