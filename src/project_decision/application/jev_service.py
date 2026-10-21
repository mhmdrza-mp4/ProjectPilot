"""Application service: ask Jev questions about a state.

Follows the ``LoadedProjectState`` pattern: expected failures
(no key, authentication, network, API errors, unexpected responses)
become ``JevCallResult`` data the presentation layer can display
safely — errors are never silently swallowed and never contain the
API key. Truly unexpected bugs still propagate.

Stage boundary (intentional): this verifies that the application can
communicate with the official Jev API. It does not wire Jev into the
Microsoft Project decision workflow, does not choose project actions,
and performs no optimization. The test payload below is an integration
example — its answers are not project-control decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from project_decision.config import TypesafeSettings, load_typesafe_settings
from project_decision.infrastructure.jev import (
    JevApiError,
    JevAuthenticationError,
    JevChoiceQuestion,
    JevConfigurationError,
    JevConnectionError,
    JevError,
    JevInvalidRequestError,
    JevNoulQuestion,
    JevScoreQuestion,
    JevUnexpectedResponseError,
    TypeSafeJevClient,
)
from project_decision.infrastructure.jev.models import JevAnswer


def _error_kind(exc: JevError) -> str:
    """Stable, UI-friendly category for the failure."""
    if isinstance(exc, JevConfigurationError):
        return "configuration"
    if isinstance(exc, JevAuthenticationError):
        return "authentication"
    if isinstance(exc, JevConnectionError):
        return "connection"
    if isinstance(exc, JevInvalidRequestError):
        return "invalid_request"
    if isinstance(exc, JevApiError):
        return "api"
    if isinstance(exc, JevUnexpectedResponseError):
        return "unexpected_response"
    return "unknown"


@dataclass(frozen=True)
class JevCallResult:
    """One System One request: answers, or a safe error message."""

    ok: bool
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    answers: dict[str, JevAnswer] = field(default_factory=dict)
    error_message: str | None = None
    error_kind: str | None = None  # e.g. "configuration", "authentication"


def prepare_test_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    """The documented integration example: a small project state and
    one question of each official type (Choice, Score, Noul).

    The returned answers must not be treated as real project-control
    decisions; their purpose is to verify the API integration end to end.
    """
    state = {
        "project": {
            "name": "Example Project",
            "schedule_status": "At risk",
            "completed_tasks": 42,
            "in_progress_tasks": 8,
        }
    }
    questions = {
        "project_status": JevChoiceQuestion.from_options(
            "What is the current project status?",
            options=["healthy", "at_risk", "critical"],
        ),
        "schedule_concern": JevScoreQuestion(
            instructions="How concerning is the current schedule status?",
            criteria=[
                "not concerning",
                "somewhat concerning",
                "concerning",
                "very concerning",
            ],
        ),
        "schedule_risk": JevNoulQuestion(
            instructions="Does the provided project state indicate a schedule risk?"
        ),
    }
    return state, questions


def send_jev_questions(
    state: Any,
    questions: dict[str, Any],
    *,
    settings: TypesafeSettings | None = None,
    env_file_directory: Path | None = None,
    client_factory: Any | None = None,
) -> JevCallResult:
    """Send the questions to the official TypeSafe API, never raising
    expected failures."""
    settings = settings or load_typesafe_settings(env_file_directory)
    if not settings.has_api_key:
        return JevCallResult(
            ok=False,
            error_message=(
                "TYPESAFE_API_KEY is not configured. Get a key from the "
                "TypeSafe console, put it in the environment or a local "
                ".env file (see .env.example), and restart the app."
            ),
            error_kind="configuration",
        )
    client = TypeSafeJevClient(
        settings.api_key,
        client_factory=client_factory,
    )
    try:
        answer_set = client.ask(state, questions)
    except JevError as exc:
        return JevCallResult(
            ok=False,
            error_message=str(exc),
            error_kind=_error_kind(exc),
        )
    return JevCallResult(
        ok=True,
        model=answer_set.model,
        request_id=answer_set.request_id,
        input_tokens=answer_set.input_tokens,
        output_tokens=answer_set.output_tokens,
        answers=answer_set.answers,
    )