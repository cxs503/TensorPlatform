"""
Regression tests for verifier reference-value integrity.

These tests exist because of a concrete failure mode found on 2026-11-01:
the question bank's Holtrop-Mennen "form factor" reference values were produced
by a formula that mirrored the pilot implementation (including a `* 0.1`
"scaled for pilot" factor).  The verifier therefore accepted a physically
impossible implementation 15/15 and *rejected* a correct one.  A verifier that
cannot be falsified is worse than no verifier: it deletes good work.

Each test below is a falsification gate: it must fail if the defect is
reintroduced.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(HERE))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from verifier import reference_audit as ra  # noqa: E402

QBANK_DIR = os.path.join(TOOLS_DIR, "verifier", "question_bank")
BANK = os.path.join(QBANK_DIR, "questions.db")
BANK_V1_FALSIFIED = os.path.join(QBANK_DIR, "questions_v1_falsified.db")


# ---------------------------------------------------------------------------
# The defect itself, frozen in code so it can never quietly come back
# ---------------------------------------------------------------------------

def legacy_form_factor(Cb: float, L: float, B: float, T: float) -> float:
    """The falsified implementation (kept ONLY as a test fixture)."""
    term1 = 0.93
    term2 = 0.487118 * (Cb ** (-0.9249))
    term3 = (L / B) ** 0.15
    term4 = (B / T) ** 0.2
    return term1 + term2 * term3 * term4 * 0.1


def legacy_fixed_geometry(Cb: float, **kwargs) -> float:
    """Legacy formula on fixed L/B and B/T, sweeping only Cb.

    The legacy expression has no lcb/Cstern/Cp inputs at all, so sweeping Cb at
    fixed geometry is the only comparison it admits.
    """
    return legacy_form_factor(Cb, 150.0, 22.0, 8.5)


def test_legacy_form_factor_has_inverted_cb_trend():
    """A fuller hull must show MORE form drag; the legacy formula shows less.

    Holding L/B and B/T fixed and varying Cb is a comparison the physics decides
    unambiguously, and it is also a comparison the legacy expression is blind to
    in the right direction: it drops Cp, lcb and Cstern entirely.
    """
    legacy = [legacy_form_factor(cb, 150.0, 22.0, 8.5) for cb in (0.55, 0.70, 0.85)]
    corrected = [ra.holtrop_1982_form_factor(cb, 150.0, 22.0, 8.5, lcb=2.0)
                 for cb in (0.55, 0.70, 0.85)]
    assert all(b > a for a, b in zip(corrected, corrected[1:])), "corrected must increase"
    assert all(b < a for a, b in zip(legacy, legacy[1:])), "legacy must (wrongly) decrease"
    # the knock-on effect on the shipped references is large, not cosmetic
    assert abs(legacy[-1] - corrected[-1]) / corrected[-1] > 0.10


def test_legacy_formula_fails_auditor_invariants():
    fails = ra.form_factor_invariants(legacy_fixed_geometry, [])
    joined = " | ".join(fails)
    assert "physically impossible" in joined or "< 1.0" in joined
    assert "not increasing with Cb" in joined


# ---------------------------------------------------------------------------
# The replacement must satisfy the invariants the old one violated
# ---------------------------------------------------------------------------

def test_holtrop_1982_satisfies_invariants():
    assert ra.form_factor_invariants(ra.holtrop_1982_form_factor, []) == []


def test_holtrop_1982_monotone_in_cb_and_cstern():
    cbs = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
    vals = [ra.holtrop_1982_form_factor(cb, 150.0, 22.0, 8.5, lcb=2.0) for cb in cbs]
    assert all(v >= 1.0 for v in vals)
    assert all(b > a for a, b in zip(vals, vals[1:]))
    # c13 = 1 + 0.93*Cstern must order the stern types
    f_m25 = ra.holtrop_1982_form_factor(0.70, 150.0, 22.0, 8.5, lcb=2.0, Cstern=-25.0)
    f_p10 = ra.holtrop_1982_form_factor(0.70, 150.0, 22.0, 8.5, lcb=2.0, Cstern=10.0)
    assert f_p10 > f_m25


def test_holtrop_1982_c12_branch_continuity():
    """c12 has two branches around T/L = 0.05; they must agree at the seam."""
    eps = 1e-9
    L = 100.0
    T_lo, T_hi = L * (0.05 - eps), L * (0.05 + eps)
    low = ra.holtrop_1982_form_factor(0.70, L, 15.0, T_lo, lcb=2.0)
    high = ra.holtrop_1982_form_factor(0.70, L, 15.0, T_hi, lcb=2.0)
    assert abs(low - high) / high < 1e-3, f"c12 branch discontinuity {low} vs {high}"


def test_holtrop_1982_rejects_garbage_inputs():
    with pytest.raises(ValueError):
        ra.holtrop_1982_form_factor(1.2, 100.0, 15.0, 6.0)
    with pytest.raises(ValueError):
        ra.holtrop_1982_form_factor(0.7, 100.0, 15.0, 1.0)  # T/L below validity


# ---------------------------------------------------------------------------
# Bank-level gates
# ---------------------------------------------------------------------------

def test_shipped_bank_passes_audit():
    report = ra.audit_bank(BANK)
    fails = [q for q in report["questions"] if q["verdict"] == "FAIL"]
    assert not fails, json.dumps(fails, indent=2)
    # references must agree with the independent recomputation to <1%
    for q in report["questions"]:
        for key, dev in q["rel_deviation_pct"].items():
            assert dev < 1.0, f"{q['title']} {key} deviates {dev:.3f}%"
    # analytic families are graded REAL, empirical reconstruction stays EMPIRICAL
    grades = {q["family"]: q["grade"] for q in report["questions"]}
    assert grades["ittc_1957"] == "REAL"
    assert grades["blasius_laminar"] == "REAL"
    assert grades["holtrop_mennen_form_factor"] == "EMPIRICAL"


def test_archived_v1_bank_is_falsified():
    if not os.path.exists(BANK_V1_FALSIFIED):
        pytest.skip("archived legacy bank not present")
    report = ra.audit_bank(BANK_V1_FALSIFIED)
    failed = [q for q in report["questions"] if q["verdict"] == "FAIL"]
    assert len(failed) == 3, [q["title"] for q in failed]
    assert all(q["family"] == "holtrop_mennen_form_factor" for q in failed)
    assert not report["ok"]
    # the deviation is large, i.e. not a rounding issue of the reference
    tanker = [q for q in failed if "Tanker" in q["title"]][0]
    assert tanker["rel_deviation_pct"]["1+k"] > 10.0, tanker["rel_deviation_pct"]


def test_mutation_is_detected():
    """The audit must not be a tautology: corrupt references and require FAILs."""
    assert ra.mutation_test(BANK)["ok"] is True


def test_tolerance_window_is_bounded():
    """Acceptance windows must stay tight enough to be meaningful."""
    report = ra.audit_bank(BANK)
    for q in report["questions"]:
        assert q["max_effective_acceptance_window_pct"] <= 5.0, q["title"]


# ---------------------------------------------------------------------------
# Analytic families: independent path must reproduce the bank exactly
# ---------------------------------------------------------------------------

def test_ittc57_matches_decimal_reference():
    # published anchor values of the ITTC-1957 line
    anchors = {1e7: 0.003, 1e8: 0.0020833333333333333, 1e9: 0.001530612244897959}
    for re, cf in anchors.items():
        assert ra.ittc57_cf(re) == pytest.approx(cf, rel=1e-12)


def test_blasius_matches_boundary_layer_theory():
    assert ra.blasius_cf(1e5) == pytest.approx(1.328 / math.sqrt(1e5), rel=1e-12)
    assert ra.blasius_cf(1e6) == pytest.approx(0.001328, rel=1e-12)


def test_prandtl_schlichting_monotone_and_bounded():
    fails = ra._cf_family_invariants("prandtl_schlichting", ra.prandtl_schlichting_cf)
    assert fails == []