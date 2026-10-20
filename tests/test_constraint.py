import pytest

from project_decision.domain import ConstraintKind
from tests.conftest import make_constraint


class TestConstraint:
    def test_valid_constraint(self):
        constraint = make_constraint()

        assert constraint.kind == ConstraintKind.DEADLINE
        assert constraint.description

    @pytest.mark.parametrize(
        "kind",
        [
            ConstraintKind.DEADLINE,
            ConstraintKind.RESOURCE_CAPACITY,
            ConstraintKind.TASK_DEPENDENCY,
            ConstraintKind.MILESTONE,
            ConstraintKind.OTHER,
        ],
    )
    def test_all_kinds_representable(self, kind):
        constraint = make_constraint(kind=kind)
        assert constraint.kind is kind

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_constraint(constraint_id="")

    def test_empty_description_rejected(self):
        with pytest.raises(ValueError, match="description"):
            make_constraint(constraint_id="C", description="")
