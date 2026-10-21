"""Rendering views — the only module in the UI that calls Streamlit.

Each ``render_*`` function consumes EXISTING application results
(``DecisionWorkflowState``, ``DecisionPipelineResult``) through the
pure helpers in ``components``/``charts`` and replaces
``st.session_state`` with the NEW immutable workflow states the
workflow functions return. There is no business logic in this file:
formatting, statuses, emphasis, and ordering of sections are
presentation concerns only — all conditions come from the workflow
state and application models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

from project_decision.application.consequence_evaluation import (
    LOW_CONFIDENCE_THRESHOLD,
)
from project_decision.application.decision_review import DecisionReview, ReviewStatus
from project_decision.application.decision_workflow import (
    add_candidate,
    confirm_session,
    load_project,
    remove_candidate,
    run_decision,
    set_planning_period,
)
from project_decision.application.priority_ranking import RankedTask
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.infrastructure.msp.exceptions import MspError

from app.ui import charts, components
from app.ui.styles import STATUS_CSS_CLASS

WORKFLOW_KEY = "decision_workflow"
RESULT_KEY = "decision_pipeline_result"
RESULT_PARAMS_KEY = "decision_result_parameters"
SELECTED_TASK_KEY = "selected_task_id"


def reset_session_for_new_cycle() -> None:
    """Clear workflow state so a new project source can be chosen.

    Pure session-state management (same category as the staleness
    clearing, which also drops results): no business rule decides here
    — the workflow simply returns to its first step.
    """
    for key in (WORKFLOW_KEY, RESULT_KEY, RESULT_PARAMS_KEY, SELECTED_TASK_KEY):
        st.session_state.pop(key, None)

_TITLE = "Project Decision Assistant"
_SUBTITLE = "Periodic activity prioritization for project execution decisions."
_READ_ONLY = (
    '<div class="readonly-note"><strong>MSP integration: READ-ONLY.</strong> '
    "The application reads the active Microsoft Project schedule and "
    "nothing is ever written, saved, or closed.</div>"
)


@dataclass(frozen=True)
class UiContext:
    """What the sidebar decided for this render (UI configuration only)."""

    mode: str
    evaluator: object
    scoring_parameters: ScoringParameters | None
    parameters_valid: bool
    parameter_note: str | None


def ensure_session_state() -> None:
    for key in (WORKFLOW_KEY, RESULT_KEY, RESULT_PARAMS_KEY, SELECTED_TASK_KEY):
        st.session_state.setdefault(key, None)


def store_workflow(next_state) -> None:
    """Replace (never mutate) the stored immutable workflow state."""
    st.session_state[WORKFLOW_KEY] = next_state


def empty_state(title: str, body: str) -> None:
    st.markdown(
        f'<div class="empty-state"><div class="empty-title">{title}</div>{body}</div>',
        unsafe_allow_html=True,
    )


# -- header ----------------------------------------------------------------------


def render_header() -> None:
    st.markdown(f'<div class="product-title">{_TITLE}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="product-subtitle">{_SUBTITLE}</div>', unsafe_allow_html=True
    )
    st.markdown(_READ_ONLY, unsafe_allow_html=True)
    workflow = st.session_state.get(WORKFLOW_KEY)
    has_result = st.session_state.get(RESULT_KEY) is not None
    steps = components.workflow_steps(workflow, has_result)
    st.markdown(
        '<div class="stepper">'
        "".join(
            f'<span class="step {state}">{label}</span>' for label, state in steps
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def stale_result_check(current_parameters: ScoringParameters | None) -> None:
    """Harden the staleness UX around the honest invalidation causes.

    - invalid parameter-editor input: the previous result is cleared and
      the user is told why (no result is ever shown next to parameters it
      was NOT computed with);
    - a changed planning session (the existing ``result_is_stale``
      mechanism - the single source of truth for that);
    - changed scoring parameters (UI configuration only; the pipeline
      result does not store parameters, so the UI remembers them).
    In all cases the old result is cleared and the user is told to rerun.
    A result from a different session/parameter set is never displayed.
    """
    stored_result = st.session_state.get(RESULT_KEY)
    result, invalid_message = components.clear_if_parameters_invalid(
        stored_result, current_parameters
    )
    if invalid_message is not None:
        st.session_state[RESULT_KEY] = None
        st.session_state[RESULT_PARAMS_KEY] = None
        st.warning(invalid_message)
        return
    if current_parameters is None:
        return

    result, message = components.clear_if_stale(
        result, st.session_state.get(WORKFLOW_KEY)
    )
    if result is not None:
        changed, parameter_message = components.clear_if_parameters_changed(
            st.session_state.get(RESULT_PARAMS_KEY), current_parameters
        )
        if changed:
            result = None
            message = parameter_message
    # Honesty: the "cleared" banner only when a result actually existed
    # and was dropped - on fresh state (nothing run yet) the workflow is
    # trivially 'stale' but there is no previous result to report.
    st.session_state[RESULT_KEY] = result
    if message and stored_result is not None:
        st.info(message)


# -- 1. Project -------------------------------------------------------------------


def render_project_section(context: UiContext) -> None:
    del context  # fixed context; kept signature uniform for the flow
    workflow = st.session_state.get(WORKFLOW_KEY)
    if workflow is None:
        st.subheader("Project")
        demo_column, live_column = st.columns(2)
        with demo_column:
            if st.button("Load Demo Project", type="primary"):
                store_workflow(load_project(components.build_demo_loader()))
                st.rerun()
        with live_column:
            if st.button("Load Active MSP Project"):
                loader = components.build_project_loader()
                try:
                    store_workflow(load_project(loader))
                except MspError as exc:
                    # Expected load failure: structured message, previous
                    # valid state preserved, nothing fabricated.
                    st.error(components.msp_error(exc))
                    return
                st.rerun()
        st.caption(
            "**Demo Project** — a deterministic offline fixture; no "
            "Microsoft Project, JEV, network, or API key needed. "
            "**Live MSP Project** — requires Microsoft Project running "
            "with a project open (the integration is strictly read-only)."
        )
        empty_state(
            "No project loaded yet",
            "Load the demo project to explore the full workflow offline, "
            "or load the active Microsoft Project schedule to start a "
            "real planning cycle.",
        )
        return

    state = workflow.project_state
    st.subheader("Project")
    rows = components.project_summary_rows(workflow)
    name_row = rows[0]
    first, second = st.columns([1, 3])
    with first:
        st.metric("Project", name_row["Value"])
        st.metric("Capture", rows[-1]["Value"])
    with second:
        st.table(rows[1:4])
    for warning in state.capture_warnings:
        st.warning(f"**Capture limitation (MSP mapping):** {warning}")
    with st.expander("New planning cycle", expanded=False):
        st.caption(
            "Clears the loaded project, the planning session, and any "
            "analysis result, then lets you choose the Demo Project or "
            "a live Microsoft Project again."
        )
        if st.button("Start a new planning cycle"):
            reset_session_for_new_cycle()
            st.rerun()


# -- 2. Planning Period ------------------------------------------------------------


def render_period_section() -> None:
    workflow = st.session_state.get(WORKFLOW_KEY)
    if workflow is None:
        return
    suggested_start, suggested_finish = components.default_period_bounds(
        workflow.project_state
    )
    period = workflow.planning_period

    if period is None:
        st.subheader("Planning Period")
        st.caption("Define the inclusive date window this plan is made for.")
    else:
        with st.expander(
            f"Planning Period — {period.start_date} to {period.end_date} "
            f"({period.working_days} working days, inclusive)",
            expanded=False,
        ):
            _render_period_editor(workflow, suggested_start, suggested_finish)
        return
    _render_period_editor(workflow, suggested_start, suggested_finish)


def _render_period_editor(workflow, suggested_start, suggested_finish) -> None:
    value = (
        (suggested_start, suggested_finish)
        if suggested_start is not None and suggested_finish is not None
        else (date.today(), date.today() + timedelta(days=6))
    )
    selected = st.date_input("Period (both dates inclusive)", value=value,
                             format="YYYY-MM-DD")
    if not isinstance(selected, tuple) or len(selected) != 2:
        st.info("Select a start and an end date to define the planning period.")
        return
    start, finish = selected

    if st.button("Apply planning period", type="primary"):
        try:
            store_workflow(
                set_planning_period(
                    workflow, PlanningPeriod(start_date=start, end_date=finish)
                )
            )
            st.rerun()
        except ValueError as exc:
            st.error(
                "**WHAT:** the planning period was rejected.\n\n"
                f"**WHY IT MATTERS:** {exc}\n\n"
                "**NEXT:** choose a start date that is on or before the "
                "end date."
            )

    session = workflow.planning_session
    if session is not None:
        period = workflow.planning_period
        period_card, days_card, candidates_card = st.columns(3)
        period_card.metric("Period",
                           f"{period.start_date} to {period.end_date}")
        days_card.metric("Working days (Mon–Fri, inclusive)",
                         components.optional_text(period.working_days))
        candidates_card.metric("Default candidates",
                               len(session.candidate_task_ids))


# -- 3. Candidate Activities ---------------------------------------------------------


def render_candidates_section() -> None:
    workflow = st.session_state.get(WORKFLOW_KEY)
    if workflow is None or workflow.planning_session is None:
        return
    session = workflow.planning_session
    confirmed = workflow.confirmed
    header = (
        "Candidate Activities"
        if not confirmed
        else f"Candidate Activities — {len(session.candidate_task_ids)} selected"
    )
    if confirmed:
        with st.expander(
            f"{header} (confirmed — changes require confirmation again)",
            expanded=False,
        ):
            _render_candidate_editor(workflow)
        return
    st.subheader(header)
    _render_candidate_editor(workflow)


def _render_candidate_editor(workflow) -> None:
    session = workflow.planning_session
    rows = components.candidate_rows(workflow.project_state, session)
    if not rows:
        empty_state(
            "No candidates for this period",
            "No automatically discovered activities overlap this planning "
            "period. Add existing project tasks manually below if some are "
            "relevant — the analysis stays honest about their origin.",
        )
    else:
        st.dataframe(rows, hide_index=True, width="stretch")
        manual = [row for row in rows if row["Source"] == "manually added"]
        if manual:
            st.caption(f"{len(manual)} manually added activity(ies); the "
                       "rest were discovered automatically. The project "
                       "state itself is never modified.")

    removal = st.selectbox(
        "Remove a candidate (type to search)",
        options=["(none)", *session.candidate_task_ids],
    )
    if removal != "(none)" and st.button("Remove selected candidate"):
        store_workflow(remove_candidate(workflow, removal))
        st.rerun()

    candidate_ids = set(session.candidate_task_ids)
    available = [
        task for task in workflow.project_state.tasks if task.id not in candidate_ids
    ]
    if available:
        options = {f"{task.id} — {task.name}": task.id for task in available}
        addition = st.selectbox("Add an existing task (type to search)",
                                options=list(options))
        if st.button("Add selected task"):
            store_workflow(add_candidate(workflow, options[addition]))
            st.rerun()
    else:
        st.caption("Every project task is already in the candidate set.")


# -- 4. Decision Setup (confirmation + run) -----------------------------------------


def render_setup_section(context: UiContext) -> None:
    workflow = st.session_state.get(WORKFLOW_KEY)
    if workflow is None or workflow.planning_session is None:
        return
    session = workflow.planning_session
    st.subheader("Decision Setup")
    if not workflow.confirmed:
        _render_confirmation_summary(workflow)
        if session.candidate_task_ids:
            st.caption(
                "Editing the period or candidates un-confirms the session "
                "automatically (existing workflow behavior)."
            )
        return

    st.markdown(
        '<span class="status-badge badge ready">SESSION CONFIRMED</span> '
        "Changing the period or candidates requires confirmation again.",
        unsafe_allow_html=True,
    )
    with st.expander("Details of the confirmed session", expanded=False):
        _render_confirmation_summary(workflow, confirmed_view=True)
    _render_run(context, workflow)


def _render_confirmation_summary(workflow, confirmed_view: bool = False) -> None:
    period = workflow.planning_period
    session = workflow.planning_session
    candidates = session.candidate_task_ids
    tasks_by_id = {task.id: task for task in workflow.project_state.tasks}
    # Display-only pre-checks from existing task fields (no review rules).
    without_slack = [c for c in candidates if tasks_by_id[c].total_slack_days is None]
    without_notes = [c for c in candidates if not tasks_by_id[c].notes]

    first, second, third, fourth = st.columns(4)
    first.metric("Planning period", f"{period.start_date} to {period.end_date}")
    second.metric("Candidates", len(candidates))
    third.metric("Without slack data", len(without_slack))
    fourth.metric("Without notes (JEV)", len(without_notes))
    if candidates and without_slack:
        st.caption(
            "Without slack data: " + ", ".join(without_slack)
            + " — schedule pressure will be unavailable for them (review flags)."
        )
    if candidates and without_notes:
        st.caption(
            "Without notes: " + ", ".join(without_notes)
            + " — the consequence of delay stays provisional for them."
        )
    if not candidates:
        empty_state(
            "Empty candidate set",
            "This confirmed summary has zero candidates: the analysis is "
            "possible but there is nothing to rank. Add candidates to make "
            "the decision meaningful.",
        )
    if not confirmed_view:
        label = ("Confirm Planning Session"
                 if candidates else "Confirm Planning Session (no candidates)")
        if st.button(label, type="primary"):
            store_workflow(confirm_session(workflow))
            st.rerun()


def _render_run(context: UiContext, workflow) -> None:
    runnable, reason = components.can_run_decision(workflow)
    if context.scoring_parameters is None or not context.parameters_valid:
        runnable = False
        reason = (
            context.parameter_note
            or "Fix the scoring parameters in the sidebar first."
        )
    if st.button("Run Decision Analysis", type="primary", disabled=not runnable):
        st.session_state[RESULT_KEY] = run_decision(
            workflow, context.evaluator, scoring_parameters=context.scoring_parameters
        )
        st.session_state[RESULT_PARAMS_KEY] = context.scoring_parameters
        st.rerun()
    if not runnable:
        st.caption(f"Not runnable yet: {reason}")


# -- 5. Decision Dashboard ------------------------------------------------------------


def render_dashboard(context: UiContext) -> None:
    del context  # the dashboard consumes the stored result only
    result = st.session_state.get(RESULT_KEY)
    if result is None:
        return
    review: DecisionReview = result.decision_review

    st.subheader("Decision Dashboard")
    render_status_section(review)
    render_kpi_row(review, result.decision_result.ranked_tasks)
    render_priority_table(result)
    render_ranking_chart(result)
    render_task_investigation(result)
    render_review_center(result)


def render_status_section(review: DecisionReview) -> None:
    label, _ = components.status_badge(review.status)
    css_class = STATUS_CSS_CLASS[review.status.value]
    st.markdown(
        f'<span class="status-badge badge {css_class}">{label}</span> '
        f'<span class="product-subtitle">{components.status_explanation(review.status)}</span>',
        unsafe_allow_html=True,
    )
    if review.status is ReviewStatus.INVALID:
        st.error(
            "**WHAT:** the decision result failed structural validation.\n\n"
            "**WHY IT MATTERS:** an invalid result must not be used for a "
            "real decision.\n\n"
            "**NEXT:** review the issues below with the team; rerun after "
            "the underlying session/state problem is fixed."
        )
        for issue in review.validation_issues:
            st.markdown(f"- {issue}")
    elif review.review_reason_counts:
        st.markdown(
            " ".join(
                f'<span class="review-chip">{chip}</span>'
                for chip in components.review_reason_chips(review)
            ),
            unsafe_allow_html=True,
        )


def render_kpi_row(review: DecisionReview, ranked_tasks: tuple[RankedTask, ...]) -> None:
    cards = [
        ("Candidates", components.optional_text(review.candidate_count), ""),
        ("Top priority", components.score_text(components.top_score(ranked_tasks)), ""),
        ("Needs review", len(review.tasks_needing_review),
         ", ".join(review.tasks_needing_review) or "none"),
        ("Resource warnings", review.resource_warning_count,
         ", ".join(review.tasks_with_resource_warnings) or "none"),
    ]
    accent = STATUS_CSS_CLASS[review.status.value]
    columns = st.columns(4)
    for column, (label, value, sub) in zip(columns, cards):
        with column:
            sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
            st.markdown(
                f'<div class="kpi-card {accent}"><div class="kpi-label">{label}</div>'
                f'<div class="kpi-value {accent}">{components.optional_text(value)}</div>'
                f'{sub_html}</div>',
                unsafe_allow_html=True,
            )


def render_priority_table(result) -> None:
    st.markdown("**Priority Ranking** (from the existing DecisionResult; "
                "order and values are unchanged)")
    rows = components.ranking_rows(result)
    if not rows:
        empty_state(
            "Nothing to rank",
            "The confirmed session has zero candidates, so the decision "
            "run produced no ranking. This is an honest empty state.",
        )
        return
    st.dataframe(rows, hide_index=True, width="stretch")
    render_ranking_state_legend()


def render_ranking_chart(result) -> None:
    ranked = result.decision_result.ranked_tasks
    figure, note = charts.ranking_figure(ranked)
    if figure is None:
        empty_state(
            "No scored activities",
            "No activity carries a complete score yet (for example when "
            "schedule data is missing); the table above shows the honest "
            "state instead of a chart.",
        )
        return
    st.plotly_chart(figure)
    display_note = note or charts.ranking_note(ranked)
    if display_note:
        st.caption(display_note)


def render_ranking_state_legend() -> None:
    st.caption(
        "Row states (display only): OK — fully explained; REVIEW — manager "
        "review recommended; RESOURCE WARNING — locked conflict evidence, "
        "never changes scores; UNSCORED — no complete score available."
    )


def _level_rows(evaluation: dict) -> list[dict]:
    """Per-level probability rows from the stored JEV evaluation."""
    legend = evaluation["legend"]
    probabilities = evaluation["probabilities"]
    levels = sorted(set(list(legend.keys()) + list(probabilities.keys())))
    return [
        {
            "Level": level,
            "Meaning": components.optional_text(legend.get(level)),
            "Probability": components.criterion_text(probabilities.get(level)),
        }
        for level in levels
    ]


def render_task_investigation(result) -> None:
    ranked_tasks = result.decision_result.ranked_tasks
    if not ranked_tasks:
        empty_state(
            "No activity to investigate",
            "There are no ranked activities; run the analysis with at "
            "least one candidate to enable investigation.",
        )
        return
    st.markdown("**Selected Activity Details**")
    options = [f"#{one.rank} {one.task_name} ({one.task_id})" for one in ranked_tasks]
    label_by_option = dict(zip(options, ranked_tasks))
    current = st.session_state.get(SELECTED_TASK_KEY)
    index = 0
    if current is not None:
        for position, one in enumerate(ranked_tasks):
            if one.task_id == current:
                index = position
    selection = st.selectbox("Inspect a ranked activity (type to search)",
                             options=options, index=index)
    ranked = label_by_option[selection]

    first, second, third, fourth, fifth = st.columns(5)
    first.metric("Rank", ranked.rank)
    second.metric("Priority score", components.score_text(ranked.total_score))
    third.metric("Activity ID", ranked.task_id)
    fourth.metric("Consequence source",
                  components.optional_text(ranked.consequence_source))
    fifth.metric("Status", components.row_state(ranked))
    st.caption(ranked.task_name)

    st.markdown("**Criterion contributions** (score = 100 × Σ weight × value)")
    parameters = st.session_state.get(RESULT_PARAMS_KEY) or ScoringParameters()
    st.table(components.contribution_rows(ranked, parameters))
    figure = charts.contribution_figure(ranked)
    if figure is not None:
        st.plotly_chart(figure)
    else:
        st.caption("No contribution chart: the activity is unscored.")

    _render_investigation_facts(result, ranked)
    _render_investigation_consequence(result, ranked)
    _render_investigation_review(ranked)
    _render_investigation_resources(result, ranked)


def _render_investigation_facts(result, ranked: RankedTask) -> None:
    details = components.successor_details(
        result, st.session_state[WORKFLOW_KEY].project_state, ranked.task_id
    )
    successor_names = (
        ", ".join(
            f"{successor_id} ({name})"
            for successor_id, name in zip(
                details["successor_ids"], details["successor_names"]
            )
        )
        if details and details["successor_ids"]
        else _dash_or_not(details)
    )
    st.markdown("**Schedule information**")
    st.table(
        [
            {"Fact": "Total slack", "Value": components.slack_text(ranked.total_slack_days)},
            {"Fact": "Critical status", "Value": components.optional_text(ranked.is_critical)},
            {"Fact": "Direct successors", "Value": components.optional_text(ranked.successor_count)},
            {"Fact": "Successor IDs and names", "Value": successor_names},
        ]
    )


def _dash_or_not(details) -> str:
    return "—" if details is None else "— (none)"


def _render_investigation_consequence(result, ranked: RankedTask) -> None:
    evaluation = components.evaluation_details(result, ranked.task_id)
    st.markdown("**Consequence of Delay information**")
    if evaluation is None:
        st.caption("No stored evaluation: the consequence is provisional "
                   "(offline mode). Nothing is invented in its place.")
        score_range = ranked.score_range
        if score_range is not None:
            st.info(_provisional_range_text(score_range))
        return
    st.table(
        [
            {"Fact": "Source", "Value": components.optional_text(ranked.consequence_source)},
            {"Fact": "JEV score", "Value": components.optional_text(evaluation["score"])},
            {"Fact": "Confidence", "Value": components.criterion_text(evaluation["confidence"])},
            {"Fact": "Error kind (if any)", "Value": components.optional_text(evaluation["error_kind"])},
        ]
    )
    confidence = evaluation["confidence"]
    if (
        evaluation["status"] == "evaluated"
        and confidence is not None
        and confidence < LOW_CONFIDENCE_THRESHOLD
    ):
        st.warning(
            "**WHAT:** the JEV confidence for this consequence is low "
            f"({components.criterion_text(confidence)} < 50%)\n\n"
            "**WHY IT MATTERS:** the value still enters the score, but a "
            "manager should not treat it as established.\n\n"
            "**NEXT:** review the reason text below; rerun JEV later for "
            "a firmer value."
        )
    elif evaluation["status"] in ("failed", "unavailable"):
        st.warning(
            "**WHAT:** the JEV evaluation for this task did not succeed "
            f"({components.optional_text(evaluation['status'])}).\n\n"
            "**WHY IT MATTERS:** the structured failure is kept in the "
            "result; the consequence of delay remains the provisional "
            "neutral value.\n\n"
            "**NEXT:** check the review reasons below; rerun when JEV "
            "is reachable again."
        )
    if evaluation["status"] == "evaluated" and evaluation["probabilities"]:
        level_rows = _level_rows(evaluation)
        if level_rows:
            st.table(level_rows)
    score_range = ranked.score_range
    if score_range is not None:
        st.info(_provisional_range_text(score_range))


def _provisional_range_text(score_range) -> str:
    return (
        "**Provisional consequence:** the score could still land between "
        f"{components.score_text(score_range.minimum)} and "
        f"{components.score_text(score_range.maximum)} once the "
        "consequence of delay is really evaluated (CD = 0 vs CD = 1)."
    )


def _render_investigation_review(ranked: RankedTask) -> None:
    st.markdown("**Review information**")
    reasons = components.detail_review_reasons(ranked)
    if reasons:
        for reason in reasons:
            st.markdown(f"- {reason}")
    else:
        st.caption("No review issues for this activity.")


def _render_investigation_resources(result, ranked: RankedTask) -> None:
    st.markdown("**Resource information**")
    if not ranked.resource_warning:
        st.caption("No resource conflicts scheduled for this activity "
                   "(UNKNOWN overlaps are not claimed as conflicts).")
        return
    rows = components.conflict_details(result, ranked.task_id)
    if rows:
        st.table(rows)
    else:
        st.caption("—")


# -- Review Center ---------------------------------------------------------------------


def render_review_center(result) -> None:
    rows = components.review_center_rows(result)
    st.markdown("**Review Center**")
    if not rows:
        empty_state(
            "No review issues",
            "The review found nothing to escalate: complete explanations "
            "for every ranked activity.",
        )
        return
    st.dataframe(rows, hide_index=True, width="stretch")
    candidates = _review_task_ids(result)
    if candidates:
        selection = st.selectbox("Investigate an issue in the detail view",
                                 options=["(choose)", *candidates])
        if selection != "(choose)":
            st.session_state[SELECTED_TASK_KEY] = selection
            st.rerun()


def _review_task_ids(result) -> list[str]:
    review: DecisionReview = result.decision_review
    return [
        *review.tasks_needing_review,
        *review.tasks_with_missing_schedule,
        *review.tasks_with_resource_warnings,
        *review.unscored_task_ids,
    ]


# -- sidebar ----------------------------------------------------------------------


def render_sidebar() -> UiContext:
    with st.sidebar:
        st.subheader("Decision Mode")
        mode = st.radio(
            "Consequence of Delay evaluation",
            ("OFFLINE (provisional)", "LIVE JEV"),
            captions=(
                "No API calls; consequences stay neutral (0.5) and are "
                "flagged for review.",
                "Uses the configured TypeSafe/JEV integration (real API "
                "calls, one per candidate with notes).",
            ),
        )
        evaluator = components.build_offline_evaluator()
        mode_note: str | None = None
        if mode == "LIVE JEV":
            live_evaluator, note = components.build_jev_evaluator()
            if live_evaluator is None:
                st.warning(note)
                mode_note = note
            else:
                evaluator = live_evaluator
                st.info("LIVE MODE active — structured failures never "
                        "crash the run.")
        else:
            st.info("OFFLINE MODE — the score stays deterministic and the "
                    "consequence of delay is provisional.")

        st.subheader("Scoring Parameters")
        st.caption("Weights must sum to 1.0 — rejected, never normalized. "
                   "Changing them requires a rerun.")
        weights = st.columns(3)
        schedule_weight = weights[0].number_input(
            "Schedule pressure", min_value=0.0, max_value=1.0, step=0.05,
            value=0.40, format="%.2f", key="sp_weight",
        )
        successor_weight = weights[1].number_input(
            "Successor impact", min_value=0.0, max_value=1.0, step=0.05,
            value=0.20, format="%.2f", key="si_weight",
        )
        consequence_weight = weights[2].number_input(
            "Consequence", min_value=0.0, max_value=1.0, step=0.05,
            value=0.40, format="%.2f", key="cd_weight",
        )
        successor_cap = st.number_input(
            "Successor cap K", min_value=1, step=1, value=5
        )
        override_horizon = st.checkbox("Override horizon H (working days)")
        horizon = (
            st.number_input("Horizon H", min_value=0.5, step=0.5, value=5.0)
            if override_horizon else None
        )
        parameters, parameter_note = components.build_scoring_parameters(
            schedule_weight, successor_weight, consequence_weight,
            successor_cap, horizon,
        )
        if parameters is None:
            st.error(
                "**WHAT:** the scoring parameters were rejected.\n\n"
                f"**WHY IT MATTERS:** {parameter_note}\n\n"
                "**NEXT:** adjust the weights so they sum to 1.0 (no "
                "silent normalization exists)."
            )

        st.subheader("Session")
        render_sidebar_summary()

        st.markdown(_READ_ONLY, unsafe_allow_html=True)
    return UiContext(
        mode=mode,
        evaluator=evaluator,
        scoring_parameters=parameters,
        parameters_valid=parameters is not None,
        parameter_note=parameter_note,
    )


def render_sidebar_summary() -> None:
    workflow = st.session_state.get(WORKFLOW_KEY)
    if workflow is None:
        st.caption("No project loaded.")
        return
    state = workflow.project_state
    st.caption(f"Project: {components.optional_text(state.project_name)} · "
               f"{len(state.tasks)} tasks · {len(state.resources)} resources")
    if workflow.planning_session is None:
        st.caption("No planning period selected.")
        return
    period = workflow.planning_period
    session = workflow.planning_session
    st.caption(f"Period: {period.start_date} to {period.end_date} · "
               f"{period.working_days} working days")
    st.caption(f"Candidates: {len(session.candidate_task_ids)} · "
               + ("confirmed" if workflow.confirmed else "NOT confirmed"))
    if st.session_state.get(RESULT_KEY) is not None:
        st.caption("Analysis: result available for this session.")
    else:
        st.caption("Analysis: nothing run yet.")
