import pytest

from impl import transform


def test_transform_smoke():
    with pytest.raises(NotImplementedError):
        transform([1.0])
