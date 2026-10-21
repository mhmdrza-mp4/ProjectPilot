"""Decision pipeline: ONE deterministic use case — orchestration only.

    run_decision_pipeline(state, planning_session, consequence_evaluator,
                          scoring_parameters=None)
            ↓
    DecisionPipelineResult

Execution order (each stage runs EXACTLY ONCE, using the existing
functions — this module contains no decision logic of its own):

1. ``analyze_schedule``            → ScheduleAnalysis
2. ``analyze_resource_conflicts``  → ResourceConflictAnalysis
3. ``consequence_evaluator``       → consequence evaluations (injected!)
4. ``score_session_with_evaluations`` → per-candidate TaskPriorityScore
5. ``create_decision_result``      → DecisionResult (ranking)
6. ``review_decision_result``      → DecisionReview

Deliberate boundaries:
- the consequence evaluator is INJECTED: the pipeline never imports the
  TypeSafe SDK, never constructs a client, never touches HTTP, and
  holds no JEV question/mapping logic. Everything JEV-specific stays in
  ``consequence_evaluation`` / the JEV infrastructure. The evaluator is
  any callable ``(planning_session, project_state) -> evaluations``
  (the existing ``evaluate_session_consequences`` bound with its
  configuration is the intended real-use adapter);
- expected business/app conditions stay inside the existing models
  (structured ``ConsequenceEvaluation`` failures, provisional CDs,
  review flags). This module catches NOTHING: truly unexpected
  programming errors propagate untouched and are never disguised as a
  successful result;
- deterministic: same state + session + evaluator behavior + parameters
  → same result. No timestamps, randomness, hidden state, or
  candidate-relative normalization anywhere;
- read-only: nothing here mutates the state, session, tasks, analyses,
  scores, or any produced result object;
- application-level only: imports are domain/application — never
  Streamlit, FastAPI, databases, MSP COM, or the SDK.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from project_decision.application.consequence_evaluation import (
    ConsequenceEvaluation,
    score_session_with_evaluations,
)
from project_decision.application.decision_review import (
    DecisionReview,
    review_decision_result,
)
from project_decision.application.priority_ranking import (
    DecisionResult,
    create_decision_result,
)
from project_decision.application.priority_scoring import (
    ScoringParameters,
    TaskPriorityScore,
)
from project_decision.application.resource_conflicts import (
    ResourceConflictAnalysis,
    analyze_resource_conflicts,
)
from project_decision.application.schedule_analysis import (
    ScheduleAnalysis,
    analyze_schedule,
)
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState

# The injected evaluator boundary: the shape the pipeline consumes. The real-use adapter binds the existing
# ``evaluate_session_consequences`` with its configuration (send /
# settings / client factory / confidence threshold) upfront.
ConsequenceEvaluator = Callable[
    [PlanningSession, ProjectState],
    Sequence[ConsequenceEvaluation] | Mapping[str, ConsequenceEvaluation] | None,
]


@dataclass(frozen=True)
class DecisionPipelineResult:
    """Everything one prioritization run produced — composition only.

    The fields ARE the immutable outputs of the existing stages (no
    duplication): the session that was decided, the analysis and
    consequence evaluations it consumed, the ranked ``DecisionResult``,
    and its ``DecisionReview``. Evaluations are stored EXACTLY as the
    evaluator returned them (sequence or mapping; None when the
    evaluator supplied none — score/review both treat that as "nothing
    evaluated", never as fabricated values). This is the object future
    UI/API layers can consume.
    """

    planning_session: PlanningSession
    schedule_analysis: ScheduleAnalysis
    conflict_analysis: ResourceConflictAnalysis
    consequence_evaluations: (
        Sequence[ConsequenceEvaluation] | Mapping[str, ConsequenceEvaluation] | None
    )
    decision_result: DecisionResult
    decision_review: DecisionReview


def run_decision_pipeline(
    state: ProjectState,
    planning_session: PlanningSession,
    consequence_evaluator: ConsequenceEvaluator,
    scoring_parameters: ScoringParameters | None = None,
) -> DecisionPipelineResult:
    """Run the full periodic activity-prioritization use case, once.

    Deterministic and side-effect free; expected evaluation failures are
    structured data (per the existing consequence-evaluation behavior),
    while genuinely unexpected errors propagate (never swallowed).
    """
    schedule_analysis = analyze_schedule(state)
    conflict_analysis = analyze_resource_conflicts(state)
    consequence_evaluations = consequence_evaluator(planning_session, state)

    scores = score_session_with_evaluations(
        planning_session,
        schedule_analysis,
        consequence_evaluations if consequence_evaluations is not None else {},
        parameters=scoring_parameters,
    )
    decision_result = create_decision_result(
        planning_session,
        state,
        schedule_analysis,
        scores,
        conflict_analysis,
    )
    decision_review = review_decision_result(
        planning_session,
        state,
        decision_result,
        schedule_analysis=schedule_analysis,
        consequence_evaluations=consequence_evaluations,
    )
    return DecisionPipelineResult(
        planning_session=planning_session,
        schedule_analysis=schedule_analysis,
        conflict_analysis=conflict_analysis,
        consequence_evaluations=consequence_evaluations,
        decision_result=decision_result,
        decision_review=decision_review,
    )
