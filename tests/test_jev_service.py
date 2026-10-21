"""Tests for the application-level Jev service (errors become data)."""

from pathlib import Path

import pytest

from project_decision.application.jev_service import (
    JevCallResult,
    prepare_test_payload,
    send_jev_questions,
)
from project_decision.config import TypesafeSettings
from project_decision.infrastructure.jev import (
    JevAuthenticationError,
    JevChoiceQuestion,
    JevNoulAnswer,
    JevNoulQuestion,
    JevScoreQuestion,
)
from tests.fakes.fake_type_safe import (
    FakeNoulAnswer,
    FakeResponse,
    FakeTypeSafeClient,
    fake_client_factory,
)


def configured_settings(key: str = "sk-service-key") -> TypesafeSettings:
    return TypesafeSettings(api_key=key)


def questions_for_fake() -> dict:
    return {"risk": JevNoulQuestion(instructions="Is there a risk?")}


class TestConfiguration:
    def test_missing_key_returns_a_clear_configuration_error(self):
        result = send_jev_questions(
            {}, questions_for_fake(), settings=TypesafeSettings(api_key=None)
        )

        assert result.ok is False
        assert result.error_kind == "configuration"
        assert "TYPESAFE_API_KEY" in result.error_message
        assert result.answers == {}

    def test_key_is_never_leaked_by_the_service_result(self):
        result = send_jev_questions(
            {}, questions_for_fake(), settings=configured_settings("sk-service-key"),
            client_factory=fake_client_factory(
                FakeTypeSafeClient(response=FakeResponse({}))
            ),
        )
        assert result.ok is True
        assert "sk-service-key" not in str(result.model)


class TestSuccessfulCalls:
    def test_answers_are_returned_as_application_data(self):
        fake = FakeTypeSafeClient(
            response=FakeResponse({"risk": FakeNoulAnswer(noul=0.9)})
        )
        result = send_jev_questions(
            {"project": {"name": "Example"}},
            questions_for_fake(),
            settings=configured_settings(),
            client_factory=fake_client_factory(fake),
        )

        assert result.ok is True
        assert result.error_message is None
        assert isinstance(result.answers["risk"], JevNoulAnswer)
        assert result.answers["risk"].noul == pytest.approx(0.9)
        assert result.model == "jev-latest"
        # service passes key/model through to the SDK client construction
        assert fake.received_kwargs == {"api_key": "sk-service-key", "model": "jev-latest"}

    def test_prepares_the_documented_example_payload(self):
        state, questions = prepare_test_payload()

        assert state["project"]["name"] == "Example Project"
        assert state["project"]["completed_tasks"] == 42
        assert state["project"]["in_progress_tasks"] == 8
        assert set(questions) == {"project_status", "schedule_concern", "schedule_risk"}
        assert isinstance(questions["project_status"], JevChoiceQuestion)
        assert isinstance(questions["schedule_concern"], JevScoreQuestion)
        assert isinstance(questions["schedule_risk"], JevNoulQuestion)


class TestErrorHandling:
    def test_authentication_failure_becomes_a_result_not_an_exception(self):
        def broken_factory(**kwargs):
            raise JevAuthenticationError("HTTP 401 rejected")

        result = send_jev_questions(
            {}, questions_for_fake(), settings=configured_settings(),
            client_factory=broken_factory,
        )

        assert result.ok is False
        assert "401" in result.error_message
        assert result.error_kind == "authentication"

    def test_truly_unexpected_failures_propagate(self, monkeypatch):
        import project_decision.application.jev_service as service

        class ExplodingClient:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self, *args, **kwargs):
                raise RuntimeError("bug, not an expected failure")

        monkeypatch.setattr(service, "TypeSafeJevClient", ExplodingClient)
        with pytest.raises(RuntimeError, match="bug"):
            send_jev_questions(
                {}, questions_for_fake(), settings=configured_settings()
            )

    def test_error_result_has_no_answers(self):
        def broken_factory(**kwargs):
            raise JevAuthenticationError("HTTP 401")

        result = send_jev_questions(
            {}, questions_for_fake(), settings=configured_settings(),
            client_factory=broken_factory,
        )
        assert result.answers == {}
        assert result.ok is False


class TestCallResultShape:
    def test_success_result_carries_metadata(self):
        result = JevCallResult(
            ok=True, model="jev-latest", request_id="req-1", input_tokens=5,
            output_tokens=2,
        )
        assert result.ok and result.error_message is None
