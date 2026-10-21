"""Jev integration test panel — a development/verification section.

Purpose: verify that this application can communicate with the official
TypeSafe Jev API. It shows whether ``TYPESAFE_API_KEY`` is configured
(never the value), sends a predefined example state with one official
question of each type (Choice, Score, Noul), and displays the
structured answers with probabilities, legend, and confidence.

Boundaries:
- this panel is clearly labeled as a development/test section;
- it is NOT connected to the project decision workflow yet;
- no answer shown here is treated as a real project-control decision;
- the API key value is never displayed;
- expected errors are displayed as safe messages via ``JevCallResult``
  (the application service already produces them).
"""

from __future__ import annotations

import streamlit as st

from project_decision.application import jev_service
from project_decision.application.jev_service import JevCallResult
from project_decision import config as _config
from project_decision.infrastructure.jev import (
    JevChoiceAnswer,
    JevNoulAnswer,
    JevScoreAnswer,
)

_SEND_BUTTON_KEY = "jev:send"


def render_jev_test_page() -> None:
    st.subheader("Jev integration test (development)")
    st.caption(
        "Development/test section — verifies the connection to the official "
        "TypeSafe Jev API only. It is not connected to the project decision "
        "workflow, and its answers are not project-control decisions."
    )

    settings = _config.load_typesafe_settings()
    if settings.has_api_key:
        st.success("TYPESAFE_API_KEY is configured (the value is never shown).")
    else:
        st.warning(
            "TYPESAFE_API_KEY is **not** configured. Get a key from "
            "https://console.typesafe.ai/keys, add it to the environment "
            "or a local .env file (see .env.example), and restart the app."
        )

    _render_payload_preview()

    if st.button(
        "Send test questions to Jev",
        key=_SEND_BUTTON_KEY,
        type="primary",
        disabled=not settings.has_api_key,
    ):
        state, questions = jev_service.prepare_test_payload()
        with st.spinner("Asking Jev about the test state..."):
            result = jev_service.send_jev_questions(state, questions, settings=settings)
        _render_result(result)


def _render_payload_preview() -> None:
    state, questions = jev_service.prepare_test_payload()
    with st.expander("Predefined test state and questions"):
        st.caption("State (JSON):")
        st.json(state, expanded=False)
        st.caption("Questions (one of each official type):")
        for name, question in questions.items():
            criteria = getattr(question, "criteria", None)
            detail = (
                f"options: {', '.join(criteria)}" if isinstance(criteria, list)
                else f"criteria: {', '.join(criteria)}" if isinstance(criteria, dict)
                else "yes/no question"
            )
            st.markdown(f"- `{name}` — {question.instructions} ({detail})")


def _render_result(result: JevCallResult) -> None:
    if not result.ok:
        st.error(result.error_message)
        return

    st.caption(
        f"Model: {result.model} · request id: {result.request_id} · "
        f"tokens: {result.input_tokens} in / {result.output_tokens} out"
    )
    for name, answer in result.answers.items():
        with st.expander(f"**{name}**", expanded=True):
            if isinstance(answer, JevChoiceAnswer):
                st.metric(label="Selected option", value=answer.choice)
                st.caption(f"Confidence: {answer.confidence:.0%}")
                st.dataframe(
                    [{"option": option, "probability": probability}
                     for option, probability in sorted(
                         answer.probabilities.items(), key=lambda item: -item[1]
                     )],
                    hide_index=True,
                    width="stretch",
                )
            elif isinstance(answer, JevScoreAnswer):
                st.metric(label="Score (expected)", value=f"{answer.score:.2f}")
                st.caption(f"Confidence: {answer.confidence:.0%}")
                st.dataframe(
                    [{"level": level,
                      "description": answer.legend.get(level, "not available"),
                      "probability": answer.probabilities.get(level, 0.0)}
                     for level in sorted(answer.legend)],
                    hide_index=True,
                    width="stretch",
                )
            elif isinstance(answer, JevNoulAnswer):
                st.progress(
                    min(max(answer.noul, 0.0), 1.0),
                    text=f"noul (probability of yes): {answer.noul:.2f}",
                )
                st.caption(
                    "Near 1 tends to yes, near 0 tends to no, near 0.5 is uncertain."
                )
            else:
                st.warning(f"Unsupported answer type: {type(answer).__name__}")

    with st.expander("Full structured response (JSON)"):
        from project_decision.infrastructure.jev import JevAnswerSet

        st.json(JevAnswerSet(
            model=result.model or "",
            answers=dict(result.answers),
            request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        ).to_payload(), expanded=False)
