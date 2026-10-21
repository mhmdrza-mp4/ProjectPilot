"""Neutral data models for the Jev integration.

The question DTOs and answer models below are SDK-free: they mirror the
official TypeSafe primitives (see docs.typesafe.ai/primitives) so the
application and presentation layers never import ``typesafe_sdk``.
``infrastructure.jev.client`` is the only place where these are mapped
onto/served from the official SDK types.

Question shapes (per the official documentation):
- Choice: instructions + criteria, an option name keyed to optional JSON
  structure (``None`` = plain option).
- Score: instructions + ordered criterion levels.
- Noul: instructions only (yes/no judgment).

Answer shapes (per the official documentation):
- ChoiceAnswer: ``choice``, ``probabilities`` (per criterion), ``confidence``.
- ScoreAnswer: ``score`` (probability-weighted level average), ``legend``
  (level -> description), ``probabilities`` (per integer level),
  ``confidence``.
- NoulAnswer: ``noul`` in 0..1 (yes/true probability; ~0.5 = uncertain).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JevCriteria = dict[str, Any] | list[str]

DEFAULT_MODEL = "jev-latest"


# -- questions ------------------------------------------------------------------


@dataclass(frozen=True)
class JevNoulQuestion:
    """A yes/no question: the answer is the 'yes' probability."""

    instructions: str


@dataclass(frozen=True)
class JevChoiceQuestion:
    """A pick-one-option question over named criteria."""

    instructions: str
    criteria: dict[str, Any]

    @classmethod
    def from_options(cls, instructions: str, options: JevCriteria) -> "JevChoiceQuestion":
        """Plain string options become options without extra structure."""
        if isinstance(options, (list, tuple)):
            options = {str(option): None for option in options}
        return cls(instructions=instructions, criteria=dict(options))


@dataclass(frozen=True)
class JevScoreQuestion:
    """A rating question over ordered, descriptive levels."""

    instructions: str
    criteria: list[str]

    def __post_init__(self) -> None:  # noqa: D105 - keep criterion copy
        object.__setattr__(self, "criteria", list(self.criteria))


# -- answers --------------------------------------------------------------------


@dataclass(frozen=True)
class JevNoulAnswer:
    noul: float


@dataclass(frozen=True)
class JevChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class JevScoreAnswer:
    score: float
    legend: dict[int, Any]
    probabilities: dict[int, float]
    confidence: float


JevAnswer = JevNoulAnswer | JevChoiceAnswer | JevScoreAnswer


@dataclass(frozen=True)
class JevAnswerSet:
    """All answers to one System One request, keyed by question name."""

    model: str
    answers: dict[str, JevAnswer]
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """Plain (JSON-ready) view for display/logging without the SDK."""
        payload: dict[str, Any] = {
            "model": self.model,
            "request_id": self.request_id,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "answers": {},
        }
        for name, answer in self.answers.items():
            data = dict(vars(answer))
            if isinstance(answer, JevScoreAnswer):
                data["legend"] = {
                    str(level): description for level, description in answer.legend.items()
                }
                data["probabilities"] = {
                    str(level): probability
                    for level, probability in answer.probabilities.items()
                }
            payload["answers"][name] = data
        return payload
