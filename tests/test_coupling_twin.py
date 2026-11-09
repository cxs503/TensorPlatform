"""Tests for the synthetic two-solver twin gate (tools/coupling/twin.py).

Acceptance mapping (node IN383-PLAT01):
  C4 twin end-to-end residual ≤ 1e-10
  C5 negative control (conservation correction off) drifts ≥ 1 %
  C6 clock contract violations fail closed inside a running coupled loop
  C7 identical spec ⇒ identical run digest
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.coupling import (
    ClockContractViolation,
    Field,
    ClockMode,
    RemapOperator,
    TwinSpec,
    canonical_digest,
    check,
    remap,
    run_twin,
)
from tools.coupling.twin import _meshes, diffusion_step


# ---------------------------------------------------------------------------
# The loop conserves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layout", ["nested", "shifted"])
def test_twin_loop_conserves_to_machine_precision(layout):
    result = run_twin(TwinSpec(cycles=40, layout=layout))
    assert result["max_residual"] <= 1e-10
    assert result["max_representation_gap"] <= 1e-10
    assert result["clock_violations"] == 0


def test_twin_roundtrip_on_nested_meshes_is_exact():
    result = run_twin(TwinSpec(cycles=5, layout="nested"))
    assert result["roundtrip_error"] <= 1e-12


def test_twin_with_rbf_and_correction_is_conservative():
    result = run_twin(TwinSpec(cycles=20, operator=RemapOperator.RBF_CONSERVATIVE,
                               apply_conservation_correction=True))
    assert result["max_residual"] <= 1e-10


def test_twin_diffusion_operator_alone_conserves_mass():
    """The loop's own solver step must conserve on a non-uniform mesh as well."""
    spec = TwinSpec(n_coarse=5, refinement=2, layout="shifted", cycles=1)
    coarse, fine = _meshes(spec)
    field = Field(np.linspace(0.0, 1.0, fine.n_cells) + 0.5, fine, "density", "u")
    grid = (spec.n_coarse * spec.refinement,) * spec.dim
    spacing = (1.0 / grid[0],) * spec.dim
    stepped = diffusion_step(field, grid, spacing, 0.01, 1e-3)
    assert stepped.total == pytest.approx(field.total, rel=1e-14)
    assert np.any(stepped.values != field.values)   # the update is not a no-op

    coarse_field = Field(np.linspace(0.0, 1.0, coarse.n_cells) + 0.5, coarse, "density", "v")
    coarse_grid = (spec.n_coarse,) * spec.dim
    coarse_spacing = (1.0 / spec.n_coarse,) * spec.dim
    stepped_c = diffusion_step(coarse_field, coarse_grid, coarse_spacing, 0.02, 1e-3)
    assert stepped_c.total == pytest.approx(coarse_field.total, rel=1e-14)


# ---------------------------------------------------------------------------
# C5: falsifiability — the gate must fire when the correction is removed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layout", ["nested", "shifted"])
def test_twin_negative_control_drifts_by_more_than_one_percent(layout):
    result = run_twin(TwinSpec(cycles=20, layout=layout,
                               operator=RemapOperator.NEAREST_SCALED,
                               apply_conservation_correction=False))
    assert result["max_residual"] >= 1e-2


def test_twin_rbf_without_correction_drifts():
    result = run_twin(TwinSpec(cycles=20, operator=RemapOperator.RBF_CONSERVATIVE,
                               apply_conservation_correction=False))
    assert result["max_residual"] >= 1e-4
    corrected = run_twin(TwinSpec(cycles=20, operator=RemapOperator.RBF_CONSERVATIVE,
                                  apply_conservation_correction=True))
    assert corrected["max_residual"] < result["max_residual"] / 1e3


# ---------------------------------------------------------------------------
# C6: clock semantics inside a running loop
# ---------------------------------------------------------------------------

def test_twin_sync_mode_answers_at_master_ticks():
    result = run_twin(TwinSpec(cycles=6, mode=ClockMode.SYNC))
    assert all(ex["clock_mode"] == "sync" for ex in result["exchanges"])
    assert not any(ex["extrapolated"] for ex in result["exchanges"])


def test_twin_linear_mode_is_used_when_requests_are_off_the_sub_grid():
    result = run_twin(TwinSpec(cycles=4, substeps=4, mode=ClockMode.LINEAR_INTERPOLATION,
                               clock_slack_steps=1))
    assert any(ex["interpolated"] for ex in result["exchanges"])
    assert result["max_residual"] <= 1e-10


def test_twin_extrapolation_beyond_the_horizon_fails_closed():
    with pytest.raises(ClockContractViolation):
        run_twin(TwinSpec(cycles=3, mode=ClockMode.ONE_STEP_EXTRAPOLATION,
                          max_extrapolation_steps=1, clock_slack_steps=3))


def test_twin_extrapolation_within_the_horizon_is_flagged():
    result = run_twin(TwinSpec(cycles=3, mode=ClockMode.ONE_STEP_EXTRAPOLATION,
                               max_extrapolation_steps=2, clock_slack_steps=3))
    assert result["clock_violations"] == 0
    assert result["max_residual"] <= 1e-10


def test_twin_clock_ledger_is_a_valid_chain():
    result = run_twin(TwinSpec(cycles=3))
    ledger = result["clock_ledger"]
    assert ledger["chain_valid"] is True
    assert ledger["n_entries"] == 1 + 2 * 3 + 3   # open + stores + requests


# ---------------------------------------------------------------------------
# C7 + contract validation
# ---------------------------------------------------------------------------

def test_twin_is_deterministic_and_digest_identifies_the_run():
    a = run_twin(TwinSpec(cycles=6))
    b = run_twin(TwinSpec(cycles=6))
    assert a["run_digest"] == b["run_digest"]
    assert a["residual_series"] == b["residual_series"]
    c = run_twin(TwinSpec(cycles=6, diffusivity=2e-3))
    assert c["run_digest"] != a["run_digest"]
    assert canonical_digest({"a": 1}) == canonical_digest({"a": 1})


@pytest.mark.parametrize("kwargs", [
    {"n_coarse": 0},
    {"refinement": 0},
    {"cycles": 0},
    {"dim": 4},
    {"layout": "spiral"},
    {"mode": "adaptive_magic"},
    {"operator": "spectral_magic"},
    {"substeps": 0},
])
def test_twin_spec_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        TwinSpec(**kwargs)


def test_twin_reports_initial_and_final_mass_for_the_audit_trail():
    result = run_twin(TwinSpec(cycles=5))
    assert result["mass_initial"]["fine"] > 0.0
    assert result["mass_final"]["fine"] == pytest.approx(
        result["mass_final"]["coarse"], rel=1e-10)
    assert set(result["spec"]) >= {"operator", "mode", "layout", "dt_master", "dt_sub", "substeps"}