"""Regression tests for the Wageningen B-series reference and its benchmark harness.

The reference (``backend/services/wageningen_b_series.py``) is the *judge* of the
platform's open-water kernels, so the judge itself has to be adjudicated: these
tests pin the coefficient tables, the published validity domain, and a set of
physics anchors that any faithful transcription of Oosterveld & van Oossanen
(1975) must reproduce.  A second group of tests guards the benchmark artifact
(``artifacts/prop_openwater/bench.json``) against silent drift.
"""
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import wageningen_b_series as bs  # noqa: E402


# ---------------------------------------------------------------------------
# 1. coefficient-table integrity
# ---------------------------------------------------------------------------
def test_term_counts_match_published_regression():
    """O&O (1975): 39 terms for KT, 47 terms for KQ."""
    assert len(bs.KT_COEFFICIENTS) == 39
    assert len(bs.KQ_COEFFICIENTS) == 47


def test_coefficient_fingerprint():
    """Spot-pin the first terms so an accidental edit cannot pass unnoticed."""
    assert bs.KT_COEFFICIENTS[0] == (0.00880496, 0, 0, 0, 0)
    assert bs.KT_COEFFICIENTS[1] == (-0.204554, 1, 0, 0, 0)
    assert bs.KT_COEFFICIENTS[2] == (0.166351, 0, 1, 0, 0)
    assert bs.KQ_COEFFICIENTS[0] == (0.00379368, 0, 0, 0, 0)
    assert bs.KQ_COEFFICIENTS[1] == (0.00886523, 2, 0, 0, 0)
    assert bs.KQ_COEFFICIENTS[2] == (-0.032241, 1, 1, 0, 0)


def test_exponent_structure_is_sane():
    """Exponents are small non-negative integers; Z never dominates (v <= 2)."""
    for table in (bs.KT_COEFFICIENTS, bs.KQ_COEFFICIENTS):
        for coeff, s, t, u, v in table:
            assert isinstance(s, int) and 0 <= s <= 3
            assert isinstance(t, int) and 0 <= t <= 6
            assert isinstance(u, int) and 0 <= u <= 3
            assert isinstance(v, int) and 0 <= v <= 2
            assert abs(coeff) < 1.0
    # the tables must not be permutations of each other (KQ != KT scaled)
    assert {c[1:] for c in bs.KT_COEFFICIENTS} != {c[1:] for c in bs.KQ_COEFFICIENTS}


# ---------------------------------------------------------------------------
# 2. physics anchors of the regression itself (independent of any kernel)
# ---------------------------------------------------------------------------
def test_bollard_pull_thrust_anchor():
    """KT(J=0) for Z=4, Ae/Ao=0.70 over P/D=0.6..1.4: 0.2500 -> 0.6244."""
    values = [bs.kt(0.0, pd, 0.70, 4) for pd in (0.6, 0.8, 1.0, 1.2, 1.4)]
    assert values[0] == pytest.approx(0.2500, abs=5e-3)
    assert values[2] == pytest.approx(0.4547, abs=5e-3)
    assert values[-1] == pytest.approx(0.6244, abs=5e-3)
    assert values == sorted(values)  # more pitch => more thrust


def test_zero_thrust_ratio_tracks_geometric_pitch():
    """J0/(P/D) must sit near 1.06-1.08 for the whole B-series pitch range."""
    for pd in (0.6, 0.8, 1.0, 1.2, 1.4):
        j0 = bs.zero_thrust_advance_ratio(pd, 0.70, 4)
        assert 1.0 <= j0 / pd <= 1.15, (pd, j0)


def test_parasitic_torque_at_zero_thrust():
    """At J0 the thrust vanishes but viscous torque must not."""
    j0 = bs.zero_thrust_advance_ratio(1.0, 0.70, 4)
    assert abs(bs.kt(j0, 1.0, 0.70, 4)) < 1e-9
    assert 10.0 * bs.kq(j0, 1.0, 0.70, 4) > 0.05


