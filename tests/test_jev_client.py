"""Offline unit tests for the TypeSafe/Jev client wrapper.

The external TypeSafe API is never called: the SDK client is replaced
with ``FakeTypeSafeClient`` through the client factory; exception
routing uses real SDK exception classes built with hand-set attributes
(no live key, no network).
"""

import sys

import pytest

import typesafe_sdk as sdk

from project_decision.infrastructure.jev import (
    JevApiError,
    JevAuthenticationError,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevConfigurationError,
    JevConnectionError,
    JevError,
    JevInvalidRequestError,
    JevNoulAnswer,
    JevNoulQuestion,
    JevScoreAnswer,
    JevScoreQuestion,
    JevUnexpectedResponseError,
    TypeSafeJevClient,
)
from tests.fakes.fake_type_safe import (
    FakeChoiceAnswer,
    FakeNoulAnswer,
    FakeResponse,
    FakeScoreAnswer,
    FakeTypeSafeClient,
    fake_client_factory,
    make_sdk_exception,
)

TEST_KEY = "sk-test-key"


def make_client(client: FakeTypeSafeClient) -> TypeSafeJevClient:
    return TypeSafeJevClient(TEST_KEY, client_factory=fake_client_factory(client))


def make_standard_questions() -> dict:
    return {
        "status": JevChoiceQuestion.from_options(
            "What is the status?", options=["healthy", "at_risk", "critical"]
        ),
        "concern": JevScoreQuestion(
            instructions="How concerning is this?",
            criteria=["low", "medium", "high"],
        ),
        "risk": JevNoulQuestion(instructions="Is there a risk?"),
    }


class TestClientConstruction:
    def test_missing_key_is_a_configuration_error(self):
        with pytest.raises(JevConfigurationError, match="TYPESAFE_API_KEY"):
            TypeSafeJevClient(api_key=None)

    def test_blank_key_is_a_configuration_error(self):
        with pytest.raises(JevConfigurationError, match="TYPESAFE_API_KEY"):
            TypeSafeJevClient(api_key="   ")

    def test_configured_key_is_kept_private(self):
        client = TypeSafeJevClient(TEST_KEY)
        assert repr(client).count(TEST_KEY) == 0


