"""Synthetic capability-marker suite (fixture) — **zero domain vocabulary**.

Every identifier here is an invented token (``synth`` / ``grid`` / ``algebra``).
The suite exists only to exercise the marker grammar
(``function_kwargs`` / ``function_positional_id`` / ``function_bare`` /
``param_marks``) and the outcome→level policy (passed / failed / skipped /
xfailed).  It is executed in a temporary copy by
``tests/test_capability_discovery_plugin.py``; the ``conftest.py`` next to this
file keeps it out of the repository-wide ``pytest tests/`` collection.
"""
import pytest


# (1) function_kwargs + declared REAL + anchor + param range -> passed -> REAL
@pytest.mark.capability(
    id="synth.grid.trapezoid",
    label="synthetic trapezoid rule vs closed form",
    level="REAL",
    anchors=["ref:closed_form:quadratic"],
    params={"n_samples": {"min": 32, "max": 4096}},
    tags=["numeric"],
)
def test_trapezoid_matches_closed_form():
    assert sum(range(1, 5)) == 10


# (2) function_positional_id, same capability -> second test node, passed
@pytest.mark.capability(
    "synth.grid.trapezoid",
    level="REAL",
    anchors=["ref:closed_form:cubic"],
)
def test_trapezoid_second_source():
    assert 2 + 2 == 4


# (3) function_bare -> id derived from the node id -> undeclared -> WITHHELD
@pytest.mark.capability
def test_bare_marker_case():
    assert "".join(["a", "b"]) == "ab"


# (4) declared REAL but no anchor -> passed yet capped at WITHHELD
@pytest.mark.capability(id="synth.claims.no_anchor", level="REAL")
def test_reals_need_an_anchor():
    assert 3 * 3 == 9


# (5) skip modifier -> skipped -> WITHHELD
@pytest.mark.skip(reason="synthetic skip case")
@pytest.mark.capability(
    id="synth.case.skipped", level="REAL", anchors=["ref:closed_form:quadratic"]
)
def test_skipped_case():
    assert True


# (6) xfail modifier -> xfailed -> WITHHELD
@pytest.mark.xfail(reason="synthetic known-defect case", strict=True)
@pytest.mark.capability(
    id="synth.case.xfail", level="REAL", anchors=["ref:closed_form:quadratic"]
)
def test_expected_failure_case():
    assert 1 == 2


# (7) failing test -> contradicted -> MOCK
@pytest.mark.capability(
    id="synth.case.contradiction", level="REAL", anchors=["ref:closed_form:quadratic"]
)
def test_contradiction_case():
    assert 1 == 2


# (8) param_marks -> two parametrised cases under one capability, both pass
@pytest.mark.parametrize(
    "n",
    [
        pytest.param(
            8,
            marks=pytest.mark.capability(
                id="synth.grid.richardson",
                level="REAL",
                anchors=["ref:richardson:order"],
            ),
        ),
        pytest.param(
            16,
            marks=pytest.mark.capability(
                id="synth.grid.richardson",
                level="REAL",
                anchors=["ref:richardson:order"],
            ),
        ),
    ],
)
def test_richardson_param(n):
    assert n in (8, 16)