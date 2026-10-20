from datetime import date

import pytest

from tests.conftest import make_milestone


class TestMilestone:
    def test_valid_milestone(self):
        milestone = make_milestone()

        assert milestone.id == "M1"
        assert milestone.date == date(2026, 6, 1)

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_milestone(milestone_id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            make_milestone(milestone_id="M", name="")
