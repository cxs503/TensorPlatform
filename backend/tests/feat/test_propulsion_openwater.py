"""Verification tests for the propulsion (推进) open-water baseline.

Every assertion is a closed-form anchor, a conservation identity or a
documented-audit verdict - no hand-copied target numbers:

* open-water efficiency identity ``eta_0 = J KT / (2 pi KQ)``
* actuator-disk ideal efficiency ``eta_i = 2/(1+sqrt(1+C_T)) = 1/(1+a)``,
  ``C_T = 4 a (1+a)``, ``C_T = 8 KT/(pi J^2)``
* thrust / torque / power identities and Froude similarity (``n``, ``D`` scaling)
* gate suite behaviour: passes a structurally physical curve, catches the
  efficiency-identity violation inside the serving dataset, catches a
  non-monotone curve and a momentum-bound violation
* residual verdicts for the serving ``wageningen_b`` model under test: its
  deviation from its *own* cited dataset exceeds 15 % at low advance ratio
  while its metadata declares ``validation_status = verified``
"""
from __future__ import annotations

import math

import pytest

from methods.propulsion import (
    audit_open_water_curve,
    axial_induction_factor,
    derived_quantities,
    evaluate_b_series,
    fit_reference_model,
    ideal_efficiency_from_CT,
    ideal_efficiency_from_KT_J,
    ideal_wake_fraction,
    load_reference_dataset,
    open_water_efficiency,
    serving_wageningen_b,
    thrust_loading_coefficient,
)

DATASET = load_reference_dataset()
J_REF = DATASET["data_points"]["J"]
KT_REF = DATASET["data_points"]["KT"]
KQ_REF = DATASET["data_points"]["KQ"]
ETA_REF = DATASET["data_points"]["eta_0"]


# ---------------------------------------------------------------------------
# analytic anchors
# ---------------------------------------------------------------------------

def test_efficiency_identity_matches_definition():
    assert open_water_efficiency(0.6, 0.18, 0.027) == pytest.approx(
        0.6 * 0.18 / (2.0 * math.pi * 0.027), rel=1e-12)


def test_efficiency_identity_rejects_zero_torque():
    with pytest.raises(ValueError):
        open_water_efficiency(0.5, 0.2, 0.0)


def test_actuator_disk_two_closed_forms_agree():
    """``2/(1+sqrt(1+C_T))`` and ``1/(1+a)`` with ``C_T = 4a(1+a)`` are the same function."""
    for CT in (0.0, 0.05, 0.3, 1.0, 2.5, 6.0):
        a = axial_induction_factor(CT)
        assert CT == pytest.approx(4.0 * a * (1.0 + a), rel=1e-12)
        assert ideal_efficiency_from_CT(CT) == pytest.approx(1.0 / (1.0 + a), rel=1e-12)


def test_actuator_disk_limits():
    assert ideal_efficiency_from_CT(0.0) == pytest.approx(1.0, rel=1e-15)
    # C_T = 1 -> a = (sqrt(2)-1)/2
    assert axial_induction_factor(1.0) == pytest.approx((math.sqrt(2.0) - 1.0) / 2.0, rel=1e-12)
    assert ideal_efficiency_from_CT(1.0) == pytest.approx(2.0 / (1.0 + math.sqrt(2.0)), rel=1e-12)
    # ideal efficiency decreases monotonically with loading
    values = [ideal_efficiency_from_CT(ct) for ct in (0.0, 0.5, 1.0, 2.0, 4.0)]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_ideal_wake_fraction_relation():
    CT = 0.8
    a = axial_induction_factor(CT)
    w = ideal_wake_fraction(CT)
    assert w == pytest.approx(a / (1.0 + a), rel=1e-12)
    assert 0.0 < w < 0.5


def test_efficiency_bound_from_KT_J_equals_CT_route():
    assert ideal_efficiency_from_KT_J(0.8, 0.09) == pytest.approx(
        ideal_efficiency_from_CT(thrust_loading_coefficient(0.8, 0.09)), rel=1e-12)
    assert thrust_loading_coefficient(0.8, 0.09) == pytest.approx(
        8.0 * 0.09 / (math.pi * 0.8 ** 2), rel=1e-12)


def test_derived_quantities_identities():
    out = derived_quantities(J=0.7, KT=0.2, KQ=0.03, rho=1000.0, n=2.0, D=1.0)
    assert out["thrust_N"] == pytest.approx(0.2 * 1000.0 * 4.0, rel=1e-12)
    assert out["torque_Nm"] == pytest.approx(0.03 * 1000.0 * 4.0, rel=1e-12)
    assert out["power_W"] == pytest.approx(out["power_closed_form_W"], rel=1e-12)
    assert out["advance_speed_m_s"] == pytest.approx(0.7 * 2.0, rel=1e-12)


