#!/usr/bin/env python3
"""
Regression gate for literature-anchored verification (mission 1391, verifier seat).

What is pinned here
-------------------
1. The *reference* path (tools/verifier/reference_formulas.py) must reproduce
   values that are tabulated in the literature -- otherwise the reference itself
   is broken and every verdict derived from it is worthless.
2. The frozen question bank must agree with that independent reference
   (|dev| < 1e-12). If someone edits the generator to emit a different value,
   this fails.
3. Prandtl-Schlichting must agree with the independent Schoenherr line within
   3 % (a level check that does not share any constant with the candidate).
4. Known literature deviations of the pilot's form factor are recorded as
   ``xfail(strict=True)``: they document a live debt and turn into failures the
   moment the defect is fixed without updating the marker (so the debt cannot
   silently rot). The audit artifact carries the matching evidence.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFIER = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(VERIFIER))
for p in (VERIFIER, os.path.join(VERIFIER, "pilots"), REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

import reference_formulas as ref  # noqa: E402
import audit_literature_crosscheck as audit  # noqa: E402
import resistance_calculator as cand  # noqa: E402

ANI = audit.HULL_ASSUMPTIONS


# --------------------------------------------------------------------------- #
# 1. The reference itself must match published anchors
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", sorted(ref.PUBLISHED_ANCHORS))
def test_reference_matches_published_anchors(line):
    """Reference implementation must reproduce the literature table."""
    checks = {c["line"]: c for c in audit.anchor_check()}
    assert line in checks
    # all anchors of this line are covered by anchor_check(); re-check them here
    anchors = [a for a in audit.anchor_check() if a["line"] == line]
    assert anchors, f"no anchors registered for {line}"
    for a in anchors:
        assert a["pass"], (
            f"{line} at {a['input']}: reference={a['reference_impl']:.6g} "
            f"published={a['published']:.6g} dev={a['rel_dev_pct']:.3f}% "
            f"(allowed {a['tolerance_pct']:.3f}%)"
        )


def test_anchor_check_complete():
    """Every registered anchor passes -- no silent skips."""
    res = audit.anchor_check()
    assert len(res) == sum(len(v) for v in ref.PUBLISHED_ANCHORS.values())
    assert all(r["pass"] for r in res)


# --------------------------------------------------------------------------- #
# 2. Frozen question bank vs independent reference
# --------------------------------------------------------------------------- #
def test_bank_agrees_with_independent_reference_friction():
    """The 12 friction questions reproduce from the independent reference path.

    The 3 form-factor questions are excluded here on purpose: their stored
    expectation is the defective pilot value (see the xfail debt tests below).
    """
    questions = audit.load_questions()
    assert len(questions) == 15
    rows = [r for r in audit.bank_check(questions) if r["key"] == "Cf"]
    assert len(rows) == 12
    for row in rows:
        assert row["bank_vs_reference_rel_dev"] < 1e-12, (
            f"{row['title']}: bank={row['bank_expected']!r} "
            f"reference={row['independent_reference']!r} "
            f"dev={row['bank_vs_reference_rel_dev']:.3e}"
        )


@pytest.mark.xfail(
    strict=True,
    reason="known debt F2: bank expectation for the 3 form-factor questions is "
           "the pilot's ad-hoc value, not Holtrop & Mennen (1982)",
)
def test_bank_form_factor_expectations_match_literature():
    rows = [r for r in audit.bank_check(audit.load_questions()) if r["key"] == "1+k"]
    assert len(rows) == 3
    for row in rows:
        assert row["bank_vs_reference_rel_dev"] < 1e-12, row


def test_friction_questions_match_reference_exactly():
    """12 friction questions (ITTC-57 / Blasius / Prandtl-Schlichting) are exact."""
    friction = [r for r in audit.bank_check(audit.load_questions()) if r["key"] == "Cf"]
    assert len(friction) == 12
    for r in friction:
        assert r["candidate_vs_reference_rel_dev"] < 1e-12
        assert r["within_target_3pct"]


# --------------------------------------------------------------------------- #
# 3. Cross-formula level check (independent constant set)
# --------------------------------------------------------------------------- #
def test_prandtl_schlichting_vs_schoenherr_within_3pct():
    for row in audit.cross_formula_check():
        assert row["rel_dev_pct"] <= 3.0, row


def test_reference_module_is_independent_of_candidate():
    """Reference must not import / reuse the candidate implementation."""
    src = open(os.path.join(VERIFIER, "reference_formulas.py"), encoding="utf-8").read()
    assert "resistance_calculator" not in src
    assert "from .pilots" not in src and "import resistance" not in src
    # the ad-hoc fudge factor of the pilot must not leak into the reference
    assert "0.1  # Scaled" not in src


# --------------------------------------------------------------------------- #
# 4. Recorded debt: pilot form factor vs Holtrop & Mennen (1982)
#    strict xfail -> fails (XPASS) once the pilot is corrected, forcing cleanup
# --------------------------------------------------------------------------- #
def _form_factor_questions():
    return [
        q
        for q in audit.load_questions()
        if q["specification"].get("method") == "holtrop_mennen_form_factor"
    ]


@pytest.mark.xfail(
    strict=True,
    reason="known debt F2/F3: pilot form factor is an ad-hoc expression "
           "('scaled for pilot'), not Holtrop & Mennen (1982); see "
           "artifacts/m1391/literature_crosscheck.json",
)
def test_pilot_form_factor_matches_holtrop1982():
    rows = audit.form_factor_check(_form_factor_questions())
    assert len(rows) == 3
    for r in rows:
        assert r["rel_err_pct_on_1_plus_k"] <= audit.PLATFORM_ACCEPT_PCT, r
        assert r["rel_err_pct_on_k"] <= audit.PLATFORM_ACCEPT_PCT, r
        assert not r["pilot_outside_envelope"], r


@pytest.mark.xfail(
    strict=True,
    reason="known debt F3: pilot 1+k decreases with Cb (Cb^-0.9249) whereas "
           "Holtrop (1982) increases strongly with Cb",
)
def test_pilot_form_factor_cb_trend_matches_literature():
    rows = sorted(
        audit.form_factor_check(_form_factor_questions()),
        key=lambda r: r["geometry"]["Cb"],
    )
    pilot = [r["pilot_1_plus_k"] for r in rows]
    lit = [r["holtrop1982_1_plus_k"] for r in rows]
    assert pilot == sorted(pilot, reverse=True)  # what the pilot does (wrong)
    assert lit == sorted(lit)                    # what the literature does
    for a, b in zip(pilot, lit):
        assert abs(a - b) <= 0.05 * b


# --------------------------------------------------------------------------- #
# 5. The audit must actually detect the defects it reports
# --------------------------------------------------------------------------- #
def test_audit_detects_form_factor_defect():
    """The audit reports FAIL_LITERATURE for all three form-factor questions."""
    rows = audit.form_factor_check(_form_factor_questions())
    assert all(r["verdict"] == "FAIL_LITERATURE" for r in rows)
    assert all(r["pilot_outside_envelope"] for r in rows)


def test_audit_artifact_present_if_generated():
    """If the audit artifact exists it must agree with a fresh recompute."""
    import json

    path = os.path.join(REPO, "artifacts", "m1391", "literature_crosscheck.json")
    if not os.path.exists(path):
        pytest.skip("audit artifact not generated in this checkout")
    art = json.load(open(path, encoding="utf-8"))
    fresh = audit.form_factor_check(_form_factor_questions())
    assert art["D_form_factor_fail_count"] == sum(
        1 for r in fresh if r["verdict"] != "PASS"
    )
    assert art["A_anchor_check_pass"] is True