"""Holtrop-Mennen cross-implementation consistency (mission m1648).

The platform ships two "Holtrop" entry points.  These tests pin them to each
other and to the published piecewise coefficients, so a future edit of either
copy cannot silently re-introduce the defects found at m1648:

* ``c15`` constant branches of the 1982 m2 term (swapped in
  ``services/holtrop_mennen`` until m1648, R_W +12.8 % for KVLCC2 at Fn=0.22);
* the digital-twin copy's swapped ``c7`` branches (c1 up to 24x, R_W up to
  220x) and its substitution of the unrelated ``m4`` term for ``m2`` and of
  ``sqrt(Cb)`` for the published ``sqrt(Cm)``.

The frozen numbers below come from an independent transcription of the paper
(``tools/verify/holtrop_crosscheck.py::paper_literal``); the cross-check tool
grades both implementations against it on a 5-hull x 4-Fn sweep.
"""
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.holtrop_mennen import c15_coefficient, ittc1957_cf  # noqa: E402
from services.digital_twin.models.resistance import holtrop_resistance as dt  # noqa: E402
from services.holtrop_mennen import holtrop_resistance as ref  # noqa: E402

G = 9.81
HULLS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, Cb=0.8098),
    "KCS": dict(L=230.0, B=32.2, T=10.8, Cb=0.651),
    "S60-Cb060": dict(L=121.92, B=16.26, T=6.5, Cb=0.60),
}


def _cm(cb):
    return 1.0 - 0.2 * (1.0 - cb)


def _cwp(cb):
    return (1.0 + 2.0 * cb) / 3.0


def _ref(h, V):
    r = ref(length=h["L"], beam=h["B"], draft=h["T"], cb=h["Cb"], speed_ms=V,
            cm=_cm(h["Cb"]), cwp=_cwp(h["Cb"]), lcb_frac=0.0,
            rho=1025.0, nu=1.188e-6)
    return {
        "RT_N": r.rt_n, "RW_N": r.rw_n, "RA_N": r.ra_n,
        "RF_N": r.rf_n / r.form_factor, "form_factor": r.form_factor,
        "Cf": r.cf, "wetted_surface_m2": r.wetted_surface_m2,
        "Rn": r.re, "Fn": r.fn, "Cm": r.cm, "Cwp": r.cwp, "Cp": r.cp,
        "c1": r.c1,
    }


# ---------------------------------------------------------------------------
# 1. published piecewise coefficients
# ---------------------------------------------------------------------------
def test_c15_follows_published_branches_and_is_continuous():
    # Holtrop & Mennen 1982: -1.69385 below L/nabla^(1/3)=8, 0 above 12
    assert c15_coefficient(4.71) == pytest.approx(-1.69385)   # KVLCC2
    assert c15_coefficient(7.99) == pytest.approx(-1.69385)
    assert c15_coefficient(8.0) == pytest.approx(-1.69385)
    assert c15_coefficient(10.0) == pytest.approx(-1.69385 + 2.0 / 2.36)
    assert c15_coefficient(12.0) == pytest.approx(0.0, abs=2e-3)
    assert c15_coefficient(13.0) == pytest.approx(0.0)
    # no jump anywhere: the pre-m1648 form dropped 1.69 at x = 12
    xs = [6.0 + 0.01 * i for i in range(800)]
    vals = [c15_coefficient(x) for x in xs]
    assert max(abs(b - a) for a, b in zip(vals, vals[1:])) < 0.01


def test_ittc1957_closed_form():
    assert ittc1957_cf(1e7) == pytest.approx(0.0030, rel=1e-12)
    assert ittc1957_cf(1e9) == pytest.approx(0.075 / 49.0, rel=1e-12)


# ---------------------------------------------------------------------------
# 2. the two entry points agree, component by component
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hull", sorted(HULLS))
@pytest.mark.parametrize("fn", [0.15, 0.21, 0.26, 0.30])
def test_digital_twin_matches_paper_faithful_implementation(hull, fn):
    h = HULLS[hull]
    V = fn * math.sqrt(G * h["L"])
    a = _ref(h, V)
    b = dt(V, h["L"], h["B"], h["T"], h["Cb"], rho=1025.0, nu=1.188e-6)
    for key in ("RT_N", "RW_N", "RA_N", "RF_N", "form_factor", "Cf",
                "wetted_surface_m2", "Rn", "Fn", "Cm", "Cwp", "Cp", "c1"):
        assert b[key] == pytest.approx(a[key], rel=1e-12), key
    assert set(b) >= {"RT_N", "RF_N", "RW_N", "RA_N", "form_factor", "Cf",
                      "Rn", "Fn", "wetted_surface_m2", "Cwp", "Cm", "Cp",
                      "c1", "c16", "lambda"}


