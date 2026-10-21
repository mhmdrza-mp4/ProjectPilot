"""Live integration test against the real TypeSafe Jev API.

Not part of the offline unit suite: excluded from the default pytest
run (`addopts = -m 'not msp and not jev'`), and skipped automatically
when no API key is configured even when selected explicitly.

Run explicitly (requires TYPESAFE_API_KEY in the environment or .env):

    pytest -m jev -q

This test sends the documented example payload (small project state,
one official question of each type) in ONE System One request, exactly
like the Streamlit Jev test panel does. Answers are structural
assertions only — they are not project-control decisions.

The API-key check is deliberately LAZY: the configuration is read when
this test runs, not at module import, so that a plain ``pytest`` run
never loads a local ``.env`` (and therefore never puts a real key into
the process environment) merely by collecting tests.
"""

import pytest

from project_decision.application.jev_service import (
    prepare_test_payload,
    send_jev_questions,
)
from project_decision.config import load_typesafe_settings
from project_decision.infrastructure.jev import (
    JevChoiceAnswer,
    JevNoulAnswer,
    JevScoreAnswer,
)

pytestmark = pytest.mark.jev


@pytest.fixture
def live_settings():
    settings = load_typesafe_settings()
    if not settings.has_api_key:
        pytest.skip(
            "requires TYPESAFE_API_KEY (environment or .env); "
            "the live TypeSafe API is called"
        )
    return settings


def test_live_example_payload_gets_structured_answers(live_settings):
    state, questions = prepare_test_payload()
    result = send_jev_questions(state, questions, settings=live_settings)

    assert result.ok is True, result.error_message
    assert set(result.answers) == {"project_status", "schedule_concern", "schedule_risk"}

    choice = result.answers["project_status"]
    assert isinstance(choice, JevChoiceAnswer)
    assert choice.choice in {"healthy", "at_risk", "critical"}
    assert 0.0 <= choice.confidence <= 1.0
    assert all(0.0 <= p <= 1.0 for p in choice.probabilities.values())

    score = result.answers["schedule_concern"]
    assert isinstance(score, JevScoreAnswer)
    assert 0.0 <= score.score <= max(score.probabilities)
    assert 0.0 <= score.confidence <= 1.0
    assert all(0.0 <= p <= 1.0 for p in score.probabilities.values())

    noul = result.answers["schedule_risk"]
    assert isinstance(noul, JevNoulAnswer)
    assert 0.0 <= noul.noul <= 1.0

    assert result.ok is True, result.error_message
    assert set(result.answers) == {"project_status", "schedule_concern", "schedule_risk"}

    choice = result.answers["project_status"]
    assert isinstance(choice, JevChoiceAnswer)
    assert choice.choice in {"healthy", "at_risk", "critical"}
    assert 0.0 <= choice.confidence <= 1.0
    assert all(0.0 <= p <= 1.0 for p in choice.probabilities.values())

    score = result.answers["schedule_concern"]
    assert isinstance(score, JevScoreAnswer)
    assert 0.0 <= score.score <= max(score.probabilities)
    assert 0.0 <= score.confidence <= 1.0
    assert all(0.0 <= p <= 1.0 for p in score.probabilities.values())

    noul = result.answers["schedule_risk"]
    assert isinstance(noul, JevNoulAnswer)
    assert 0.0 <= noul.noul <= 1.0
