"""Regression tests for the ``slitvinov/lammps-sph-multiphase`` deep dive.

The deep dive may only claim what it can re-compute, so the checks below pin the
machinery that turns C++ source text into numbers:

* the kernel parser really extracts the piecewise polynomials and constants
  (run against a synthetic header, so it never needs the network);
* the normalisation integral of the quintic spline equals 1 in 2D and 3D;
* the derivative table and the Tait-EOS slope identity are exact;
* when the pinned source cache exists, *all* evidence pointers resolve and the
  negative claims hold zero hits (no GPU/OpenMP/delta-SPH in the added sources).
"""
from __future__ import annotations

import math

import pytest

from tools.oss_inventory import lammps_sph_multiphase_inventory as inv

SYNTHETIC_HEADER = """\
namespace LAMMPS_NS {
namespace SPHMultiphase {
  static constexpr double norm3d = 0.0716197243913529;
  static constexpr double norm2d = 0.04195297663091802;

  inline double kernel_poly(double s)
  {
    if (s < 1.0) return pow(3 - s, 5) - 6 * pow(2 - s, 5) + 15 * pow(1 - s, 5);
    if (s < 2.0) return pow(3 - s, 5) - 6 * pow(2 - s, 5);
    if (s < 3.0) return pow(3 - s, 5);
    return 0.0;
  }

  inline double dkernel_poly(double s)
  {
    if (s < 1.0) return -50 * pow(s, 4) + 120 * pow(s, 3) - 120 * s;
    if (s < 2.0) return 25 * pow(s, 4) - 180 * pow(s, 3) + 450 * pow(s, 2) - 420 * s + 75;
    if (s < 3.0) return -5 * pow(s, 4) + 60 * pow(s, 3) - 270 * pow(s, 2) + 540 * s - 405;
    return 0.0;
  }

  inline double w(int dim, double r) { return 0.0; }
}
}
"""


def test_parse_kernel_extracts_constants_and_branches():
    spec = inv.parse_kernel(SYNTHETIC_HEADER)
    assert spec["norm3d"] == pytest.approx(0.0716197243913529)
    assert spec["norm2d"] == pytest.approx(0.04195297663091802)
    assert [b[0] for b in spec["w_branches"]] == [1.0, 2.0, 3.0]
    assert [b[0] for b in spec["dw_branches"]] == [1.0, 2.0, 3.0]


def test_cpp_expression_translation():
    assert inv.cpp_expr_to_py("pow(3 - s, 5) - 6") == "((3 - s)**(5)) - 6"
    assert inv.cpp_expr_to_py("-50 * pow(s, 4)") == "-50 * ((s)**(4))"


def test_kernel_normalisation_is_unity_in_2d_and_3d():
    spec = inv.parse_kernel(SYNTHETIC_HEADER)
    ver = inv.verify_kernel_normalisation(spec)
    assert ver["rel_err_3d"] < 1e-12
    assert ver["rel_err_2d"] < 1e-12


def test_norm_constants_match_analytic_quintic_spline():
    spec = inv.parse_kernel(SYNTHETIC_HEADER)
    assert spec["norm3d"] == pytest.approx(9.0 / (40.0 * math.pi), rel=1e-15)
    assert spec["norm2d"] == pytest.approx(63.0 / (478.0 * math.pi), rel=1e-15)


def test_derivative_table_is_the_symbolic_derivative():
    spec = inv.parse_kernel(SYNTHETIC_HEADER)
    ver = inv.verify_derivative_table(spec)
    assert ver["symbolic_zero"] is True
    assert ver["max_abs_pointwise_err"] < 1e-12


def test_tait_eos_slope_equals_sound_speed_squared():
    ver = inv.verify_eos_slope(soundspeed=1.5, rho0=0.01, gamma=1.4)
    assert ver["rel_err"] < 1e-12
    assert ver["rel_err_fd_vs_analytic"] < 1e-6
    # With rbackground == 0 the Tait EOS carries a background pressure:
    # p(rho0) == B, i.e. the wall that has rho set explicitly also carries B.
    assert ver["p_at_rho0"] == pytest.approx(ver["B"], rel=1e-12)


def test_phase_change_energy_identity():
    ver = inv.energy_identity_check()
    assert ver["rel_err"] < 1e-12
    assert ver["delta_E"] == pytest.approx(ver["expected_delta_E"], rel=1e-12)


def test_neighbour_support_matches_hand_count():
    sup = inv.neighbour_support()
    # 2h is a subset of the 3h cut-off, so the counts must be strictly nested.
    assert sup["n_within_2h"] < sup["n_within_cut3h"]
    assert sup["n_within_2h"] == 32 and sup["n_within_cut3h"] == 92


@pytest.mark.skipif(not (inv.CACHE / "blob_index.json").exists(),
                    reason="pinned source cache absent (run the inventory module once)")
def test_all_evidence_pointers_resolve_on_pinned_tree():
    rows, by_key = inv.evidence_table()
    assert len(rows) == len(inv.EVIDENCE_SPECS)
    for row in rows:
        assert row["source_line"], row["key"]
        assert row["pointer"].endswith(f":{row['line']}")
    # phase-change energy bookkeeping pointers must hit executable lines
    assert by_key["pc_energy_update"]["line"] > 0
    assert "enew" in by_key["pc_energy_update"]["source_line"]


@pytest.mark.skipif(not (inv.CACHE / "blob_index.json").exists(),
                    reason="pinned source cache absent")
def test_negative_claims_hold_with_zero_hits():
    absent = inv.absence_evidence()
    for key in ("cuda_or_kokkos_or_gpu", "openmp", "delta_sph_density_diffusion",
                "isph_or_projection", "in_kernel_adaptive_dt"):
        assert absent[key]["n_hits"] == 0, (key, absent[key]["hits"])
    assert absent["ci_workflows"]["n_files"] == 0
    assert absent["test_files"]["matches"] == []
    assert absent["license_files"]["matches"] == []