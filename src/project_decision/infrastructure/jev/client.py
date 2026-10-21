"""Official TypeSafe (Jev) client wrapper — the only module that talks SDK.

Responsibilities (kept deliberately small):
- build the official ``typesafe_sdk.TypeSafeClient`` with the configured
  API key and ``model="jev-latest"``,
- send one state with several questions in a single System One request
  (the official usage: every question sees the same state),
- map the typed SDK answers into the neutral ``models`` dataclasses,
- translate SDK exceptions into ``exceptions`` errors, redacting the API
  key from anything that could be echoed back.

This client never evaluates questions itself and holds no state beyond
its configuration; it is not connected to the project decision workflow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from project_decision.infrastructure.jev.exceptions import (
    JevApiError,
    JevAuthenticationError,
    JevConfigurationError,
    JevConnectionError,
    JevError,
    JevInvalidRequestError,
    JevUnexpectedResponseError,
)
from project_decision.infrastructure.jev.models import (
    DEFAULT_MODEL,
    JevAnswer,
    JevAnswerSet,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevNoulAnswer,
    JevNoulQuestion,
    JevScoreAnswer,
    JevScoreQuestion,
)

ClientFactory = Callable[..., Any]
"""Builds SDK client objects (keyword ``api_key``/``model``); injectable for tests."""


def _default_client_factory(*, api_key: str, model: str) -> Any:
    from typesafe_sdk import TypeSafeClient

    return TypeSafeClient(api_key=api_key, model=model)


class TypeSafeJevClient:
    """Asks Jev questions about a state via the official TypeSafe SDK."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if api_key is None or not api_key.strip():
            raise JevConfigurationError(
                "TYPESAFE_API_KEY is not configured. Create a key at "
                "https://console.typesafe.ai/keys and provide it via the "
                "environment or a local .env file (see .env.example); then "
                "restart the application."
            )
        self._api_key = api_key
        self._model = model
        self._client_factory = client_factory or _default_client_factory

    # -- public API ---------------------------------------------------------------

    def ask(self, state: Any, questions: Mapping[str, Any]) -> JevAnswerSet:
        """Send one state with several questions; get one answer each."""
        if not questions:
            raise JevInvalidRequestError("at least one question is required")
        sdk_questions = {name: _to_sdk_question(q) for name, q in questions.items()}
        try:
            with self._new_sdk_client() as client:
                response = client.system_one(state=state, questions=sdk_questions)
        except JevError:
            raise
        except Exception as exc:
            raise _translate_request_error(exc, self._api_key) from exc
        return _map_response(response, self._api_key)

    # -- internals ------------------------------------------------------------------

    def _new_sdk_client(self) -> Any:
        try:
            return self._client_factory(api_key=self._api_key, model=self._model)
        except JevError:
            raise
        except Exception as exc:
            # Per the official docs, invalid API keys raise TypeSafeError
            # during client creation, before any request.
            raise _translate_creation_error(exc, self._api_key) from exc


def _to_sdk_question(question: Any) -> Any:
    """Map a neutral question DTO onto the official SDK question type."""
    from typesafe_sdk import Choice, Noul, Score

    if isinstance(question, JevNoulQuestion):
        return Noul(instructions=question.instructions)
    if isinstance(question, JevChoiceQuestion):
        return Choice(instructions=question.instructions, criteria=question.criteria)
    if isinstance(question, JevScoreQuestion):
        return Score(instructions=question.instructions, criteria=question.criteria)
    raise JevInvalidRequestError(
        f"unsupported question type: {type(question).__name__}"
    )