def test_froude_similarity_scaling():
    """At equal J, KT/KQ are invariant while T scales as n^2 D^4 and Q as n^2 D^5."""
    base = derived_quantities(J=0.6, KT=0.25, KQ=0.035, rho=1025.0, n=5.0, D=1.0)
    scaled = derived_quantities(J=0.6, KT=0.25, KQ=0.035, rho=1025.0, n=10.0, D=2.0)
    assert scaled["thrust_N"] == pytest.approx(base["thrust_N"] * 4.0 * 16.0, rel=1e-12)
    assert scaled["torque_Nm"] == pytest.approx(base["torque_Nm"] * 4.0 * 32.0, rel=1e-12)
    assert scaled["advance_speed_m_s"] == pytest.approx(base["advance_speed_m_s"] * 4.0, rel=1e-12)


# ---------------------------------------------------------------------------
# B-series polynomial structure (refuses to guess literature coefficients)
# ---------------------------------------------------------------------------

def test_b_series_refuses_without_coefficient_table():
    with pytest.raises(ValueError, match="coefficient table supplied"):
        evaluate_b_series(0.6, 1.0, 0.7)


def test_b_series_structure_ordering():
    """``KT = sum C J^s (P/D)^t (Ae/A0)^u`` with the documented exponent order."""
    terms = {"KT": [(0, 0, 0, 0.1), (1, 1, 0, 0.2)], "KQ": [(0, 1, 1, 0.03)]}
    out = evaluate_b_series(J=0.5, P_D=1.2, Ae_Ao=0.7, terms=terms)
    assert out["KT"] == pytest.approx(0.1 + 0.2 * 0.5 * 1.2, rel=1e-12)
    assert out["KQ"] == pytest.approx(0.03 * 1.2 * 0.7, rel=1e-12)
    assert out["eta_0"] == pytest.approx(0.5 * out["KT"] / (2 * math.pi * out["KQ"]), rel=1e-12)


# ---------------------------------------------------------------------------
# gate suite
# ---------------------------------------------------------------------------

def test_gates_pass_on_reference_model():
    ref = fit_reference_model(J_REF, KT_REF, KQ_REF)
    # extend past the declared range: the reference curve clamps instead of extrapolating
    j_ext = [i * (1.35) / 44.0 for i in range(45)]
    curve = ref.curve(j_ext)
    audit = audit_open_water_curve(curve["J"], curve["KT"], curve["KQ"], curve["eta_0"])
    evaluated = [k for k, v in audit["gates"].items() if v["pass"] is not None]
    assert len(evaluated) >= 6
    failed = {k: v["detail"] for k, v in audit["gates"].items() if v["pass"] is False}
    assert not failed, failed
    # zero-thrust crossing located to the resolution of the audit grid (0.03 in J)
    assert audit["summary"]["J_zero_thrust"] == pytest.approx(1.2, abs=0.04)


def test_gates_flag_identity_violation_in_serving_dataset():
    """The platform's declared dataset violates eta_0 = J KT/(2 pi KQ) at J = 0.8."""
    audit = audit_open_water_curve(J_REF, KT_REF, KQ_REF, ETA_REF)
    gate = audit["gates"]["G1_eta_identity"]
    assert gate["pass"] is False
    assert gate["detail"]["worst_J"] == pytest.approx(0.8)
    assert gate["detail"]["max_abs_dev"] > 0.10


def test_gates_flag_non_monotone_curve():
    j = [0.0, 0.2, 0.4, 0.6]
    kt = [0.4, 0.35, 0.36, 0.2]        # bump at J=0.4
    kq = [0.04, 0.035, 0.03, 0.02]
    audit = audit_open_water_curve(j, kt, kq)
    assert audit["gates"]["G3_monotone"]["pass"] is False


def test_gates_flag_momentum_bound_violation():
    """An over-efficient curve (eta_0 near 1 at high loading) breaks eta_0 <= eta_i."""
    j = [0.3, 0.4, 0.5]
    kt = [0.9, 0.7, 0.5]
    kq = [0.02, 0.016, 0.012]          # eta_0 ~ 2.1, 2.8, 3.3 -> impossible
    audit = audit_open_water_curve(j, kt, kq)
    assert audit["gates"]["G6_momentum_bound"]["pass"] is False
    assert audit["summary"]["momentum_utilisation"] > 1.0


