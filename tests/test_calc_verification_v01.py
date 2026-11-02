"""V-01 regression suite: independent-benchmark verification of the ship-performance
calculation endpoints (`backend/api/calculations.py`).

Why this file exists
--------------------
The capability audit grades ``calc.resistance`` and ``calc.stability`` as REAL, but
before this suite the repository had **no** test at all that touched those routes
(``grep -rl 'calculations/resistance' backend/tests`` returned nothing), i.e. the
REAL grade rested on "an algorithm exists", not on a measured accuracy.

Method
------
Every reference quantity here is built independently of the code under test:
  * the ITTC-1957 line is re-evaluated from the closed form (50-digit mpmath when
    available, plain float otherwise);
  * wetted areas come from explicit bodies of revolution, S = int 2*pi*r*ds dx;
  * righting arms come from a strip integration of the submerged cross-section of a
    box barge, GZ = (y_B - B/2)cos(phi) + (z_B - KG)sin(phi).

The module is loaded by file path so the suite runs without the platform's
database drivers (``backend/main.py`` needs psycopg2, which is absent here).

Numbers and their provenance are in ``artifacts/verification/v01/metrics.json``;
the narrative report is ``docs/verification/V01_calc_endpoints.md``; the generator
is ``scripts/verify_calc_v01.py``.

Note on two "characterization" tests (draft insensitivity, Cr monotonicity): they
assert the *current, defective* behaviour on purpose, so the defects stay visible
and cannot be removed silently. When the model is fixed, they must be updated in
the same change - they are findings, not endorsements.
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
sys.path.insert(0, BACKEND)

RHO, NU = 998.0, 1.004e-6
_TRAP = getattr(np, "trapezoid", getattr(np, "trapz", None))


def _load_calc():
    path = os.path.join(BACKEND, "api", "calculations.py")
    if not os.path.exists(path):  # pragma: no cover
        pytest.skip(f"{path} not found")
    spec = importlib.util.spec_from_file_location("vp_calc_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CALC = _load_calc()


def resistance(**kw):
    return asyncio.run(CALC.calc_resistance(CALC.ResistanceRequest(**kw)))


def stability(**kw):
    return asyncio.run(CALC.calc_stability(CALC.StabilityRequest(**kw)))


# ---------------------------------------------------------------- references


def ittc1957_cf(Re: float) -> float:
    """ITTC-1957 friction line, evaluated outside the code under test."""
    try:
        import mpmath as mp
        mp.mp.dps = 50
        return float(mp.mpf("0.075") / (mp.log10(mp.mpf(Re)) - 2) ** 2)
    except ImportError:  # pragma: no cover
        return 0.075 / (math.log10(Re) - 2.0) ** 2


def schoenherr_cf(Re: float) -> float:
    return 0.455 / math.log10(Re) ** 2.58


def code_wetted_surface(L: float, B: float, cb: float) -> float:
    """The wetted-surface model as written in the endpoint (deliberately copied)."""
    return math.pi * B * L * (0.5 + 0.25 * cb)


def revolve_metrics(rfun, L: float, n: int = 60001):
    x = np.linspace(0.0, L, n)
    r = rfun(x)
    drdx = np.gradient(r, x)
    S = float(_TRAP(2.0 * np.pi * r * np.sqrt(1.0 + drdx ** 2), x))
    vol = float(_TRAP(np.pi * r ** 2, x))
    return S, vol / (np.pi * float(np.max(r)) ** 2 * L)


def capped_family(p_exp: float, L: float):
    """Nose / parallel middle body / tail with SUBOFF length fractions, caps as
    superellipses r = R*(1-u**p)**(1/p) (p=2 elliptical, p>2 fuller, p<2 pointier)."""
    R = 0.5 * (L / 8.57)                       # L/D = 8.57, the endpoint's default ratio
    f_nose, f_mid = 1.016 / L, 2.229 / L
    l0, x_tail, lt = f_nose * L, (f_nose + f_mid) * L, 1.111 / L * L

    def rfun(x, L=L, R=R, l0=l0, x_tail=x_tail, lt=lt, pe=p_exp):
        r = np.zeros_like(x)
        m = x < l0
        r[m] = R * (1.0 - np.clip((l0 - x[m]) / l0, 0, 1) ** pe) ** (1.0 / pe)
        m = (x >= l0) & (x <= x_tail)
        r[m] = R
        m = x > x_tail
        r[m] = R * (1.0 - np.clip((x[m] - x_tail) / lt, 0, 1) ** pe) ** (1.0 / pe)
        return r
    return rfun, R


def solve_capped(cb_target: float, L: float) -> float:
    """Cap exponent whose body has block coefficient cb_target."""
    lo, hi = 0.15, 60.0                        # Cb increases with the exponent
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        _, cb = revolve_metrics(capped_family(mid, L)[0], L, 20001)
        if cb < cb_target:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def box_barge_gz(B: float, T: float, KG: float, depth: float, phi_deg: float,
                 n: int = 200001) -> float:
    """First-principles righting arm (strip integration; handles bilge/deck limits)."""
    phi = math.radians(phi_deg)
    y = np.linspace(0.0, B, n)
    h = np.clip(T + (y - B / 2.0) * math.tan(phi), 0.0, depth)
    area = float(_TRAP(h, y))
    zc = float(_TRAP(h ** 2 / 2.0, y)) / area
    yc = float(_TRAP(y * h, y)) / area
    return (yc - B / 2.0) * math.cos(phi) + (zc - KG) * math.sin(phi)


SUBOFF = dict(length=4.356, beam=0.508, draft=0.476, cb=0.542)


# ------------------------------------------------------------ R1/R2: friction


@pytest.mark.parametrize("Re", [1e7, 1e8, 1e9])
def test_friction_line_matches_ittc1957(Re):
    """C_F recovered from the endpoint equals the ITTC-1957 closed form."""
    V = 5.0
    L = Re * NU / V
    B, T = L / 8.57, L / 9.15
    r = resistance(speed_ms=V, length=L, beam=B, draft=T, cb=0.542)
    cf_code = r.frictional_resistance_n / (0.5 * RHO * code_wetted_surface(L, B, 0.542) * V ** 2)
    cf_ref = ittc1957_cf(r.reynolds_number)
    assert r.frictional_resistance_n > 10.0          # rounding of the response is negligible
    assert abs(cf_code - cf_ref) / cf_ref < 1e-6, (cf_code, cf_ref)


def test_friction_line_consistent_with_schoenherr():
    """Two published lines must agree to a few percent over Re = 1e7..1e9."""
    for Re in (1e7, 1e8, 1e9):
        dev = abs(ittc1957_cf(Re) - schoenherr_cf(Re)) / schoenherr_cf(Re)
        assert dev < 0.05, (Re, dev)


# --------------------------------------------------------------- R3: identities


def test_flat_plate_identities_hold():
    r = resistance(speed_ms=3.05, **SUBOFF)
    S = code_wetted_surface(SUBOFF["length"], SUBOFF["beam"], SUBOFF["cb"])
    cf = ittc1957_cf(r.reynolds_number)
    rf_ref = 0.5 * RHO * S * cf * 3.05 ** 2
    assert abs(r.frictional_resistance_n - rf_ref) / rf_ref < 1e-4
    assert abs(r.total_resistance_n - r.frictional_resistance_n - r.residual_resistance_n) < 1e-3
    assert abs(r.froude_number - 3.05 / math.sqrt(9.81 * SUBOFF["length"])) < 1e-6
    assert abs(r.reynolds_number - 3.05 * SUBOFF["length"] / NU) / r.reynolds_number < 1e-6


# ------------------------------------------------------- R4: wetted surface


def test_quadrature_matches_spheroid_closed_form():
    """The reference integrator itself is checked against an exact area."""
    L, R = SUBOFF["length"], SUBOFF["beam"] / 2.0
    S_num, _ = revolve_metrics(
        lambda x: R * np.sqrt(np.clip(1.0 - ((x - L / 2) / (L / 2)) ** 2, 0, 1)), L)
    a, b = L / 2.0, R
    e = math.sqrt(1.0 - (b / a) ** 2)
    S_ana = 2 * np.pi * b ** 2 + 2 * np.pi * (a * b / e) * math.asin(e)
    assert abs(S_num - S_ana) / S_ana < 1e-6


def test_wetted_surface_cylinder_counterexample():
    """Hard counterexample, no literature needed: a cylinder's lateral area is pi*D*L,
    the endpoint's Cb-only fit returns 0.75*pi*D*L -> -25%."""
    L, B = 100.0, 12.0
    S_true = math.pi * B * L
    S_code = code_wetted_surface(L, B, 1.0)
    rel = (S_code - S_true) / S_true
    assert abs(rel - (-0.25)) < 0.005, rel