def test_efficiency_peak_envelope():
    """B-series peak efficiency rises with P/D and stays inside 0.50-0.78."""
    peaks = [bs.best_efficiency_point(pd, 0.70, 4).eta0 for pd in (0.6, 1.0, 1.4)]
    assert 0.50 <= peaks[0] <= 0.58
    assert 0.66 <= peaks[1] <= 0.73
    assert 0.73 <= peaks[2] <= 0.78
    assert peaks == sorted(peaks)


def test_torque_is_not_a_fixed_multiple_of_thrust():
    """Guard against the MOCK degeneracy KQ == KT/10 seen in the legacy surrogate."""
    ratios = []
    for j in (0.2, 0.4, 0.6, 0.8):
        ratios.append(10.0 * bs.kq(j, 1.0, 0.70, 4) / bs.kt(j, 1.0, 0.70, 4))
    assert max(ratios) - min(ratios) > 0.25
    assert all(0.5 < r < 4.0 for r in ratios)


def test_kt_monotone_in_j_and_pitch():
    for pd in (0.6, 1.0, 1.4):
        kts = [bs.kt(j, pd, 0.70, 4) for j in (0.0, 0.2, 0.4, 0.6)]
        assert kts == sorted(kts, reverse=True)
    for j in (0.2, 0.5):
        assert bs.kt(j, 0.8, 0.70, 4) < bs.kt(j, 1.0, 0.70, 4) < bs.kt(j, 1.2, 0.70, 4)


def test_efficiency_closed_identity():
    for j in (0.2, 0.5, 0.8):
        kt = bs.kt(j, 1.0, 0.70, 4)
        kq = bs.kq(j, 1.0, 0.70, 4)
        assert bs.eta0(j, 1.0, 0.70, 4) == pytest.approx(j * kt / (2 * math.pi * kq), rel=1e-12)


def test_domain_gate():
    bs.check_domain(0.4, 1.0, 0.70, 4)  # inside: no raise
    problems = bs.check_domain(0.4, 1.6, 0.70, 4, strict=False)
    assert problems and "P/D" in problems[0]
    with pytest.raises(bs.DomainError):
        bs.kt(0.4, 1.6, 0.70, 4)
    with pytest.raises(bs.DomainError):
        bs.kq(0.4, 1.0, 0.70, 8)
    with pytest.raises(bs.DomainError):
        bs.kq(0.4, 1.0, 0.10, 4)


def test_froude_scale_invariance_of_coefficients():
    """KT/KQ/eta0 are non-dimensional: identical for any (D, n, V) with the same J."""
    geom_like = [bs.OpenWaterPoint(0.5, 1.0, 0.70, 4,
                                   bs.kt(0.5, 1.0, 0.70, 4),
                                   bs.kq(0.5, 1.0, 0.70, 4),
                                   bs.eta0(0.5, 1.0, 0.70, 4)) for _ in range(2)]
    assert geom_like[0].as_dict() == geom_like[1].as_dict()


# ---------------------------------------------------------------------------
# 3. benchmark artifact: literature point-value validation
# ---------------------------------------------------------------------------
BENCH_JSON = os.path.join(ROOT, "artifacts", "prop_openwater", "bench.json")


def _load_bench():
    """Load bench.json; skip tests gracefully if missing."""
    assert os.path.exists(BENCH_JSON), f"benchmark artifact not found at {BENCH_JSON}"
    with open(BENCH_JSON) as f:
        return json.load(f)


