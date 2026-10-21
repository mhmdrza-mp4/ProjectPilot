"""Fakes for the official TypeSafe SDK used by offline unit tests.

``FakeTypeSafeClient`` imitates ``typesafe_sdk.TypeSafeClient`` closely
enough for our wrapper: context manager, ``system_one(state=...,
questions=...)`` returning fake typed answer objects (``.type``,
``.choice``/``.score``/``.noul`` plus probabilities/legend/confidence),
or raising a configured SDK exception.

SDK exception fakes are built with ``__new__`` (bypassing ``__init__``)
so they match the real classes for ``isinstance`` routing in the client
without depending on their constructor signatures.
"""

from __future__ import annotations

import typesafe_sdk as sdk


def make_sdk_exception(exception_class, **attributes):
    """Instance of a real SDK exception class with hand-set attributes."""
    exc = exception_class.__new__(exception_class)
    for name, value in attributes.items():
        setattr(exc, name, value)
    return exc


class FakeUsage:
    def __init__(self, input_tokens=None, output_tokens=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeChoiceAnswer:
    def __init__(self, choice, probabilities, confidence):
        self.type = "choice"
        self.choice = choice
        self.probabilities = probabilities
        self.confidence = confidence


class FakeScoreAnswer:
    def __init__(self, score, legend, probabilities, confidence):
        self.type = "score"
        self.score = score
        self.legend = legend
        self.probabilities = probabilities
        self.confidence = confidence


class FakeNoulAnswer:
    def __init__(self, noul):
        self.type = "noul"
        self.noul = noul


class FakeResponse:
    def __init__(self, answers, model="jev-latest", request_id="req-123",
                 usage=None):
        self.model = model
        self.usage = usage or FakeUsage(10, 1)
        self.answers = answers
        self.request_id = request_id


class FakeTypeSafeClient:
    """Context-manager SDK client recording the last request."""

    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []
        self.was_closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.was_closed = True
        return False

    def system_one(self, *, state, questions):
        self.calls.append({"state": state, "questions": questions})
        if self.error is not None:
            raise self.error
        return self.response


def fake_client_factory(client):
    """A ClientFactory double: ignores kwargs, always returns ``client``."""

    def _factory(**kwargs):
        client.received_kwargs = kwargs
        return client

    return _factory
