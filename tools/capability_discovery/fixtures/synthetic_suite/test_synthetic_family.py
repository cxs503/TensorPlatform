"""Synthetic fixture (2/3): module-level ``pytestmark`` + class-level marker.

Zero domain vocabulary — invented tokens only.
"""
import pytest

# (5) module_pytestmark: covers every test in this file
pytestmark = [
    pytest.mark.capability(
        id="synth.family.module_scope",
        level="WITHHELD",
        anchors=["ref:module:doc"],
    ),
]


# (4) class_kwargs: inherited by both methods
@pytest.mark.capability(
    id="synth.algebra.vector_norm",
    label="synthetic norm identity",
    level="REAL",
    anchors=["ref:closed_form:pythagoras"],
)
class TestVectorNorm:
    def test_axis_aligned(self):
        assert 3**2 + 4**2 == 25

    def test_scaled(self):
        assert (2 * 5) ** 2 == 100