def test_benchmark_artifact_exists_and_has_minimum_coverage():
    """C1: bench.json exists, >= 3 blade types x >= 5 J points, each with source."""
    data = _load_bench()
    assert "points" in data
    assert data["n_points"] >= 15  # 3 blades x 5 J points minimum
    # Check at least 3 distinct blade types
    blade_types = set(p["blade_type"] for p in data["points"])
    assert len(blade_types) >= 3, f"Need >= 3 blade types, got {blade_types}"
    # Check each blade type has >= 5 J points
    for bt in blade_types:
        j_vals = [p["J"] for p in data["points"] if p["blade_type"] == bt]
        assert len(j_vals) >= 5, f"Blade {bt} has only {len(j_vals)} J points"
    # Check source annotation exists
    assert "reference" in data or "source_note" in data


def test_benchmark_points_reproduce_regression():
    """Every point in bench.json must match the B-series regression to machine precision."""
    data = _load_bench()
    for p in data["points"]:
        z = p["Z"]
        ae_ao = p["Ae_Ao"]
        p_d = p["P_D"]
        j = p["J"]
        kt_ref = bs.kt(j, p_d, ae_ao, z, strict=False)
        kq_ref = bs.kq(j, p_d, ae_ao, z, strict=False)
        eta_ref = bs.eta0(j, p_d, ae_ao, z, strict=False)
        assert p["KT"] == pytest.approx(kt_ref, rel=1e-9), \
            f"KT mismatch at {p['blade_type']} J={j}: {p['KT']} vs {kt_ref}"
        assert p["KQ"] == pytest.approx(kq_ref, rel=1e-9), \
            f"KQ mismatch at {p['blade_type']} J={j}: {p['KQ']} vs {kq_ref}"
        assert p["eta0"] == pytest.approx(eta_ref, rel=1e-9), \
            f"eta0 mismatch at {p['blade_type']} J={j}: {p['eta0']} vs {eta_ref}"


def test_benchmark_eta0_physical_bound():
    """C5: eta0 must satisfy 0 < eta0 < eta_ideal = 2/(1+sqrt(1+C_T)) for all J>0 points."""
    data = _load_bench()
    violations = []
    for p in data["points"]:
        if p["J"] <= 0 or p["KT"] <= 0:
            continue
        ct = 8.0 * p["KT"] / (math.pi * p["J"] ** 2)
        eta_ideal = 2.0 / (1.0 + math.sqrt(1.0 + ct))
        if not (0 < p["eta0"] < eta_ideal):
            violations.append(
                f"{p['blade_type']} J={p['J']}: eta0={p['eta0']:.4f}, "
                f"eta_ideal={eta_ideal:.4f}")
    assert len(violations) == 0, f"eta0 physical bound violations: {violations}"


def test_benchmark_kt_monotone_decreasing():
    """KT must decrease monotonically with J for each blade type."""
    data = _load_bench()
    blade_types = set(p["blade_type"] for p in data["points"])
    for bt in blade_types:
        pts = sorted(
            [p for p in data["points"] if p["blade_type"] == bt],
            key=lambda x: x["J"])
        for i in range(1, len(pts)):
            assert pts[i]["KT"] <= pts[i-1]["KT"] + 1e-9, \
                f"KT not monotone for {bt} at J={pts[i]['J']}: " \
                f"{pts[i-1]['KT']} -> {pts[i]['KT']}"


def test_benchmark_eta0_positive_for_j_gt_zero():
    """eta0 must be positive for all J > 0 points (where KT > 0)."""
    data = _load_bench()
    for p in data["points"]:
        if p["J"] > 0 and p["KT"] > 0:
            assert p["eta0"] > 0, \
                f"eta0 should be positive for {p['blade_type']} J={p['J']}"


def test_benchmark_j0_consistency():
    """J0 stored in bench.json must match the regression's zero-thrust advance ratio."""
    data = _load_bench()
    checked = set()
    for p in data["points"]:
        key = (p["blade_type"], p["Z"], p["Ae_Ao"], p["P_D"])
        if key in checked:
            continue
        checked.add(key)
        j0_ref = bs.zero_thrust_advance_ratio(p["P_D"], p["Ae_Ao"], p["Z"])
        assert p["J0"] == pytest.approx(j0_ref, rel=1e-5), \
            f"J0 mismatch for {p['blade_type']}: {p['J0']} vs {j0_ref}"


