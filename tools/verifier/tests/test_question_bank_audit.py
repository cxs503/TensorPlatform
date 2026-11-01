#!/usr/bin/env python3
"""
Tests for the question-bank audit (verify the verifier's ground truth).

These tests are a regression gate on the *reference data*, not on the code that
consumes it.  They encode the findings of docs/verifier/question_bank_audit.md:

  * the 12 flat-plate / ITTC-1957 references reproduce the cited primary sources
    to round-off — they must never drift;
  * the 3 Holtrop & Mennen form-factor references are circular (they re-evaluate
    the candidate's own fabricated formula) and must stay flagged until they are
    superseded by the version-2 set in artifacts/question_bank_corrections_v2.json;
  * the auditor must never write to the bank (read-only) and must catch the
    permission-isolation regression;
  * the literature reference itself must satisfy the physical trend
    d(1+k)/dCb > 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.verifier import reference_impl as ref  # noqa: E402
from tools.verifier.audit_question_bank import (  # noqa: E402
    DEFAULT_DB,
    build_corrections,
    check_permission_isolation,
    run_audit,
)

ROUND_OFF = 1e-9
PLATFORM_PRECISION = 0.03


@pytest.fixture(scope="module")
def audit():
    return run_audit(DEFAULT_DB)


# --------------------------------------------------------------------------- #
# Reference implementations vs the published sources
# --------------------------------------------------------------------------- #
def test_blasius_matches_closed_form():
    for re_l in (1e5, 5e5, 1e6):
        assert ref.blasius_laminar_cf(re_l) == pytest.approx(1.328 / re_l ** 0.5, rel=1e-12)


def test_prandtl_schlichting_matches_closed_form():
    for re_l in (1e6, 1e7, 1e8, 1e9):
        expected = 0.455 / (__import__("math").log10(re_l) ** 2.58)
        assert ref.prandtl_schlichting_cf(re_l) == pytest.approx(expected, rel=1e-12)


def test_ittc1957_matches_closed_form():
    for re_l in (1e6, 1e7, 5e6):
        expected = 0.075 / (__import__("math").log10(re_l) - 2.0) ** 2
        assert ref.ittc_1957_cf(re_l) == pytest.approx(expected, rel=1e-12)


def test_holtrop_form_factor_hand_computed_value():
    """Independent hand calculation for the Series-60 case (Cb=0.6, L=100, B=15, T=6).

    1+k = 0.93 + 0.487118*(B/L)^1.06806*(T/L)^0.46106*(L/LR)^0.121563
               *(L^3/nabla)^0.36486*(1-Cp)^-0.604247   with Cp=Cb/0.98, LR=L(1-Cp)
    """
    Cb, L, B, T = 0.6, 100.0, 15.0, 6.0
    Cp = Cb / 0.98
    nabla = Cb * L * B * T
    LR = L * (1.0 - Cp)
    expected = (
        0.93
        + 0.487118
        * (B / L) ** 1.06806
        * (T / L) ** 0.46106
        * (L / LR) ** 0.121563
        * (L ** 3 / nabla) ** 0.36486
        * (1.0 - Cp) ** (-0.604247)
    )
    got = ref.holtrop_mennen_form_factor(Cb, L, B, T)["1+k"]
    assert got == pytest.approx(expected, rel=1e-12)
    # documented value used throughout the report
    assert got == pytest.approx(1.1646, abs=5e-4)


def test_holtrop_form_factor_increases_with_fullness():
    """Physical trend: fuller hull -> larger form factor (Holtrop database)."""
    values = [ref.holtrop_mennen_form_factor(cb, 150.0, 22.0, 8.5)["1+k"]
              for cb in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)]
    assert all(b > a for a, b in zip(values, values[1:])), values
    assert 1.0 < values[0] < values[-1] < 1.6


def test_holtrop_band_brackets_nominal():
    band = ref.holtrop_mennen_form_factor_band(0.7, 150.0, 22.0, 8.5)
    assert band["min"] < band["nominal"] < band["max"]


# --------------------------------------------------------------------------- #
# The bank: 12 sound references, 3 circular ones
# --------------------------------------------------------------------------- #
def test_flat_plate_and_ittc_references_confirmed(audit):
    good = [q for q in audit["questions"]
            if q["formula"] in ("blasius_laminar", "prandtl_schlichting", "ittc_1957")]
    assert len(good) == 12
    for q in good:
        assert q["verdict"] == "CONFIRMED", (q["title"], q["verdict"])
        assert q["max_deviation"] <= ROUND_OFF, (q["title"], q["max_deviation"])


def test_holtrop_references_are_circular_and_outside_band(audit):
    bad = [q for q in audit["questions"] if q["formula"] == "holtrop_mennen_form_factor"]
    assert len(bad) == 3
    for q in bad:
        assert q["verdict"] == "CIRCULAR_REFERENCE", (q["title"], q["verdict"])
        # disagreement exceeds the platform-wide 3% acceptability standard
        assert q["max_deviation"] > PLATFORM_PRECISION, (q["title"], q["max_deviation"])
        # robust: no admissible omitted input can reproduce the stored value
        band = q["sensitivity_band"]
        stored = q["stored"]["1+k"]
        assert not (band["min"] <= stored <= band["max"]), (q["title"], stored, band)


def test_stored_form_factor_trend_is_inverted(audit):
    trend = audit["bank_checks"]["physical_trend"]
    assert trend["ok"] is False
    series = sorted(trend["series"], key=lambda s: s["Cb"])
    bank = [s["bank_1pk"] for s in series]
    indep = [s["independent_1pk"] for s in series]
    assert all(b < a for a, b in zip(bank, bank[1:])), bank       # decreasing: wrong
    assert all(b > a for a, b in zip(indep, indep[1:])), indep   # increasing: right


def test_commit_hash_integrity_holds(audit):
    assert audit["bank_checks"]["commit_hash_integrity"]["ok"] is True


def test_audit_overall_not_ok_while_defect_present(audit):
    assert audit["ok"] is False
    assert "CIRCULAR_REFERENCE" in audit["verdict_counts"]


# --------------------------------------------------------------------------- #
# Auditor hygiene
# --------------------------------------------------------------------------- #
def _file_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_audit_is_read_only():
    before = _file_hash(DEFAULT_DB)
    run_audit(DEFAULT_DB)
    assert _file_hash(DEFAULT_DB) == before, "audit must not modify the question bank"


def test_permission_leak_is_detected():
    tmpdir = tempfile.mkdtemp()
    try:
        copy = os.path.join(tmpdir, "questions.db")
        shutil.copyfile(DEFAULT_DB, copy)
        os.chmod(copy, 0o644)
        chk = check_permission_isolation(copy)
        assert chk["ok"] is False
        assert chk["observed_mode"] == "0o644"
        os.chmod(copy, 0o600)
        assert check_permission_isolation(copy)["ok"] is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Corrections payload
# --------------------------------------------------------------------------- #
def test_corrections_payload_is_complete(audit):
    corr = build_corrections(audit)
    assert corr["n_corrections"] == 3
    for c in corr["corrections"]:
        assert c["old_deviation_vs_literature"] > PLATFORM_PRECISION
        assert c["new_expected"]["1+k"] == pytest.approx(
            ref.holtrop_mennen_form_factor(
                c["specification"]["Cb"], c["specification"]["L"],
                c["specification"]["B"], c["specification"]["T"])["1+k"], rel=1e-12)
        assert "Holtrop" in c["provenance"]["source"]
        assert c["provenance"]["sensitivity_band"]["min"] <= c["new_expected"]["1+k"] <= \
            c["provenance"]["sensitivity_band"]["max"]