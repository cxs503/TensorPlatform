"""m1674 — cross-path conformance guards for the calm-water resistance model.

The platform ships four code paths that all answer "what is R_T of this ship":

===============  ==========================================  ===================
path             module                                      physics
===============  ==========================================  ===================
A                ``services/holtrop_mennen.py``              Holtrop-Mennen 1982
                                                             (m2 wave fit) +
                                                             1984 addenda
B                ``services/ship_resistance.py``             endpoint core:
                                                             axisymmetric S,
                                                             Cr = 0.002(1+2Cb)Fn^2.5
C                ``services/digital_twin/models/resistance`` HM 1984 (m4 fit),
                                                             Cm/Cwp regressions
D                ``services/polar/resistance.py``            HM 1984 (m4 fit),
                                                             Cm = 0.98 fixed
===============  ==========================================  ===================

Before m1674 A returned a wave resistance 38x too small for a 7.1-L/B hull
(swapped c7 branches), D used L/T instead of LR/B in iE, C used sqrt(Cb)
instead of sqrt(Cm) for the wetted surface, and A/D dropped the lower c15 band.
These tests lock the *post-fix* state so the divergence cannot silently return.

Reference values in ``PINNED`` are measured, not hand-computed: they come from
``artifacts/m1674/ablation.json`` (``python3 artifacts/m1674/ablation.py``).
"""
from __future__ import annotations

import math

import pytest

from services.digital_twin.models.resistance import holtrop_resistance as dt_holtrop
from services.holtrop_mennen import holtrop_resistance as hm_holtrop
from services.polar.resistance import holtrop_open_water_resistance as polar_holtrop
from services.polar.ships import ShipGeometry
from services.ship_resistance import resistance_breakdown

G = 9.80665

# (L, B, T, Cb)
KCS = (230.0, 32.2, 10.8, 0.6505)      # container ship,  Fn 0.08-0.29
BULKER = (180.0, 30.0, 11.0, 0.830)    # full-form bulker, Fn 0.12-0.31
TANKER = (320.0, 58.0, 20.8, 0.8098)   # VLCC,            Fn 0.13-0.30

# R_T [N] measured after the m1674 fixes (artifacts/m1674/ablation.json)
PINNED = {
    ("KCS", 8.0): {"A": 622353.6, "C": 640904.2, "D": 636274.4},
    ("KCS", 12.0): {"A": 1858042.8, "C": 2059780.9, "D": 1944383.7},
    ("BULKER", 9.0): {"A": 1440049.9, "C": 1556436.2, "D": 1435571.4},
    ("TANKER", 12.0): {"A": 7304851.2, "C": 7907280.1, "D": 7306942.7},
}
HULLS = {"KCS": KCS, "BULKER": BULKER, "TANKER": TANKER}


def _ship(label: str) -> ShipGeometry:
    l, b, t, cb = HULLS[label]
    return ShipGeometry(key=label.lower(), name=label, category="tanker", length_m=l,
                        beam_m=b, draft_m=t, cb=cb, dwt_t=1.0, design_speed_kn=12.0,
                        brake_power_kw=1.0, sfoc_g_kwh=180.0, eta_propulsive=0.6)


def _rt(label: str, v: float):
    l, b, t, cb = HULLS[label]
    return (hm_holtrop(l, b, t, cb, v).rt_n,
            dt_holtrop(v, l, b, t, cb)["RT_N"],
            polar_holtrop(_ship(label), v)["total"])


# ---------------------------------------------------------------------------
# 1. the three Holtrop paths agree in the operative Froude range
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", ["KCS", "BULKER", "TANKER"])
@pytest.mark.parametrize("v", [5.0, 7.0, 9.0])
def test_holtrop_paths_agree_in_design_range(label, v):
    """A and D (same 1984-vs-1982 wave fit family? no: A=m2, D=m4) agree to 6 %.

    The m2 (1982) and m4 (1984) wave fits only separate above Fn ~ 0.25, which
    is outside the operative range of a displacement hull, so A and D must be
    within a few per cent here.
    """
    l, _, _, _ = HULLS[label]
    fn = v / math.sqrt(G * l)
    if fn > 0.22:
        pytest.skip("Fn above the design range; see test_wave_fit_versions_split")
    a, _, d = _rt(label, v)
    assert abs(d - a) / a < 0.06, (label, v, a, d)


@pytest.mark.parametrize("label,speed", [("KCS", 8.0), ("KCS", 12.0),
                                         ("BULKER", 9.0), ("TANKER", 12.0)])
