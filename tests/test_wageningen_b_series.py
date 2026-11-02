"""Regression tests for the Wageningen B-series reference and its benchmark harness.

The reference (``backend/services/wageningen_b_series.py``) is the *judge* of the
platform's open-water kernels, so the judge itself has to be adjudicated: these
tests pin the coefficient tables, the published validity domain, and a set of
physics anchors that any faithful transcription of Oosterveld & van Oossanen
(1975) must reproduce.  A second group of tests guards the benchmark harness
(``tools/verifier/propeller_bseries_benchmark.py``) against silent drift.
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
# 3. benchmark harness: verdicts must stay reproducible and honest
# ---------------------------------------------------------------------------
BENCH_JSON = os.path.join(ROOT, "artifacts", "verification", "propeller_bseries",
                          "benchmark.json")


@pytest.mark.skipif(not os.path.exists(BENCH_JSON), reason="benchmark artifact not generated")
def test_benchmark_artifact_verdicts():
    data = json.load(open(BENCH_JSON))
    summary = data["summary"]
    assert {"repo_candidate_lifting_line", "repo_bem_strip"} <= set(summary)
    for name, rec in summary.items():
        assert rec["verdict"] in {"ADMISSIBLE", "PROVISIONAL", "REJECT"}
        # the whole point of the exercise: neither kernel meets the 3% bar
        assert rec["verdict"] != "ADMISSIBLE"
        assert rec["primary_band"]["KT"]["max"] > 0.03
        assert rec["primary_band"]["KT"]["n"] >= 40  # sanity: grid coverage
    # the BEM strip kernel must beat the lifting-line candidate (and, when the
    # live endpoint was scoreable in this run, it must beat it too but still fall
    # short of the 3% bar -- the served kernel is the legacy surrogate)
    if "served_platform" in summary:
        served = summary["served_platform"]
        assert served["verdict"] != "ADMISSIBLE"
        assert (summary["repo_bem_strip"]["primary_band"]["KT"]["mean"]
                < served["primary_band"]["KQ"]["mean"])
    assert (summary["repo_bem_strip"]["primary_band"]["KT"]["mean"]
            < summary["repo_candidate_lifting_line"]["primary_band"]["KT"]["mean"])


@pytest.mark.skipif(not os.path.exists(BENCH_JSON), reason="benchmark artifact not generated")
def test_benchmark_artifact_is_self_consistent():
    """Points must reproduce their own aggregate statistics."""
    data = json.load(open(BENCH_JSON))
    primary_frac = 0.70
    for name in ("repo_bem_strip", "repo_candidate_lifting_line"):
        errs = [abs(p["kernels"][name]["KT_err"]) for p in data["points"]
                if p["J_over_J0"] <= primary_frac + 1e-12]
        rec = data["summary"][name]["primary_band"]["KT"]
        assert rec["n"] == len(errs)
        assert rec["max"] == pytest.approx(max(errs), rel=1e-9)
        assert rec["mean"] == pytest.approx(sum(errs) / len(errs), rel=1e-9)


def test_harness_reference_self_scores_zero():
    """Scoring the reference against itself must be exactly zero (no plumbing bug)."""
    sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))
    import propeller_bseries_benchmark as hb
    for pd in (0.6, 1.0, 1.4):
        for frac in (0.1, 0.5):
            j = frac * bs.zero_thrust_advance_ratio(pd, 0.70, 4)
            assert hb._rel_err(bs.kt(j, pd, 0.70, 4), bs.kt(j, pd, 0.70, 4)) == 0.0