class TestSuccessfulRequests:
    def test_questions_are_sent_to_the_official_sdk_types(self):
        fake = FakeTypeSafeClient(response=FakeResponse({}))
        client = make_client(fake)

        result = client.ask({"project": {"name": "Example"}}, make_standard_questions())

        assert len(fake.calls) == 1
        sent = fake.calls[0]["questions"]
        assert isinstance(sent["status"], sdk.Choice)
        assert sent["status"].criteria == {
            "healthy": None,
            "at_risk": None,
            "critical": None,
        }
        assert isinstance(sent["concern"], sdk.Score)
        assert sent["concern"].criteria == ["low", "medium", "high"]
        assert isinstance(sent["risk"], sdk.Noul)
        assert fake.calls[0]["state"] == {"project": {"name": "Example"}}
        assert fake.received_kwargs["api_key"] == TEST_KEY
        assert fake.received_kwargs["model"] == "jev-latest"
        assert fake.was_closed
        assert isinstance(result.model, str)

    def test_choice_answer_is_mapped_with_probabilities_and_confidence(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse(
                {
                    "status": FakeChoiceAnswer(
                        choice="at_risk",
                        probabilities={"healthy": 0.1, "at_risk": 0.8, "critical": 0.1},
                        confidence=0.7,
                    )
                }
            )
        )
        result = make_client(fake).ask(
            {}, {"status": JevChoiceQuestion.from_options("What?", options=["healthy", "at_risk"])}
        )

        answer = result.answers["status"]
        assert isinstance(answer, JevChoiceAnswer)
        assert answer.choice == "at_risk"
        assert answer.probabilities["at_risk"] == pytest.approx(0.8)
        assert answer.confidence == pytest.approx(0.7)

    def test_score_answer_is_mapped_with_legend_and_probabilities(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse(
                {
                    "concern": FakeScoreAnswer(
                        score=1.7,
                        legend={0: "low", 1: "medium", 2: "high"},
                        probabilities={0: 0.2, 1: 0.5, 2: 0.3},
                        confidence=0.6,
                    )
                }
            )
        )
        result = make_client(fake).ask(
            {},
            {"concern": JevScoreQuestion(instructions="How?", criteria=["low", "medium", "high"])},
        )

        answer = result.answers["concern"]
        assert isinstance(answer, JevScoreAnswer)
        assert answer.score == pytest.approx(1.7)
        assert answer.legend[1] == "medium"
        assert answer.probabilities[2] == pytest.approx(0.3)
        assert answer.confidence == pytest.approx(0.6)
        assert all(isinstance(level, int) for level in answer.legend)

    def test_noul_answer_is_mapped(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse({"risk": FakeNoulAnswer(noul=0.87)})
        )
        result = make_client(fake).ask(
            {}, {"risk": JevNoulQuestion(instructions="Is there a risk?")}
        )

        answer = result.answers["risk"]
        assert isinstance(answer, JevNoulAnswer)
        assert answer.noul == pytest.approx(0.87)

    def test_response_metadata_is_preserved(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse({}, model="jev-latest", request_id="req-42")
        )
        result = make_client(fake).ask(
            {}, {"risk": JevNoulQuestion(instructions="x?")}
        )

        assert result.model == "jev-latest"
        assert result.request_id == "req-42"
        assert result.input_tokens == 10
        assert result.output_tokens == 1

    def test_payload_view_is_json_like(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse(
                {
                    "concern": FakeScoreAnswer(
                        score=1.0,
                        legend={1: "medium"},
                        probabilities={1: 0.9},
                        confidence=0.5,
                    )
                }
            )
        )
        result = make_client(fake).ask(
            {}, {"concern": JevScoreQuestion(instructions="How?", criteria=["low", "high"])}
        )
        import json

        payload = result.to_payload()
        json.dumps(payload)  # must be JSON-compatible
        assert payload["answers"]["concern"]["probabilities"] == {"1": 0.9}

    def test_missing_questions_rejected_before_any_request(self):
        fake = FakeTypeSafeClient(response=FakeResponse({}))
        with pytest.raises(JevInvalidRequestError):
            make_client(fake).ask({}, {})
        assert fake.calls == []

    def test_unsupported_question_object_is_rejected_as_invalid(self):
        fake = FakeTypeSafeClient(response=FakeResponse({}))
        with pytest.raises(JevInvalidRequestError, match="unsupported question"):
            make_client(fake).ask({}, {"x": object()})


class TestUnexpectedResponse:
    def test_unknown_answer_type_is_reported_not_crashed(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse({"x": FakeNoulAnswer(noul=0.5)})
        )
        fake.response.answers["x"].type = "box"  # we never ask box questions
        with pytest.raises(JevUnexpectedResponseError, match="box"):
            make_client(fake).ask({}, {"x": JevNoulQuestion(instructions="x?")})

    def test_unreadable_response_is_reported_not_crashed(self):
        class BrokenResponse:
            answers = None  # not a dict

        fake = FakeTypeSafeClient(response=BrokenResponse())
        with pytest.raises(JevUnexpectedResponseError, match="read"):
            make_client(fake).ask({}, {"x": JevNoulQuestion(instructions="x?")})


