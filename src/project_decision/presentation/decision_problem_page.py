"""Decision Problems page: authoring decision problems in Streamlit.

Organization of this module:
- `render_decision_problems_page()` is the only entry point called by the
  dashboard; everything below it is UI plumbing for the authoring flow:
  title/description/objective, dynamic lists of alternatives, criteria,
  constraints, and assumptions, a readiness review, and save/load of
  problems via the application JSON store.
- No business rules live here: validation and structure belong to the
  domain (DecisionProblem, Decision, EvaluationCriterion, Constraint,
  DecisionAssumption), persistence to
  application.decision_problem_store. This page only passes values in
  and results out.
- No alternative is ever ranked or scored here, and no schedule/cost
  effect is ever calculated: evaluation concerns belong to the future
  JEV integration.
- Read-only guarantee for Microsoft Project: this page may attach the
  already-loaded snapshot as context, but nothing in it touches the
  Microsoft Project file.

Draft mechanics: the draft is a plain dict in ``st.session_state``.
Every widget key embeds a stable per-item ``uid`` so adding/removing/
reordering items keeps user input attached to the right item; deletions
purge the item's widget keys and ``st.rerun()``.
"""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from project_decision.application.decision_problem_store import (
    DecisionProblemFileStore,
    problem_to_dict,
)
from project_decision.domain import (
    Constraint,
    ConstraintKind,
    Decision,
    DecisionAssumption,
    DecisionKind,
    DecisionProblem,
    EvaluationCriterion,
    ProjectState,
)

DRAFT_KEY = "dp_draft"
_SELECTED_KEY = "dp_selected"
_SOURCE_KEY = "dp_source"
_NEW_DRAFT = "(new empty draft)"

_KIND_LABELS = {
    DecisionKind.ADD_RESOURCE_CAPACITY: "Add resource capacity",
    DecisionKind.ADJUST_TASK_DURATION: "Adjust task duration",
    DecisionKind.RESEQUENCE_TASKS: "Resequence tasks (change task order)",
    DecisionKind.LOWER_TASK_SCOPE: "Lower task scope",
    DecisionKind.OTHER: "Other",
}
_LABEL_TO_KIND = {label: kind for kind, label in _KIND_LABELS.items()}

_CONSTRAINT_LABELS = {
    ConstraintKind.DEADLINE: "Deadline",
    ConstraintKind.RESOURCE_CAPACITY: "Resource capacity",
    ConstraintKind.TASK_DEPENDENCY: "Task dependency",
    ConstraintKind.MILESTONE: "Milestone",
    ConstraintKind.OTHER: "Other",
}
_LABEL_TO_CONSTRAINT = {label: kind for kind, label in _CONSTRAINT_LABELS.items()}


def render_decision_problems_page() -> None:
    """The whole Decision Problems tab."""
    store = DecisionProblemFileStore()
    draft = _draft()
    _render_saved_problems(store)

    st.divider()
    _render_problem_details(draft)
    _render_alternatives(draft)
    _render_criteria(draft)
    _render_constraints(draft)
    _render_assumptions(draft)
    _render_review(store, draft)


# -- draft state ------------------------------------------------------------------


def _draft() -> dict:
    """The authoring draft: plain dicts, synced with widgets each rerun."""
    if DRAFT_KEY not in st.session_state:
        st.session_state[DRAFT_KEY] = _new_draft()
    return st.session_state[DRAFT_KEY]


def _new_draft() -> dict:
    return {
        "id": uuid4().hex,
        "title": "",
        "description": "",
        "objective": "",
        "attach_project": False,
        "alternatives": [],
        "criteria": [],
        "constraints": [],
        "assumptions": [],
    }


def _draft_from_problem(problem: DecisionProblem) -> dict:
    draft = _new_draft()
    draft["id"] = problem.id
    draft["title"] = problem.title
    draft["description"] = problem.description
    draft["objective"] = problem.objective
    draft["attach_project"] = problem.current_state is not None
    draft["alternatives"] = [
        {
            "uid": uuid4().hex,
            "id": alternative.id,
            "name": alternative.name,
            "kind": alternative.kind.value,
            "description": alternative.description,
        }
        for alternative in problem.alternatives
    ]
    draft["criteria"] = [
        {"uid": uuid4().hex, "id": c.id, "name": c.name, "description": c.description}
        for c in problem.criteria
    ]
    draft["constraints"] = [
        {"uid": uuid4().hex, "id": c.id, "kind": c.kind.value, "description": c.description}
        for c in problem.constraints
    ]
    draft["assumptions"] = [
        {"uid": uuid4().hex, "id": a.id, "description": a.description}
        for a in problem.assumptions
    ]
    return draft


def _purge_widget_keys(*prefixes: str) -> None:
    for prefix in prefixes:
        for key in [key for key in st.session_state if str(key).startswith(prefix)]:
            del st.session_state[key]