@pytest.mark.parametrize("hull", sorted(HULLS))
def test_digital_twin_uses_sqrtCm_wetted_surface(hull):
    """Regression: the copy used sqrt(Cb), -8 % to -19 % on S."""
    h = HULLS[hull]
    V = 0.21 * math.sqrt(G * h["L"])
    cm, cb = _cm(h["Cb"]), h["Cb"]
    bracket = (0.453 + 0.4425 * cb - 0.2862 * cm
               - 0.003467 * h["B"] / h["T"] + 0.3696 * _cwp(cb))
    s_cm = h["L"] * (2 * h["T"] + h["B"]) * math.sqrt(cm) * bracket
    s_cb = h["L"] * (2 * h["T"] + h["B"]) * math.sqrt(cb) * bracket
    s = dt(V, h["L"], h["B"], h["T"], cb, rho=1025.0, nu=1.188e-6)["wetted_surface_m2"]
    assert s == pytest.approx(s_cm, rel=1e-9)
    assert abs(s - s_cb) > 1e-3 * s_cm      # the old form is detectably different


def test_digital_twin_c7_branches_are_not_swapped():
    """For 0.11 <= B/L <= 0.25 the published c7 is 0.5 - 0.0625 L/B."""
    h = HULLS["KCS"]                       # B/L = 0.14
    V = 0.21 * math.sqrt(G * h["L"])
    r = dt(V, h["L"], h["B"], h["T"], h["Cb"], rho=1025.0, nu=1.188e-6)
    cp = r["Cp"]
    cwp = _cwp(h["Cb"])
    lb = h["L"] / h["B"]
    lr = h["L"] * (1.0 - cp)
    ie = 1.0 + 89.0 * math.exp(
        -((lb ** 0.80856) * ((1.0 - cwp) ** 0.30484)
          * ((1.0 - cp) ** 0.6367) * ((lr / h["B"]) ** 0.34574)
          * ((100.0 * h["Cb"] * h["B"] * h["T"] / h["L"] ** 2) ** 0.16302)))
    c7_lit = 0.5 - 0.0625 * lb
    c1_lit = 2223105.0 * c7_lit ** 3.78613 * (h["T"] / h["B"]) ** 1.07961 \
        * (90.0 - ie) ** (-1.37565)
    assert r["c1"] == pytest.approx(c1_lit, rel=2e-3)


# ---------------------------------------------------------------------------
# 3. frozen physics pins (paper-literal re-derivation, cross-check tool)
# ---------------------------------------------------------------------------
PINS = {
    ("KVLCC2", 0.142): dict(RT_N=1919681.0, RW_N=44303.0),
    ("KCS", 0.21): dict(RT_N=915110.0, RW_N=5945.0),
    ("S60-Cb060", 0.22): dict(RT_N=168487.0, RW_N=157.0),
}


@pytest.mark.parametrize("case", sorted(PINS))
def test_frozen_physics_pins(case):
    hull, fn = case
    h = HULLS[hull]
    V = fn * math.sqrt(G * h["L"])
    b = dt(V, h["L"], h["B"], h["T"], h["Cb"], rho=1025.0, nu=1.188e-6)
    for k, want in PINS[case].items():
        assert b[k] == pytest.approx(want, rel=2e-3), (k, b[k], want)


# ---------------------------------------------------------------------------
# 4. the cross-check tool itself stays green (and keeps its negative control)
# ---------------------------------------------------------------------------
def test_crosscheck_tool_passes_and_legacy_control_still_fails():
    out = subprocess.run([sys.executable, str(REPO / "tools" / "verify"
                                             / "holtrop_crosscheck.py"),
                          "--print-only"],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "CROSSCHECK_PASS" in out.stdout
    assert "legacy=" in out.stdout
    legacy = float(out.stdout.split("legacy=")[1].split()[0])
    assert legacy > 2.0            # the pre-m1648 copy is still rejected