def test_wetted_surface_error_grows_with_fullness():
    """On a SUBOFF-proportioned family the fit is acceptable when slender and fails
    the 15% platform bound for fuller bodies (Cb crossover near 0.71)."""
    L = SUBOFF["length"]
    for cb_t, limit in ((0.540, 0.10), (0.750, -0.15)):
        p = solve_capped(cb_t, L)
        rfun, _ = capped_family(p, L)
        S_exact, cb = revolve_metrics(rfun, L)
        rel = (code_wetted_surface(L, SUBOFF["beam"], cb) - S_exact) / S_exact
        if limit > 0:
            assert abs(rel) < limit, (cb_t, rel)
        else:
            assert rel < limit, (cb_t, rel)


def test_wetted_surface_shape_ambiguity_at_fixed_cb():
    """At the default Cb the true wetted area is not determined by Cb alone: two very
    different bodies differ by >5% in S/(pi*D*L) at the same Cb, and the endpoint
    cannot express that spread."""
    L, B = SUBOFF["length"], SUBOFF["beam"]
    p = solve_capped(0.542, L)
    S_capped, cb1 = revolve_metrics(capped_family(p, L)[0], L)
    S_cos, cb2 = revolve_metrics(
        lambda x: (B / 2.0) * np.sin(np.pi * x / L) ** 0.8067, L)
    assert abs(cb1 - 0.542) < 5e-3 and abs(cb2 - 0.542) < 5e-3
    spread = S_cos / S_capped - 1.0
    assert spread > 0.05, spread