# -- saved problems -----------------------------------------------------------------


def _render_saved_problems(store: DecisionProblemFileStore) -> None:
    st.subheader("Saved decision problems")
    summaries = store.list_problems()
    corrupt = [summary for summary in summaries if not summary.loadable]
    if corrupt:
        st.warning(
            "These saved problem files could not be read: "
            + ", ".join(summary.id for summary in corrupt)
        )
    loadable = [summary for summary in summaries if summary.loadable]

    options = [_NEW_DRAFT] + [summary.id for summary in loadable]

    def _format(option: str) -> str:
        if option == _NEW_DRAFT:
            return option
        summary = next(s for s in loadable if s.id == option)
        state = "ready for evaluation" if summary.ready_for_evaluation else "in draft"
        return f"{summary.title} [{state}]"

    selection = st.selectbox(
        "Load an existing problem or start a new draft",
        options,
        format_func=_format,
        key=_SELECTED_KEY,
    )

    previous = st.session_state.get(_SOURCE_KEY, _NEW_DRAFT)
    if selection != previous:
        st.session_state[_SOURCE_KEY] = selection
        if selection == _NEW_DRAFT:
            st.session_state[DRAFT_KEY] = _new_draft()
        else:
            try:
                problem = store.load(selection)
            except ValueError as exc:
                st.error(f"Could not load the selected problem: {exc}")
                st.session_state[DRAFT_KEY] = _new_draft()
            else:
                st.session_state[DRAFT_KEY] = _draft_from_problem(problem)
        st.rerun()

    if selection != _NEW_DRAFT:
        confirmed = st.checkbox(
            "I understand this permanently deletes the saved file",
            key=f"dp:confirm_delete:{selection}",
        )
        if st.button(
            "Delete selected",
            key="dp:delete",
            disabled=not confirmed,
            on_click=_delete_problem_clicked,
            args=(store,),
            kwargs={"problem_id": selection},
        ):
            st.rerun()
    st.caption("Loading a problem replaces the current draft.")


def _delete_problem_clicked(store: DecisionProblemFileStore, problem_id: str) -> None:
    """ callback: runs before the next script run, so widget state may be set."""
    store.delete(problem_id)
    st.session_state[_SELECTED_KEY] = _NEW_DRAFT
    st.session_state[_SOURCE_KEY] = _NEW_DRAFT
    st.session_state[DRAFT_KEY] = _new_draft()


# -- problem details ----------------------------------------------------------------


def _render_problem_details(draft: dict) -> None:
    st.subheader("Problem")
    draft["title"] = st.text_input(
        "Title",
        value=draft["title"],
        key="dp:title",
        placeholder="e.g. Choose an action to address a potential schedule issue",
    )
    draft["description"] = st.text_area(
        "Problem description",
        value=draft["description"],
        key="dp:description",
        placeholder="What is the situation? What decision is needed?",
    )
    draft["objective"] = st.text_input(
        "Objective",
        value=draft["objective"],
        key="dp:objective",
        placeholder="e.g. reduce schedule delay",
    )

    state = _loaded_state()
    attach = st.checkbox(
        "Link the currently loaded project snapshot",
        value=draft["attach_project"] and bool(state),
        key="dp:attach",
    )
    draft["attach_project"] = bool(attach and state is not None)
    if state is not None and draft["attach_project"]:
        st.caption(
            f"Linked snapshot: {state.project_name} "
            f"(captured {state.captured_at:%Y-%m-%d %H:%M}). The snapshot is "
            "copied into the problem; the Microsoft Project file is never "
            "modified."
        )
    elif state is None:
        st.caption("No project snapshot is loaded — load one on the Project overview tab.")


def _loaded_state() -> ProjectState | None:
    loaded = st.session_state.get("loaded_project_state")
    if loaded is not None and loaded.ok:
        return loaded.state
    return None


# -- dynamic list editors ----------------------------------------------------------


