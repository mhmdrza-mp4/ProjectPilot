import pytest

from tests.conftest import make_resource


class TestResource:
    def test_valid_resource(self):
        resource = make_resource()

        assert resource.id == "R1"
        assert resource.capacity_units == 1.0

    def test_custom_capacity(self):
        resource = make_resource(capacity_units=4)
        assert resource.capacity_units == 4

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_resource(resource_id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            make_resource(resource_id="R", name="")

    def test_negative_capacity_rejected(self):
        with pytest.raises(ValueError, match="capacity"):
            make_resource(capacity_units=-1)