class TestErrorTranslation:
    @staticmethod
    def _client_with_error(error: Exception) -> TypeSafeJevClient:
        return make_client(FakeTypeSafeClient(error=error))

    def test_authentication_failure_is_translated(self):
        error = make_sdk_exception(
            sdk.TypeSafeAuthenticationError, status=401, body={"error": "nope"},
            headers={}, endpoint="POST https://api.typesafe.ai/v1/systemone",
        )
        with pytest.raises(JevAuthenticationError, match="401"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_connection_failure_is_translated(self):
        error = make_sdk_exception(sdk.TypeSafeAPIConnectionError)
        with pytest.raises(JevConnectionError, match="reach"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_timeout_is_translated_as_connection_failure(self):
        error = make_sdk_exception(sdk.TypeSafeAPITimeoutError, timeout=1.0)
        with pytest.raises(JevConnectionError, match="timed out"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_bad_request_is_translated_as_invalid_request(self):
        error = make_sdk_exception(
            sdk.TypeSafeBadRequestError, status=400, body={"message": "bad criteria"},
            headers={}, endpoint="x",
        )
        with pytest.raises(JevInvalidRequestError, match="criteria"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_unprocessable_entity_is_translated_as_invalid_request(self):
        error = make_sdk_exception(
            sdk.TypeSafeUnprocessableEntityError, status=422, body={"detail": "nope"},
            headers={}, endpoint="x",
        )
        with pytest.raises(JevInvalidRequestError, match="422"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_rate_limit_is_translated_with_status_and_request_id(self):
        error = make_sdk_exception(
            sdk.TypeSafeRateLimitError, status=429, body=None, headers={},
            endpoint="x",
        )
        with pytest.raises(JevApiError, match="429") as caught:
            self._client_with_error(error).ask({}, make_standard_questions())
        assert caught.value.status == 429

    def test_server_error_is_translated(self):
        error = make_sdk_exception(
            sdk.TypeSafeInternalServerError, status=500, body="boom",
            headers={}, endpoint="x",
        )
        with pytest.raises(JevApiError, match="500"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_response_validation_error_is_translated(self):
        error = make_sdk_exception(
            sdk.TypeSafeAPIResponseValidationError, field_path="answers.tone.confidence"
        )
        with pytest.raises(JevUnexpectedResponseError, match="answers.tone"):
            self._client_with_error(error).ask({}, make_standard_questions())

    def test_creation_failure_of_invalid_key_is_a_configuration_error(self):
        def broken_factory(**kwargs):
            raise sdk.TypeSafeError("invalid api key format")

        client = TypeSafeJevClient(TEST_KEY, client_factory=broken_factory)
        with pytest.raises(JevConfigurationError, match="configuration"):
            client.ask({}, {"x": JevNoulQuestion(instructions="x?")})

    def test_missing_sdk_is_translated_before_the_import(
        self, monkeypatch
    ):
        # Simulate an environment without the optional import: the
        # translator must classify the ImportError WITHOUT importing the
        # missing module first (the old order raised a bare ImportError).
        monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
        from project_decision.infrastructure.jev.client import (
            _translate_creation_error,
        )

        translated = _translate_creation_error(ImportError("missing sdk"), TEST_KEY)

        assert isinstance(translated, JevConfigurationError)
        assert "not installed" in str(translated)

    def test_api_key_is_never_leaked_in_error_messages(self):
        body = {"echo": "sent key sk-test-key-123 illegally"}
        error = make_sdk_exception(
            sdk.TypeSafeBadRequestError, status=400, body=body, headers={}, endpoint="x",
        )
        client = TypeSafeJevClient(
            "sk-test-key-123", client_factory=fake_client_factory(
                FakeTypeSafeClient(error=error)
            )
        )
        try:
            client.ask({}, make_standard_questions())
        except JevError as exc:
            assert "sk-test-key-123" not in str(exc)


class TestNoSilentSwallowing:
    def test_jev_errors_pass_through_unchanged(self):
        def raising_factory(**kwargs):
            raise JevConfigurationError("already classified")

        with pytest.raises(JevConfigurationError, match="already classified"):
            TypeSafeJevClient(TEST_KEY, client_factory=raising_factory).ask(
                {}, {"x": JevNoulQuestion(instructions="x?")}
            )