def _render_alternatives(draft: dict) -> None:
    st.subheader("Decision alternatives")
    st.caption("At least two alternatives are needed before evaluation.")
    items = draft["alternatives"]
    kind_labels = list(_KIND_LABELS.values())

    for index, item in enumerate(items, start=1):
        uid = item.setdefault("uid", uuid4().hex)
        col_name, col_kind, col_del = st.columns([2, 2, 0.4])
        col_name.markdown(f"**Alternative {index}**")
        item["name"] = col_name.text_input(
            "Name",
            value=item["name"],
            key=f"dp:alt:{uid}:name",
            placeholder="e.g. Add resources to Design",
            label_visibility="collapsed",
        ).strip()
        label = col_kind.selectbox(
            "Kind",
            kind_labels,
            index=kind_labels.index(
                _KIND_LABELS.get(DecisionKind(item["kind"]), DecisionKind.OTHER)
            ),
            key=f"dp:alt:{uid}:kind",
            label_visibility="collapsed",
        )
        item["kind"] = _LABEL_TO_KIND[label].value
        item["description"] = st.text_area(
            "Description",
            value=item["description"],
            key=f"dp:alt:{uid}:description",
            placeholder="What exactly would this alternative do?",
            label_visibility="collapsed",
        )
        if col_del.button("✕", key=f"dp:alt:{uid}:delete", help="Remove this alternative"):
            draft["alternatives"] = [a for a in items if a["uid"] != uid]
            _purge_widget_keys(f"dp:alt:{uid}:")
            st.rerun()

    if st.button("＋ Add alternative", key="dp:add_alternative"):
        draft["alternatives"].append(
            {"uid": uuid4().hex, "id": "", "name": "", "kind": DecisionKind.OTHER.value, "description": ""}
        )
        st.rerun()

    _assign_ids(items)


def _render_criteria(draft: dict) -> None:
    st.subheader("Evaluation criteria")
    st.caption("What each criterion measures — weights and scoring come with evaluation later.")
    items = draft["criteria"]

    for index, item in enumerate(items, start=1):
        uid = item.setdefault("uid", uuid4().hex)
        col_name, col_desc, col_del = st.columns([1, 2, 0.4])
        col_name.markdown(f"**{index}.**")
        item["name"] = col_name.text_input(
            "Criterion",
            value=item["name"],
            key=f"dp:crit:{uid}:name",
            placeholder="e.g. Expected schedule impact",
            label_visibility="collapsed",
        )
        item["description"] = col_desc.text_input(
            "What it measures",
            value=item["description"],
            key=f"dp:crit:{uid}:description",
            placeholder="What does this criterion measure?",
            label_visibility="collapsed",
        )
        if col_del.button("✕", key=f"dp:crit:{uid}:delete", help="Remove this criterion"):
            draft["criteria"] = [c for c in items if c["uid"] != uid]
            _purge_widget_keys(f"dp:crit:{uid}:")
            st.rerun()

    if st.button("＋ Add criterion", key="dp:add_criterion"):
        draft["criteria"].append({"uid": uuid4().hex, "id": "", "name": "", "description": ""})
        st.rerun()

    _assign_ids(items)


def _render_constraints(draft: dict) -> None:
    st.subheader("Known constraints")
    items = draft["constraints"]
    constraint_labels = list(_CONSTRAINT_LABELS.values())

    for item in items:
        uid = item.setdefault("uid", uuid4().hex)
        col_kind, col_desc, col_del = st.columns([1, 2.4, 0.4])
        label = col_kind.selectbox(
            "Kind",
            constraint_labels,
            index=constraint_labels.index(
                _CONSTRAINT_LABELS.get(ConstraintKind(item["kind"]), ConstraintKind.OTHER)
            ),
            key=f"dp:con:{uid}:kind",
        )
        item["kind"] = _LABEL_TO_CONSTRAINT[label].value
        item["description"] = col_desc.text_input(
            "Constraint",
            value=item["description"],
            key=f"dp:con:{uid}:description",
            placeholder="e.g. Must not exceed the approved budget",
        )
        if col_del.button("✕", key=f"dp:con:{uid}:delete", help="Remove this constraint"):
            draft["constraints"] = [c for c in items if c["uid"] != uid]
            _purge_widget_keys(f"dp:con:{uid}:")
            st.rerun()

    if st.button("＋ Add constraint", key="dp:add_constraint"):
        draft["constraints"].append(
            {"uid": uuid4().hex, "id": "", "kind": ConstraintKind.OTHER.value, "description": ""}
        )
        st.rerun()

    _assign_ids(items)


def _render_assumptions(draft: dict) -> None:
    st.subheader("Assumptions")
    items = draft["assumptions"]

    for item in items:
        uid = item.setdefault("uid", uuid4().hex)
        col_desc, col_del = st.columns([10, 0.4])
        item["description"] = col_desc.text_input(
            "Assumption",
            value=item["description"],
            key=f"dp:asm:{uid}:description",
            placeholder="e.g. Overtime is possible for at most two weeks",
        )
        if col_del.button("✕", key=f"dp:asm:{uid}:delete", help="Remove this assumption"):
            draft["assumptions"] = [a for a in items if a["uid"] != uid]
            _purge_widget_keys(f"dp:asm:{uid}:")
            st.rerun()

    if st.button("＋ Add assumption", key="dp:add_assumption"):
        draft["assumptions"].append({"uid": uuid4().hex, "id": "", "description": ""})
        st.rerun()

    _assign_ids(items)


def _assign_ids(items: list[dict]) -> None:
    """Give every item a stable id (kept across save/load)."""
    for item in items:
        if not item.get("id"):
            item["id"] = item["uid"]


