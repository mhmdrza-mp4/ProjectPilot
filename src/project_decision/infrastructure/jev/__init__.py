"""Isolated Jev (TypeSafe) infrastructure package.

Public surface for the rest of the application:
- ``TypeSafeJevClient`` — official-SDK-backed client (see client.py),
- neutral question/answer models (see models.py; no SDK imports),
- expected failure types (see exceptions.py).

The domain never imports anything from this package, and the Microsoft
Project code never touches it.
"""

from project_decision.infrastructure.jev.client import TypeSafeJevClient
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

__all__ = [
    "DEFAULT_MODEL",
    "JevAnswer",
    "JevAnswerSet",
    "JevApiError",
    "JevAuthenticationError",
    "JevChoiceAnswer",
    "JevChoiceQuestion",
    "JevConfigurationError",
    "JevConnectionError",
    "JevError",
    "JevInvalidRequestError",
    "JevNoulAnswer",
    "JevNoulQuestion",
    "JevScoreAnswer",
    "JevScoreQuestion",
    "JevUnexpectedResponseError",
    "TypeSafeJevClient",
]
