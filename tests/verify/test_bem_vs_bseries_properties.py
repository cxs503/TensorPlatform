"""Verification gates for the merged BEM open-water model (m1665).

These tests are the *regression* half of the m1665 audit
(``tools/verify/propeller_open_water/audit_bem_vs_bseries.py``): they pin the
two error sources the audit separated, so a future edit cannot silently move
either one.

1. **Solution verification** (numerics): the radial-strip discretisation error of
   the shipped 40-strip model is below the 3% report gate — measured against the
   320-strip solution, which is itself checked to be converged (successive
   differences shrinking).
2. **Validation envelope** (model form): the BEM model's distance to the
   canonical Wageningen B-series polynomial is pinned per geometry.  The audit
   found the gap is systematic (BEM over-predicts thrust) and grows with blade
   area, so the gates encode *both* the sign and a per-geometry ceiling with
   margin.  A future physics upgrade (camber / cascade losses) must tighten
   these ceilings deliberately, not by accident.

Runs offline: no service, no network, ~15 s.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "backend"), ROOT,
           os.path.join(ROOT, "tools", "verify", "propeller_open_water")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
import reference_bseries as ref  # noqa: E402

TARGET_PCT = 3.0     # report gate
ACCEPT_PCT = 15.0    # platform acceptance rule


def geom(P_D: float, Ae_Ao: float, Z: int = 4, sections: int = 40) -> PropellerGeometry:
    return PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D, blade_area_ratio=Ae_Ao,
                             blade_count=Z, sections=sections)


def rel(a: float, b: float) -> float:
    return 100.0 * (a - b) / b


# ---------------------------------------------------------------------------
# 1. solution verification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("J", [0.30, 0.50, 0.70])
def test_radial_discretisation_below_report_gate(J: float) -> None:
    """40 strips (the shipped default) vs 320 strips: < 3% in KT, KQ and eta_0."""
    g40, g320 = geom(1.0, 0.70, sections=40), geom(1.0, 0.70, sections=320)
    a, b = open_water_point(g40, J), open_water_point(g320, J)
    for key in ("KT", "KQ", "eta0"):
        assert abs(rel(a[key], b[key])) < TARGET_PCT, (key, J, rel(a[key], b[key]))


def test_refinement_is_converging_not_oscillating() -> None:
    """Successive strip refinements must shrink the increments monotonically."""
    J = 0.50
    vals = [open_water_point(geom(1.0, 0.70, sections=n), J)["KT"]
            for n in (40, 80, 160, 320)]
    diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
    assert diffs[1] < diffs[0] * 0.6, diffs
    assert diffs[2] < diffs[1] * 0.6, diffs
    # grid-independent limit: the finest pair differs by < 0.05% of KT
    assert abs(rel(vals[-1], vals[-2])) < 0.05, (vals, diffs)


# ---------------------------------------------------------------------------
# 2. physical rails (must hold for every geometry, in or out of the B-series box)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("P_D,Ae_Ao,Z", [(1.0, 0.70, 4), (0.8, 0.55, 4), (0.95, 0.80, 5),
                                         (1.2, 0.50, 3), (1.1, 0.85, 7)])
def test_thrust_and_torque_decrease_with_advance_ratio(P_D, Ae_Ao, Z) -> None:
    g = geom(P_D, Ae_Ao, Z)
    pts = [open_water_point(g, J) for J in [round(0.05 * k, 2) for k in range(1, 19)]]
    for a, b in zip(pts, pts[1:]):
        assert b["KT"] < a["KT"], (a["J"], b["J"])
        assert b["KQ"] < a["KQ"], (a["J"], b["J"])


@pytest.mark.parametrize("P_D,Ae_Ao,Z", [(1.0, 0.70, 4), (0.8, 0.55, 4), (1.1, 0.85, 7)])
def test_efficiency_never_exceeds_actuator_disc_bound(P_D, Ae_Ao, Z) -> None:
    g = geom(P_D, Ae_Ao, Z)
    for J in [round(0.05 * k, 2) for k in range(1, 19)]:
        p = open_water_point(g, J)
        assert 0.0 <= p["eta0"] <= p["eta_ideal"] + 1e-9, (J, p["eta0"], p["eta_ideal"])


# ---------------------------------------------------------------------------
# 3. validation envelope vs the canonical B-series polynomial
# ---------------------------------------------------------------------------
#: Audit-measured mean |e_KT| over J<=0.5 (BEM vs polynomial) and the ceiling this
#: test enforces.  The margin (ceil > measured) is headroom for a future physics
#: upgrade; a regression that *worsens* the gap fails.
ENVELOPE = {
    #  P/D, Ae/Ao, Z : measured mean|e_KT| %, ceiling %
    (0.80, 0.55, 4): (3.7, 8.0),     # B4-55  -- inside the 15% acceptance band
    (1.00, 0.70, 4): (27.8, 32.0),   # B4-70  -- reference geometry
    (0.95, 0.80, 5): (45.0, 50.0),   # B5-80  -- high area: worst in-box
    (1.20, 0.50, 3): (4.9, 10.0),    # B3-50
    (1.10, 0.85, 7): (56.7, 62.0),   # B7-85
}


@pytest.mark.parametrize("P_D,Ae_Ao,Z", sorted(ENVELOPE))
def test_kt_envelope_vs_canonical_polynomial(P_D, Ae_Ao, Z) -> None:
    measured, ceiling = ENVELOPE[(P_D, Ae_Ao, Z)]
    g = geom(P_D, Ae_Ao, Z)
    errs = []
    for k in range(1, 11):                      # J = 0.05 .. 0.50
        J = round(0.05 * k, 2)
        c = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        if not (c["KT"] > 0.0 and c["KQ"] > 0.0):
            continue
        errs.append(abs(rel(open_water_point(g, J)["KT"], c["KT"])))
    mean = sum(errs) / len(errs)
    assert mean <= ceiling, (P_D, Ae_Ao, Z, mean, ceiling)
    # and it must not improve so much that the documented envelope is stale:
    # a physics change has to come with a regenerated audit
    assert mean >= measured * 0.5, ("envelope stale -- regenerate the m1665 audit", mean, measured)


@pytest.mark.parametrize("P_D,Ae_Ao,Z,in_band", [(0.8, 0.55, 4, True), (1.0, 0.70, 4, False),
                                                 (1.1, 0.85, 7, False)])
def test_acceptance_band_membership_is_as_documented(P_D, Ae_Ao, Z, in_band) -> None:
    """Only low-area 4-blade geometries land inside the 15% acceptance band;
    the audit report states exactly this, so the test pins it."""
    g = geom(P_D, Ae_Ao, Z)
    errs = []
    for k in range(1, 11):
        J = round(0.05 * k, 2)
        c = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        if not (c["KT"] > 0.0 and c["KQ"] > 0.0):
            continue
        errs.append(abs(rel(open_water_point(g, J)["KT"], c["KT"])))
    assert (sum(errs) / len(errs) <= ACCEPT_PCT) is in_band


@pytest.mark.parametrize("P_D,Ae_Ao,Z", [(1.0, 0.70, 4), (0.8, 0.55, 4), (0.95, 0.80, 5),
                                         (1.2, 0.50, 3), (1.1, 0.85, 7)])
def test_zero_thrust_advance_ratio_agrees_within_10pc(P_D, Ae_Ao, Z) -> None:
    """Integral anchor: J(KT=0) is insensitive to point scatter."""
    g = geom(P_D, Ae_Ao, Z)
    j0_bem = solve_zero_thrust_advance_ratio(g)
    j0_ref = ref.zero_thrust_J(P_D, Ae_Ao, Z)
    assert abs(rel(j0_bem, j0_ref)) <= 10.0, (j0_bem, j0_ref)


@pytest.mark.parametrize("P_D,Ae_Ao,Z", [(1.0, 0.70, 4), (0.95, 0.80, 5)])
def test_peak_efficiency_agrees_within_11pc(P_D, Ae_Ao, Z) -> None:
    g = geom(P_D, Ae_Ao, Z)
    best = {"eta": -1.0}
    for i in range(141):
        J = 1.4 * i / 140.0
        p = open_water_point(g, J)
        if p["KT"] > 0.0 and p["KQ"] > 0.0 and p["eta0"] > best["eta"]:
            best = {"eta": p["eta0"], "J": J}
    canon = ref.peak_efficiency(P_D, Ae_Ao, Z)
    assert best["eta"] < 1.0
    assert abs(rel(best["eta"], canon["eta_0"])) <= 11.0, (best, canon)


def test_bem_overpredicts_thrust_systematically() -> None:
    """The audit's central finding: the gap is a systematic *level* error
    (idealised symmetric-section lift, no camber/cascade loss), not noise."""
    g = geom(1.0, 0.70)
    for J in (0.2, 0.3, 0.4, 0.5, 0.6):
        c = ref.open_water_coefficients(J, 1.0, 0.70, 4)
        assert open_water_point(g, J)["KT"] > c["KT"], J


def test_momentum_coupling_is_load_bearing() -> None:
    """Disabling induction must change KT materially -- otherwise the
    'momentum-consistent' claim in the module docstring is decoration."""
    g = geom(1.0, 0.70)
    on = open_water_point(g, 0.5)["KT"]
    off = open_water_point(g, 0.5, axial_induction=False)["KT"]
    assert off > on * 1.5, (on, off)