# -- review and save -----------------------------------------------------------------


def _render_review(store: DecisionProblemFileStore, draft: dict) -> None:
    st.divider()
    st.subheader("Review")
    problem, errors = _build_problem(draft)

    if errors:
        st.info("Complete the fields above:")
        for error in errors:
            st.markdown(f"- {error}")
        st.caption("Evaluation structure may still be incomplete — nothing is ranked here.")
        return

    gaps = problem.evaluation_gaps()
    if gaps:
        st.warning("**Not ready for evaluation — missing:**")
        for gap in gaps:
            st.markdown(f"- {gap}")
    else:
        st.success("The problem is structurally complete and ready for evaluation.")
    st.caption("Actual evaluation (via JEV) is a later stage; nothing is ranked here.")

    with st.expander("Structured problem preview (JSON)"):
        st.caption(
            "This is the actual serialized problem — the starting point for "
            "the future JEV API mapping."
        )
        st.json(problem_to_dict(problem), expanded=False)

    if st.button(
        "Save decision problem",
        type="primary",
        key="dp:save",
        help="Saves a local JSON file; the Microsoft Project file is never touched.",
        on_click=_save_problem_clicked,
        args=(store,),
        kwargs={"problem": problem},
    ):
        saved_message = st.session_state.get("dp_save_info")
        if saved_message:
            del st.session_state["dp_save_info"]
            st.success(saved_message)
        st.rerun()


def _save_problem_clicked(store: DecisionProblemFileStore, problem: DecisionProblem) -> None:
    """Callback: runs before the next script run, so widget state may be set."""
    path = store.save(problem)
    st.session_state["dp_save_info"] = f"Saved as {path}"
    st.session_state[_SELECTED_KEY] = problem.id
    st.session_state[_SOURCE_KEY] = problem.id


def _build_problem(draft: dict) -> tuple[DecisionProblem | None, list[str]]:
    """Validate the draft and construct the domain problem.

    Field-level problems are returned as human-readable errors, each
    pointing at the item that caused it; full domain validation happens
    in the domain constructors.
    """
    errors: list[str] = []
    title = draft["title"].strip()
    description = draft["description"].strip()
    objective = draft["objective"].strip()

    if not title:
        errors.append("the problem needs a title")
    if not description:
        errors.append("the problem needs a description")
    if not objective:
        errors.append("the problem needs an objective")
    if errors:
        return None, errors

    alternatives, alternative_errors = _build_alternatives(draft["alternatives"])
    criteria, criterion_errors = _build_criteria(draft["criteria"])
    constraints, constraint_errors = _build_constraints(draft["constraints"])
    assumptions, assumption_errors = _build_assumptions(draft["assumptions"])
    errors = alternative_errors + criterion_errors + constraint_errors + assumption_errors
    if errors:
        return None, errors

    try:
        problem = DecisionProblem(
            current_state=_attached_state(draft),
            objective=objective,
            title=title,
            description=description,
            id=draft["id"],
            alternatives=alternatives,
            criteria=criteria,
            constraints=constraints,
            assumptions=assumptions,
            context="",
        )
    except ValueError as exc:
        return None, [str(exc)]
    return problem, []


def _attached_state(draft: dict) -> ProjectState | None:
    state = _loaded_state()
    return state if draft["attach_project"] and state is not None else None


def _build_alternatives(items: list[dict]) -> tuple[list[Decision], list[str]]:
    alternatives: list[Decision] = []
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        try:
            alternatives.append(
                Decision(
                    id=item["id"],
                    name=item["name"],
                    kind=DecisionKind(item["kind"]),
                    description=item["description"].strip(),
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(f"alternative {index}: {exc}")
    return alternatives, errors


def _build_criteria(items: list[dict]) -> tuple[list[EvaluationCriterion], list[str]]:
    criteria: list[EvaluationCriterion] = []
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        try:
            criteria.append(
                EvaluationCriterion(
                    id=item["id"], name=item["name"], description=item["description"].strip()
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(f"criterion {index}: {exc}")
    return criteria, errors


def _build_constraints(items: list[dict]) -> tuple[list[Constraint], list[str]]:
    constraints: list[Constraint] = []
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        try:
            constraints.append(
                Constraint(
                    id=item["id"],
                    kind=ConstraintKind(item["kind"]),
                    description=item["description"].strip(),
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(f"constraint {index}: {exc}")
    return constraints, errors


def _build_assumptions(items: list[dict]) -> tuple[list[DecisionAssumption], list[str]]:
    assumptions: list[DecisionAssumption] = []
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        try:
            assumptions.append(
                DecisionAssumption(id=item["id"], description=item["description"].strip())
            )
        except (ValueError, KeyError) as exc:
            errors.append(f"assumption {index}: {exc}")
    return assumptions, errors