# ------------------------------------------------------------- R5: stability


@pytest.mark.parametrize("phi", [0.0, 5.0, 10.0, 20.0, 30.0, 38.0])
def test_gz_matches_first_principles_in_valid_domain(phi):
    """Wall-sided closed form vs strip integration, inside the validity domain."""
    B, T, KG, depth = 20.0, 8.0, 6.0, 20.0
    KB, BM = T / 2.0, B ** 2 / (12.0 * T)
    GM = KB + BM - KG
    got = stability(displacement_t=1000.0, gm_m=GM, kb_m=KB, bm_m=BM,
                    heel_angle_deg=phi).righting_arm_m
    ref = box_barge_gz(B, T, KG, depth, phi)
    assert abs(got - ref) < 1e-5, (phi, got, ref)


def test_gz_small_angle_limit_is_gm():
    B, T, KG = 20.0, 8.0, 6.0
    KB, BM = T / 2.0, B ** 2 / (12.0 * T)
    GM = KB + BM - KG
    phi = 0.2
    got = stability(displacement_t=1000.0, gm_m=GM, kb_m=KB, bm_m=BM,
                    heel_angle_deg=phi).righting_arm_m / math.radians(phi)
    limit = GM + 0.5 * BM * math.tan(math.radians(phi)) ** 2
    assert abs(got - limit) / GM < 1e-3, (got, limit)


def test_gz_righting_moment_consistent_with_arm():
    r = stability(displacement_t=1000.0, gm_m=2.0, kb_m=4.0, bm_m=4.0, heel_angle_deg=15.0)
    rm_ref = 1000.0 * 1000.0 * 9.81 * r.righting_arm_m / 1000.0
    assert abs(r.righting_moment_knm - rm_ref) / rm_ref < 1e-3


# ------------------------------------------- R6: structural findings (locked)


def test_finding_resistance_ignores_draft():
    """FINDING (characterization, not endorsement): the draft field is accepted and
    validated but never used, so R_t is identical for a 20x draft change.  Physics
    requires a positive sensitivity; this test fails once that is implemented, and
    that is the signal to replace it with a positive-sensitivity assertion."""
    rt = [resistance(speed_ms=3.05, length=4.356, beam=0.508, draft=t, cb=0.542)
          .total_resistance_n for t in (0.2, 0.476, 1.0, 2.0, 4.0)]
    assert max(rt) - min(rt) < 1e-9 * max(rt), rt


def test_finding_residual_resistance_has_no_hump():
    """FINDING (characterization): Cr = 0.002*(1+2Cb)*Fn^2.5 is strictly increasing in
    Fn, so it structurally cannot reproduce the wave-resistance hump of real hulls."""
    fn, cr = [], []
    S = code_wetted_surface(4.356, 0.508, 0.542)
    for V in (1.0, 1.5, 2.0, 2.5, 3.05, 3.5, 4.0, 4.5, 5.0):
        r = resistance(speed_ms=V, length=4.356, beam=0.508, draft=0.476, cb=0.542)
        fn.append(r.froude_number)
        cr.append(r.residual_resistance_n / (0.5 * RHO * S * V ** 2))
    assert all(b >= a for a, b in zip(cr, cr[1:])), list(zip(fn, cr))


def test_block_coefficient_guard_rejects_non_physical_input():
    from fastapi import HTTPException
    # cb=1.4 is rejected by the pydantic field constraint (le=1) before the handler
    with pytest.raises(ValueError):
        resistance(speed_ms=3.0, length=4.356, beam=0.508, draft=0.476, cb=1.4)
    # cb=0.1 passes field validation and is rejected by the handler's physical range check
    with pytest.raises(HTTPException):
        resistance(speed_ms=3.0, length=4.356, beam=0.508, draft=0.476, cb=0.1)