def test_pinned_totals(label, speed):
    """Silent drift detector: the measured post-fix totals must not move."""
    a, c, d = _rt(label, speed)
    for key, got in (("A", a), ("C", c), ("D", d)):
        want = PINNED[(label, speed)][key]
        assert got == pytest.approx(want, rel=0.01), (label, speed, key, got, want)


def test_wave_fit_versions_split_above_fn_025():
    """Documented, pinned split: A (1982 m2) vs D (1984 m4) diverge at high Fn.

    This is a *method-version* divergence, not a defect: both fits are
    published Holtrop wave fits.  The guard exists so that the platform cannot
    swap one for the other silently; a caller must know which fit produced the
    number it quotes.
    """
    a, _, d = _rt("TANKER", 15.0)          # Fn = 0.268
    ratio = d / a
    assert 1.05 < ratio < 1.25, ratio


# ---------------------------------------------------------------------------
# 2. the public endpoint core is NOT Holtrop-Mennen (documented mislabel)
# ---------------------------------------------------------------------------
def test_endpoint_core_differs_from_holtrop():
    """``calc.resistance`` serves path B; it must not be quoted as Holtrop.

    B uses an axisymmetric wetted surface and Cr = 0.002(1+2Cb)Fn^2.5, so for a
    full-form hull its total differs from the Holtrop paths by tens of per cent.
    The assertion pins the *status quo* of that difference: if someone unifies
    the paths, this test fails and the evidence grade of ``calc.resistance``
    must be re-audited (it is currently documented as a SUBOFF-fit, not HM).
    """
    l, b, t, cb = TANKER
    v = 12.0
    a, _, _ = _rt("TANKER", v)
    bb = resistance_breakdown(l, b, t, cb, v)["rt_n"]
    assert abs(bb - a) / a > 0.15, (bb, a)


# ---------------------------------------------------------------------------
# 3. per-path algebraic identities (independent of the physics)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", ["KCS", "TANKER"])
def test_component_sums(label):
    l, b, t, cb = HULLS[label]
    v = 10.0
    r = hm_holtrop(l, b, t, cb, v)
    parts = r.rf_n + r.rw_n + r.rbulb_n + r.rtr_n + r.ra_n + r.rapp_n
    assert r.rt_n == pytest.approx(parts, rel=1e-12)
    assert r.ct == pytest.approx(r.rt_n / (0.5 * 1025.0 * v ** 2 * r.wetted_surface_m2),
                                rel=1e-9)

    d = polar_holtrop(_ship(label), v)
    assert d["total"] == pytest.approx(d["viscous"] + d["wave"] + d["correlation"],
                                      rel=1e-12)

    c = dt_holtrop(v, l, b, t, cb)
    assert c["RT_N"] == pytest.approx(c["RF_N"] * c["form_factor"] + c["RW_N"]
                                      + c["RA_N"], rel=1e-12)


def test_wetted_surface_uses_cm_not_cb():
    """Wet surface of the digital-twin path must be the sqrt(CM) regression."""
    l, b, t, cb = BULKER
    c = dt_holtrop(7.0, l, b, t, cb)
    s = c["wetted_surface_m2"]
    for root, expect in (("cm", s), ("cb", None)):
        cm = 1.0 - 0.2 * (1.0 - cb)
        cp_ = cb / cm
        cwp = (1.0 + 2.0 * cb) / 3.0
        r = math.sqrt(cm) if root == "cm" else math.sqrt(cb)
        s_ref = l * (2 * t + b) * r * (0.453 + 0.4425 * cb - 0.2862 * cm
                                       - 0.003467 * b / t + 0.3696 * cwp)
        if root == "cm":
            assert s == pytest.approx(s_ref, rel=1e-12)
        else:
            assert abs(s - s_ref) / s_ref > 0.01   # the old (wrong) variant


# ---------------------------------------------------------------------------
# 4. applicability domain: only path A gates the regression domain
# ---------------------------------------------------------------------------
def test_domain_gate_only_on_canonical_path():
    """SUBOFF (B/T = 1, Cb = 0.54, Fn = 0.46) is outside the HM regression.

    A is the only path that says so; the surrogate paths return silently.  The
    guard records that asymmetry so no consumer mistakes B/C/D output for a
    validated Holtrop prediction outside the domain.
    """
    r = hm_holtrop(4.356, 0.508, 0.476, 0.542, 3.0)
    assert any("B/T" in w for w in r.warnings)
    assert any("Cb" in w for w in r.warnings)
    # surrogate paths emit no warnings at all (no gating API)
    assert "warnings" not in dt_holtrop(3.0, 4.356, 0.508, 0.476, 0.542)