def test_benchmark_error_vs_served_kernel():
    """C3: Quantify KT/KQ/eta0 error of the served (MOCK) kernel vs bench.json."""
    from api.domain.propeller_open_water import serve_kernel_replay
    data = _load_bench()
    kt_errs, kq_errs, eta_errs = [], [], []
    for p in data["points"]:
        if p["J"] <= 0:
            continue
        kt_s, kq_s, eta_s = serve_kernel_replay(
            p["J"], p["P_D"], p["Z"], p["Ae_Ao"])
        kt_ref, kq_ref, eta_ref = p["KT"], p["KQ"], p["eta0"]
        if kt_ref > 1e-6:
            kt_errs.append(abs(kt_s - kt_ref) / kt_ref)
        if kq_ref > 1e-6:
            kq_errs.append(abs(kq_s - kq_ref) / kq_ref)
        if eta_ref > 1e-6:
            eta_errs.append(abs(eta_s - eta_ref) / eta_ref)
    # The served kernel is MOCK; errors should be large (>3%)
    assert max(kt_errs) > 0.03, f"KT error too small: {max(kt_errs):.4f}"
    assert max(kq_errs) > 0.03, f"KQ error too small: {max(kq_errs):.4f}"
    # Report the errors
    print(f"\nServed kernel vs B-series benchmark errors:")
    print(f"  KT: max={max(kt_errs):.4f}, mean={sum(kt_errs)/len(kt_errs):.4f}")
    print(f"  KQ: max={max(kq_errs):.4f}, mean={sum(kq_errs)/len(kq_errs):.4f}")
    print(f"  eta0: max={max(eta_errs):.4f}, mean={sum(eta_errs)/len(eta_errs):.4f}")


def test_benchmark_error_vs_bseries_kernel():
    """The B-series kernel itself should match bench.json to < 0.01% (self-consistency)."""
    data = _load_bench()
    kt_errs, kq_errs, eta_errs = [], [], []
    for p in data["points"]:
        kt_ref = bs.kt(p["J"], p["P_D"], p["Ae_Ao"], p["Z"], strict=False)
        kq_ref = bs.kq(p["J"], p["P_D"], p["Ae_Ao"], p["Z"], strict=False)
        eta_ref = bs.eta0(p["J"], p["P_D"], p["Ae_Ao"], p["Z"], strict=False)
        if p["KT"] > 1e-6:
            kt_errs.append(abs(kt_ref - p["KT"]) / p["KT"])
        if p["KQ"] > 1e-6:
            kq_errs.append(abs(kq_ref - p["KQ"]) / p["KQ"])
        if p["eta0"] > 1e-6:
            eta_errs.append(abs(eta_ref - p["eta0"]) / p["eta0"])
    # Self-consistency: errors should be at rounding level
    assert max(kt_errs) < 1e-9, f"KT self-consistency error: {max(kt_errs)}"
    assert max(kq_errs) < 1e-9, f"KQ self-consistency error: {max(kq_errs)}"
    assert max(eta_errs) < 1e-9, f"eta0 self-consistency error: {max(eta_errs)}"


def test_benchmark_artifact_is_self_consistent():
    """Points must reproduce their own aggregate statistics (replaces old test)."""
    data = _load_bench()
    # Verify n_points matches actual count
    assert data["n_points"] == len(data["points"])
    # Verify blade_types metadata matches actual points
    for bt_info in data["blade_types"]:
        label = bt_info["label"]
        pts = [p for p in data["points"] if p["blade_type"] == label]
        assert len(pts) > 0, f"No points for blade type {label}"
        for p in pts:
            assert p["Z"] == bt_info["Z"]
            assert p["Ae_Ao"] == bt_info["Ae_Ao"]
            assert p["P_D"] == bt_info["P_D"]