def test_reference_model_is_structurally_physical():
    ref = fit_reference_model(J_REF, KT_REF, KQ_REF)
    fitted = ref.curve(J_REF)
    # (a) reproduces the declared points exactly (interpolation, not a compromise fit)
    assert max(abs(a - b) for a, b in zip(fitted["KT"], KT_REF)) < 1e-12
    assert max(abs(a - b) for a, b in zip(fitted["KQ"], KQ_REF)) < 1e-12
    assert ref.mu == 0.0
    # (b) strictly monotone on a fine grid, no interpolation overshoot
    j = [i * 0.01 for i in range(0, 121)]
    curve = ref.curve(j)
    assert all(a > b for a, b in zip(curve["KT"], curve["KT"][1:]))
    assert all(a > b for a, b in zip(curve["KQ"], curve["KQ"][1:]))
    assert curve["KT"][0] == pytest.approx(KT_REF[0]) and curve["KQ"][0] == pytest.approx(KQ_REF[0])
    # (c) clamps outside the declared range instead of inventing an extrapolation
    assert ref.coefficients(1.5)["KT"] == pytest.approx(0.0, abs=1e-15)
    assert ref.kt_zero_J == pytest.approx(1.2, abs=1e-9)


def test_reference_model_rejects_bad_input():
    with pytest.raises(ValueError):
        fit_reference_model([0.0, 0.1], [0.4, 0.3], [0.04, 0.03])          # too few points
    with pytest.raises(ValueError):
        fit_reference_model([0.0, 0.2, 0.1], [0.4, 0.3, 0.2], [0.04, 0.03, 0.02])  # J not sorted
    with pytest.raises(ValueError):
        fit_reference_model([0.0, 0.2, 0.4], [0.4, 0.45, 0.2], [0.04, 0.03, 0.02])  # non-monotone KT


# ---------------------------------------------------------------------------
# model under test: serving wageningen_b
# ---------------------------------------------------------------------------

def test_serving_model_algebra_is_self_consistent():
    """The serving model is internally consistent (eta_0 built from the identity)."""
    for J in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        out = serving_wageningen_b(J, P_D=1.0, Ae_Ao=0.7, Z=4)
        if out["KT"] > 0.0:
            assert out["eta_0"] == pytest.approx(
                J * out["KT"] / (2 * math.pi * out["KQ"]), rel=1e-9, abs=1e-12)
        if J > 0.0:                       # C_T is undefined at bollard (J = 0)
            assert out["eta_0"] <= ideal_efficiency_from_KT_J(J, out["KT"]) + 1e-9


def test_serving_model_respects_declared_range():
    with pytest.raises(ValueError):
        serving_wageningen_b(1.3)
    with pytest.raises(ValueError):
        serving_wageningen_b(0.5, P_D=1.5)
    with pytest.raises(ValueError):
        serving_wageningen_b(0.5, Z=6)


def test_serving_model_deviation_from_its_cited_dataset_exceeds_15_percent():
    """Audit verdict: >15 % KT deviation at J <= 0.6 while metadata says 'verified'."""
    rows = []
    for J, kt_ref, kq_ref in zip(J_REF, KT_REF, KQ_REF):
        if kt_ref <= 0.0 or kq_ref <= 0.0:
            continue                      # relative deviation undefined at the null point
        out = serving_wageningen_b(J, P_D=1.0, Ae_Ao=0.7, Z=4)
        rows.append((J, out["KT"], (out["KT"] - kt_ref) / kt_ref,
                     out["KQ"], (out["KQ"] - kq_ref) / kq_ref))
    assert max(abs(r[2]) for r in rows) > 0.15, rows      # KT deviation blows up
    assert max(abs(r[4]) for r in rows) > 0.15, rows      # KQ deviation blows up
    last = [r for r in rows if r[0] >= 0.6]               # high-J region is worst
    assert max(abs(r[2]) for r in last) > 1.0, rows
    assert max(abs(r[4]) for r in last) > 1.0, rows


def test_serving_model_has_no_zero_thrust_point_inside_its_own_range():
    """Serving model: KT stays positive (0.0686) at J = 1.2 and zeroes only at 1.424.

    The analytic root of its regression ``KT = 0.419 - 0.28 J - 0.01 J^2`` (P/D=1.0,
    Ae/Ao=0.7, Z=4) lies *outside* the declared validity range 0 <= J <= 1.2, so the
    model cannot represent the dataset's null point at J = 1.2 at all.
    """
    import numpy as np
    roots = np.roots([-0.010, -0.280, 0.419])
    j0_serv = float(max(roots))
    assert j0_serv == pytest.approx(1.424, abs=0.01)
    assert j0_serv > 1.2                                  # outside declared range
    ref = fit_reference_model(J_REF, KT_REF, KQ_REF)
    assert ref.kt_zero_J < 1.35                           # reference model zeroes near the data
    assert abs(j0_serv - ref.kt_zero_J) / j0_serv > 0.10
    # and the serving model still predicts 0.0686 of thrust coefficient at J = 1.2,
    # where the declared dataset says KT = 0
    assert serving_wageningen_b(1.2, P_D=1.0, Ae_Ao=0.7, Z=4)["KT"] == pytest.approx(0.0684, abs=0.003)
