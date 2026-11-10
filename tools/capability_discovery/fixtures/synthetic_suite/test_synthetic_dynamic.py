"""Synthetic fixture (3/3): non-literal marker metadata must be *reported*, not guessed.

The marker value is a module-level name, so the static scanner cannot know the
capability id.  It records a diagnostic instead of inventing one — the test
itself still runs normally under pytest.
"""
import pytest

SYNTH_DYNAMIC_ID = "synth.dynamic.value"


@pytest.mark.capability(id=SYNTH_DYNAMIC_ID, level="REAL")  # non-literal -> diagnostic
def test_dynamic_marker_value():
    assert SYNTH_DYNAMIC_ID.endswith(".value")