def _translate_creation_error(exc: Exception, api_key: str) -> JevError:
    # A missing SDK must be translated BEFORE importing it: the import
    # itself is the ImportError we are trying to classify.
    if isinstance(exc, ImportError):
        return JevConfigurationError(
            "the official TypeSafe SDK (typesafe-sdk) is not installed; "
            "install it from the project dependencies."
        )

    import typesafe_sdk as sdk

    if isinstance(exc, sdk.TypeSafeError):
        return JevConfigurationError(
            "the TypeSafe SDK rejected the client configuration "
            "(often an invalid API key format); check TYPESAFE_API_KEY."
        )
    return JevUnexpectedResponseError(
        f"unexpected failure while creating the TypeSafe client: {type(exc).__name__}"
    )


def _translate_request_error(exc: Exception, api_key: str) -> JevError:
    import typesafe_sdk as sdk

    if isinstance(exc, sdk.TypeSafeAuthenticationError):
        return JevAuthenticationError(
            "Jev authentication failed (HTTP 401); the configured "
            "TYPESAFE_API_KEY was not accepted."
        )
    if isinstance(exc, sdk.TypeSafeAPITimeoutError):
        return JevConnectionError("the request to the TypeSafe API timed out.")
    if isinstance(exc, sdk.TypeSafeAPIConnectionError):
        return JevConnectionError(
            "could not reach the TypeSafe API (network error; no response)."
        )
    if isinstance(exc, (sdk.TypeSafeBadRequestError, sdk.TypeSafeUnprocessableEntityError)):
        return JevInvalidRequestError(
            "TypeSafe rejected the request as invalid "
            f"(HTTP {exc.status}): {_brief_body(exc, api_key)}"
        )
    if isinstance(exc, sdk.TypeSafeAPIResponseValidationError):
        return JevUnexpectedResponseError(
            "the TypeSafe response was missing or structurally invalid "
            f"({getattr(exc, 'field_path', 'unknown field')})."
        )
    if isinstance(exc, sdk.TypeSafeAPIError):
        request_note = (
            f" (request id {exc.request_id})" if getattr(exc, "request_id", None) else ""
        )
        return JevApiError(
            f"TypeSafe API error (HTTP {exc.status}){request_note}: "
            f"{_brief_body(exc, api_key)}",
            status=exc.status,
            request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, sdk.TypeSafeError):
        return JevApiError(
            f"TypeSafe SDK error: {type(exc).__name__}", status=None, request_id=None
        )
    return JevUnexpectedResponseError(
        f"unexpected error while talking to the TypeSafe API: {type(exc).__name__}"
    )


def _brief_body(exc: Exception, api_key: str) -> str:
    body = getattr(exc, "body", None)
    if body is None:
        body = ""
    text = str(body)
    if api_key:
        text = text.replace(api_key, "[redacted]")
    if len(text) > 300:
        text = text[:297] + "..."
    return text or "(no details)"


def _map_response(response: Any, api_key: str) -> JevAnswerSet:
    """Map the SDK response onto neutral answer models."""
    try:
        answers: dict[str, JevAnswer] = {}
        for name, raw in response.answers.items():
            kind = raw.type
            if kind == "noul":
                answers[name] = JevNoulAnswer(noul=raw.noul)
            elif kind == "choice":
                answers[name] = JevChoiceAnswer(
                    choice=raw.choice,
                    probabilities=dict(raw.probabilities),
                    confidence=raw.confidence,
                )
            elif kind == "score":
                answers[name] = JevScoreAnswer(
                    score=raw.score,
                    legend={int(level): detail for level, detail in raw.legend.items()},
                    probabilities={
                        int(level): probability
                        for level, probability in raw.probabilities.items()
                    },
                    confidence=raw.confidence,
                )
            else:
                raise JevUnexpectedResponseError(
                    f"answer {name!r} has an unexpected type {kind!r}"
                )
        usage = response.usage
        return JevAnswerSet(
            model=response.model,
            answers=answers,
            request_id=getattr(response, "request_id", None),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    except (JevError, KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, JevError):
            raise
        raise JevUnexpectedResponseError(
            f"could not read the TypeSafe response structure: {type(exc).__name__}"
        ) from exc
