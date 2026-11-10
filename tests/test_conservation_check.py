"""Conservation-residual, refinement-order and law-contract gates (P351-W1-N01).

Acceptance criteria exercised here:

(a) the manufactured two-subdomain diffusion fixture keeps its interface residual
    and its error against the closed-form flux at or below 1e-4, and a jittered
    flux must break the gate;
(b) the observed convergence order of the interface operator is at least 0.95 for
    the linear transfer and at least 1.95 for the quadratic projection;
(e) an analytic derivative and the value the gate reports agree within 2 %;
(f) every gate is falsifiable: each positive assertion is paired with a negative
    control that must fail;
(g) no domain vocabulary in the new modules or fixtures.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools.coupling.conservation_check import (
    OPERATOR_ORDER,
    ContractError,
    DiffusionInterfaceCase,
    SegmentMesh,
    cell_averages,
    central_difference,
    contract_gate,
    diffusion_interface_case,
    jitter,
    load_contract,
    noisy_operator,
    order_certificate,
    refinement_order,
    relative_residual,
    sensitivity_gate,
    transfer,
    validate_contract,
)
from tools.evidence_domain_scan import load_lexicon, scan_paths

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "conservation_law.schema.json"
EXAMPLE = ROOT / "contracts" / "conservation_law.example.v1.json"

#: smooth, non-periodic reference profile standing in for a tangential flux
def _profile(s):
    return -2.0 * math.cos(1.0) * np.cos(np.pi * np.asarray(s))


@pytest.fixture(scope="module")
def fixture_result():
    """One solved manufactured fixture, shared by the accuracy assertions."""
    return diffusion_interface_case(n_x=192, n_y=192)


# ---------------------------------------------------------------------------
# residual definition
# ---------------------------------------------------------------------------


def test_relative_residual_is_zero_for_identical_fields():
    values = np.array([1.0, -2.0, 0.5])
    assert relative_residual(values, values) == 0.0


def test_relative_residual_matches_hand_computation():
    a = np.array([1.0, 0.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0, 0.0])
    assert relative_residual(a, b) == pytest.approx(math.sqrt(2.0), rel=1e-14)
    assert relative_residual(a, b, norm="linf") == pytest.approx(1.0, rel=1e-14)


def test_relative_residual_rejects_shape_mismatch():
    with pytest.raises(Exception):
        relative_residual([1.0, 2.0], [1.0, 2.0, 3.0])


def test_relative_residual_zero_reference_is_fail_closed():
    assert relative_residual([0.0, 0.0], [0.0, 0.0]) == 0.0
    assert relative_residual([0.0, 0.0], [1.0, 0.0]) == math.inf


# ---------------------------------------------------------------------------
# operators: conservation and design order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operator", sorted(OPERATOR_ORDER))
def test_transfer_preserves_the_zeroth_moment(operator):
    src = SegmentMesh.uniform(0.0, 1.0, 33)
    dst = SegmentMesh.uniform(0.0, 1.0, 50)
    values = cell_averages(_profile, src)
    moved = transfer(values, src, dst, operator)
    assert np.sum(moved * dst.widths) == pytest.approx(
        np.sum(values * src.widths), rel=1e-13, abs=1e-15
    )


@pytest.mark.parametrize("operator", ["linear_reconstruction", "quadratic_projection"])
def test_transfer_reproduces_a_linear_field_exactly(operator):
    """A reconstruction-based operator is exact on the fields it can represent."""
    src = SegmentMesh.uniform(0.0, 1.0, 21)
    dst = SegmentMesh.uniform(0.0, 1.0, 34)
    exact = cell_averages(lambda s: 0.75 + 1.5 * np.asarray(s), dst)
    moved = transfer(cell_averages(lambda s: 0.75 + 1.5 * np.asarray(s), src), src, dst, operator)
    assert relative_residual(exact, moved) < 1e-13


def test_donor_cell_is_only_first_order_on_a_linear_field():
    """The piecewise-constant operator cannot represent a slope: that is the baseline."""
    src = SegmentMesh.uniform(0.0, 1.0, 21)
    dst = SegmentMesh.uniform(0.0, 1.0, 34)
    exact = cell_averages(lambda s: 0.75 + 1.5 * np.asarray(s), dst)
    moved = transfer(cell_averages(lambda s: 0.75 + 1.5 * np.asarray(s), src), src, dst,
                     "donor_cell")
    error = relative_residual(exact, moved)
    assert 1e-3 < error < 1e-1, error


def test_donor_cell_operator_is_first_order():
    cert = order_certificate(_profile, "donor_cell", levels=(24, 48, 96, 192))
    assert 0.85 <= cert.observed_order <= 1.25, cert.to_dict()
    assert cert.passed


def test_linear_reconstruction_meets_the_095_order_gate():
    """Criterion (b), linear part."""
    cert = order_certificate(_profile, "linear_reconstruction", levels=(24, 48, 96, 192),
                             required_order=0.95)
    assert cert.observed_order >= 0.95, cert.to_dict()
    assert cert.passed


def test_quadratic_projection_meets_the_195_order_gate():
    """Criterion (b), quadratic part."""
    cert = order_certificate(_profile, "quadratic_projection", levels=(24, 48, 96, 192),
                             required_order=1.95)
    assert cert.observed_order >= 1.95, cert.to_dict()
    assert cert.passed


def test_quadratic_projection_beats_linear_reconstruction():
    linear = order_certificate(_profile, "linear_reconstruction", levels=(24, 48, 96, 192))
    quad = order_certificate(_profile, "quadratic_projection", levels=(24, 48, 96, 192))
    assert quad.observed_order > linear.observed_order + 0.5
    for a, b in zip(quad.residuals, linear.residuals):
        assert a < b


def test_order_gate_is_falsifiable_noisy_operator_fails():
    """Criterion (f): a mesh-independent perturbation must fail the order gate."""
    noisy = noisy_operator(amplitude=1e-2, seed=11)
    h, residuals = [], []
    for n in (24, 48, 96, 192):
        src = SegmentMesh.uniform(0.0, 1.0, n)
        dst = SegmentMesh.uniform(0.0, 1.0, int(round(1.5 * n)) + 1)
        h.append(float(dst.widths.mean()))
        residuals.append(relative_residual(cell_averages(_profile, dst),
                                           noisy(cell_averages(_profile, src), src, dst)))
    record = refinement_order(h, residuals)
    assert record["observed_order"] < 1.95, record
    assert not (record["observed_order"] >= 1.95)


def test_refinement_order_reports_unanimated_pairs_on_a_stalled_study():
    record = refinement_order([0.1, 0.05, 0.025], [0.01, 0.01, 0.01])
    assert record["observed_order"] == pytest.approx(0.0, abs=1e-12)
    assert all(abs(o) < 1e-12 for o in record["pair_orders"])


# ---------------------------------------------------------------------------
# manufactured diffusion fixture (criterion a)
# ---------------------------------------------------------------------------


def test_interface_residual_two_sides_meets_1e_4(fixture_result):
    """Criterion (a): ||F_left - F_right|| / ||F_left|| <= 1e-4."""
    assert fixture_result.residual("l2") <= 1e-4
    assert fixture_result.residual("linf") <= 1e-4


def test_interface_flux_matches_the_closed_form_within_1e_4(fixture_result):
    """Criterion (a): flux error against the analytic interface flux <= 1e-4."""
    assert fixture_result.analytic_error("left") <= 1e-4
    assert fixture_result.analytic_error("right") <= 1e-4


def test_interface_potential_is_continuous(fixture_result):
    """The value reconstructed from each side agrees to second order."""
    assert fixture_result.jump_error() <= 1e-5
    assert fixture_result.potential_error() <= 1e-5


def test_jittered_flux_breaks_the_conservation_gate(fixture_result):
    """Criterion (a) negative control: a random flux perturbation must FAIL."""
    residual = fixture_result.jitter_control(amplitude=0.3, seed=7)
    assert residual > 1e-2
    assert relative_residual(fixture_result.flux_left, fixture_result.flux_right) <= 1e-4


def test_jitter_is_deterministic_and_scales_with_amplitude():
    values = np.linspace(-1.0, 1.0, 17)
    assert np.allclose(jitter(values, 0.2, 3), jitter(values, 0.2, 3))
    assert not np.allclose(jitter(values, 0.2, 3), jitter(values, 0.2, 4))
    assert np.max(np.abs(jitter(values, 0.5, 3) - values)) > np.max(
        np.abs(jitter(values, 0.1, 3) - values)
    )


def test_fixture_flux_converges_at_second_order():
    """Two-sided residual and analytic error both shrink at ~2nd order."""
    levels = (48, 96, 192)
    two_side, analytic, h = [], [], []
    for n in levels:
        result = diffusion_interface_case(n_x=n, n_y=n)
        two_side.append(result.residual())
        analytic.append(result.analytic_error("left"))
        h.append(result.dx)
    order_two_side = math.log(two_side[0] / two_side[-1]) / math.log(h[0] / h[-1])
    order_analytic = math.log(analytic[0] / analytic[-1]) / math.log(h[0] / h[-1])
    assert order_two_side > 1.9, (two_side, order_two_side)
    assert order_analytic > 1.6, (analytic, order_analytic)


def test_fixture_rejects_degenerate_configurations():
    with pytest.raises(ValueError):
        DiffusionInterfaceCase(n_x=2)
    with pytest.raises(ValueError):
        DiffusionInterfaceCase(k_left=0.0)
    with pytest.raises(ValueError):
        DiffusionInterfaceCase(interface=1.0)


def test_fixture_reports_the_quantities_the_report_quotes(fixture_result):
    record = fixture_result.to_dict()
    for key in ("interface_residual_l2", "analytic_error_left", "jitter_control_residual",
                "dx", "dy", "n_x", "n_y"):
        assert key in record
    assert record["n_x"] == 192 and record["n_y"] == 192


# ---------------------------------------------------------------------------
# sensitivity consistency (criterion e)
# ---------------------------------------------------------------------------


def test_analytic_gate_sensitivity_matches_central_difference(fixture_result):
    """Criterion (e): analytic d(gate)/d(scale) vs finite differences within 2 %.

    The probe exchange carries a deliberate 5 % flux mismatch: a residual that is
    already at round-off would put the finite-difference step outside the smooth
    branch of the norm, so the sensitivity of that exchange would be meaningless.
    """
    a = fixture_result.flux_left
    b = 1.05 * fixture_result.flux_right
    delta = a - b
    analytic = -float(np.dot(delta, b)) / (float(np.linalg.norm(a)) * float(np.linalg.norm(delta)))

    def gate(scale: float) -> float:
        return relative_residual(a, scale * b)

    assert 1e-2 < gate(1.0) < 0.2
    numeric = central_difference(gate, 1.0, step=1e-5)
    record = sensitivity_gate(analytic, numeric, rtol=0.02)
    assert record["passed"], record
    assert record["relative_error"] <= 0.02


def test_sensitivity_gate_rejects_a_wrong_derivative():
    record = sensitivity_gate(analytic=2.0, gate_value=2.5, rtol=0.02)
    assert not record["passed"]
    assert record["relative_error"] == pytest.approx(0.2, rel=1e-12)


# ---------------------------------------------------------------------------
# conservation-law contract (fail-closed)
# ---------------------------------------------------------------------------


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_schema_and_example_are_present_and_json():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"].startswith("http://json-schema.org")
    assert "interface_operator" in schema["properties"]
    assert validate_contract(_example()) == []


def test_example_contract_loads_from_disk():
    contract = load_contract(EXAMPLE)
    assert contract["law_id"] == "interface.flux.quadratic.v1"
    assert contract["schema_version"] == "conservation-law.v1"


def test_contract_rejects_missing_required_key():
    contract = _example()
    contract.pop("residual")
    errors = validate_contract(contract)
    assert any("missing required key: residual" in e for e in errors)


def test_contract_rejects_unknown_key():
    contract = _example()
    contract["tolerance_hack"] = 1.0
    assert any("unknown keys" in e for e in validate_contract(contract))


def test_contract_rejects_unknown_operator_kind():
    contract = _example()
    contract["interface_operator"]["kind"] = "magic_transfer"
    assert any("unknown interface operator" in e for e in validate_contract(contract))


@pytest.mark.parametrize(
    "patch",
    [
        {"residual": {"relative_tolerance": 0.0}},
        {"residual": {"relative_tolerance": 5.0}},
        {"refinement_gate": {"levels": [8, 16], "min_observed_order": 1.0}},
        {"refinement_gate": {"levels": [8, 16, 4], "min_observed_order": 1.0}},
        {"loop_gate": {"scheme": "jacobi", "tolerance": 0.0, "max_iterations": 10}},
        {"loop_gate": {"scheme": "sor", "tolerance": 1e-6, "max_iterations": 10}},
        {"schema_version": "conservation-law.v0"},
        {"law_id": "BAD ID"},
    ],
)
def test_contract_rejections_are_fail_closed(patch):
    contract = _example()
    contract.update(patch)
    errors = validate_contract(contract)
    assert errors, patch


def test_load_contract_raises_on_invalid_document(tmp_path):
    bad = _example()
    bad["refinement_gate"]["min_observed_order"] = -1.0
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(path)


def test_contract_gate_passes_and_fails_on_the_same_exchange(fixture_result):
    contract = _example()
    contract["residual"]["relative_tolerance"] = 1e-4
    passed = contract_gate(contract, fixture_result.flux_left, fixture_result.flux_right)
    assert passed["passed"] and passed["residual"] <= 1e-4

    failed = contract_gate(
        contract, fixture_result.flux_left,
        jitter(fixture_result.flux_right, amplitude=0.3, seed=5),
    )
    assert not failed["passed"]
    assert failed["residual"] > 1e-4


def test_contract_gate_uses_the_declared_norm(fixture_result):
    contract = _example()
    contract["norm"] = "linf_relative"
    record = contract_gate(contract, fixture_result.flux_left, fixture_result.flux_right)
    assert record["norm"] == "linf_relative"
    assert record["residual"] >= contract_gate(
        {**contract, "norm": "l2_relative"}, fixture_result.flux_left, fixture_result.flux_right
    )["residual"] * 0.999


# ---------------------------------------------------------------------------
# platform neutrality (criterion g)
# ---------------------------------------------------------------------------


def test_new_files_contain_no_domain_vocabulary():
    """Criterion (g): the zero-domain-vocabulary scan must return zero hits."""
    targets = [
        "tools/coupling/conservation_check.py",
        "tools/coupled_loop.py",
        "tests/test_conservation_check.py",
        "tests/test_coupled_loop.py",
        "contracts/conservation_law.schema.json",
        "contracts/conservation_law.example.v1.json",
    ]
    result = scan_paths(targets, lexicon=load_lexicon(), root=ROOT)
    assert result["scanned_files"] >= len(targets) - 1
    assert result["hits"] == [], result["hits"]