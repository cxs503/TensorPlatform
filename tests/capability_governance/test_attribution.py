"""Attribution chain: the four dimensions must be recovered, not merely mentioned."""
from __future__ import annotations

import pytest

from services.capability_governance import attribution as A
from services.capability_governance import fixtures


def _case(case_id):
    return next(c for c in fixtures.attribution_cases() if c["case_id"] == case_id)


@pytest.mark.parametrize("case_id,dimension", [
    ("case_format", "format"),
    ("case_output", "output"),
    ("case_input", "input_definition"),
    ("case_mesh", "discretisation"),
])
def test_single_dimension_is_recovered_exactly(case_id, dimension):
    result = A.attribute_case(_case(case_id), max_residual_share=1e-6)
    arm = result["arms"][0]
    assert arm["dominant_dimension"] == dimension
    assert arm["contributions"][dimension] == pytest.approx(1.0, abs=1e-9)
    assert arm["residual_share"] == pytest.approx(0.0, abs=1e-9)
    assert arm["attributed"] is True


def test_telescoping_identity_holds_for_every_arm():
    result = A.attribute_cases(fixtures.attribution_cases())
    for case in result["cases"]:
        for arm in case["arms"]:
            total = sum(arm["contributions"].values()) + arm["residual_share"]
            assert total == pytest.approx(1.0, abs=1e-9)


def test_combined_case_is_fully_attributed():
    result = A.attribute_case(_case("case_combined"))
    arm = result["arms"][0]
    assert arm["residual_share"] <= 0.05
    assert arm["attributed"] is True
    for dim in ("format", "output", "input_definition"):
        assert arm["contributions"][dim] > 0.0


def test_unexplained_deviation_is_refused():
    result = A.attribute_case(_case("case_unexplained"))
    arm = result["arms"][0]
    assert arm["attributed"] is False
    assert arm["residual_share"] == pytest.approx(1.0, abs=1e-9)
    assert arm["accounted_share"] == pytest.approx(0.0, abs=1e-9)


def test_missing_sensitivity_declaration_raises():
    ref = fixtures._reference_arm()
    arm = {**fixtures._reference_arm(), "arm_id": "x", "value": 100.0,
           "inputs": {"u": 1.5, "w": 2.0}}
    ref_no_sens = {k: v for k, v in ref.items() if k != "sensitivities"}
    with pytest.raises(A.AttributionError):
        A.attribute_arm(arm, ref_no_sens)


def test_single_grid_level_leaves_mesh_channel_empty():
    arm = {**fixtures._reference_arm(), "arm_id": "one-level",
           "value": 100.0 + 12.0 * 0.01 ** 2, "grid_levels": []}
    chain = A.attribute_arm(arm, fixtures._reference_arm())
    mesh_step = next(s for s in chain["steps"] if s["dimension"] == "discretisation")
    assert mesh_step["delta_deviation"] == 0.0
    assert "single grid level" in mesh_step["note"]
    assert chain["residual_share"] == pytest.approx(1.0, abs=1e-6)


def test_zero_reference_value_raises():
    ref = {**fixtures._reference_arm(), "value": 0.0}
    arm = {**fixtures._reference_arm(), "arm_id": "x", "value": 1.0}
    with pytest.raises(A.AttributionError):
        A.attribute_arm(arm, ref)


def test_batch_summary_reports_all_four_dimensions():
    result = A.attribute_cases(fixtures.attribution_cases())
    assert set(result["dimension_coverage"]) == set(A.DIMENSIONS)
    assert result["n_cases"] == len(fixtures.attribution_cases())
    # five of six cases are fully accounted for; the sixth is deliberately not
    assert result["attribution_completeness"] == pytest.approx(5.0 / 6.0, abs=1e-6)
    assert result["all_attributed"] is False