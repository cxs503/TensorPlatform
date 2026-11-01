"""m1696 regression: response-moment frequency-window truncation (seakeeping).

Adjudicates the debt declared in m1645 §局限①: ``response_statistics`` (and the
fatigue module's ``m4``) integrate RAO^2 * S over the caller's frequency window
only.  Measured here against an ultra-wide reference of the same implementation:

* ``Tz_*`` (ratio m0/m2) is *window-immune*: |bias| < 1e-4 relative.
* ``significant_*`` / ``sigma_*`` (sqrt of m0 alone) carries a one-sided
  +0.27 % bias on the production grid (omega 0.10-2.00 rad/s, 191 points),
  because the spectrum is rescaled so that int S == Hs^2/16 on that window while
  the omitted high-frequency tail is integrated at RAO^2 < 1.
* The bias is pure truncation: it falls monotonically with omega_max and is
  < 1e-4 once omega_max >= 8 rad/s.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services import seakeeping as sk  # noqa: E402

L, B, T = 320.0, 58.0, 20.8
CB, CWP = 0.8098, 0.8700
DISP = 320_000.0
KG, GM_T = 18.5, 5.00

MODES = ("heave", "pitch", "roll")
PROD_OMEGA = sk.uniform_grid(0.10, 2.00, 191)
REF_OMEGA = sk.uniform_grid(0.01, 20.0, 20001)

CASES = {
    "head": dict(kind="jonswap", heading=180.0, speed=0.0),
    "itto_head": dict(kind="ittc", heading=180.0, speed=0.0),
    "bow_quarter": dict(kind="jonswap", heading=135.0, speed=8.0),
    "beam": dict(kind="jonswap", heading=90.0, speed=0.0),
    "following": dict(kind="jonswap", heading=0.0, speed=8.0),
}


def _model() -> "sk.SeakeepingModel":
    return sk.SeakeepingModel(L=L, B=B, T=T, Cb=CB, Cwp=CWP,
                              displacement_t=DISP, KG=KG, GM_T=GM_T)


def _stats(omega, kind, heading, speed, Hs=6.0, Tp=11.0):
    spec = sk.spectrum(kind, Hs, Tp, omega)
    return sk.response_statistics(_model(), spec, heading_deg=heading, speed=speed)


def _pair(tag, prod_omega=PROD_OMEGA, ref_omega=REF_OMEGA):
    kw = CASES[tag]
    return _stats(prod_omega, **kw), _stats(ref_omega, **kw)


def test_tz_is_window_immune():
    """Periods (m0/m2 ratios) must not move when the window is widened."""
    for tag in CASES:
        prod, ref = _pair(tag)
        for mode in MODES:
            key = f"Tz_{mode}_s"
            a, b = prod[key], ref[key]
            if not math.isfinite(b):
                assert not math.isfinite(a), (tag, key)
                continue
            assert abs(a - b) / b < 1e-4, (tag, key, a, b)


def test_significant_amplitude_bias_is_positive_and_bounded():
    """sqrt(m0) is biased high by window truncation, but bounded well under 3 %."""
    seen = 0
    for tag in CASES:
        prod, ref = _pair(tag)
        for mode in MODES:
            key = f"significant_{mode}"
            a, b = prod[key], ref[key]
            if not math.isfinite(b):
                assert not math.isfinite(a), (tag, key)  # beam-sea roll stays honest nan
                continue
            rel = (a - b) / b
            seen += 1
            assert rel > 0.0, (tag, key, rel)          # one-sided: tail omitted at RAO^2<1
            assert rel < 0.01, (tag, key, rel)         # and small
    assert seen >= 10


def test_vertical_acceleration_bias_bounded():
    for tag in CASES:
        prod, ref = _pair(tag)
        rel = (prod["sigma_vertical_acc_m_s2"] - ref["sigma_vertical_acc_m_s2"]) / \
            ref["sigma_vertical_acc_m_s2"]
        assert 0.0 < rel < 0.01, (tag, rel)


def test_bias_decays_monotonically_with_window():
    """The deficit is truncation, not model error: it collapses as omega_max grows."""
    vals = []
    for omega_max in (1.0, 1.5, 2.0, 3.0, 4.0, 8.0):
        prod, ref = _pair("head", prod_omega=sk.uniform_grid(0.10, omega_max, 1201))
        vals.append((omega_max,
                     (prod["significant_heave"] - ref["significant_heave"])
                     / ref["significant_heave"]))
    for (w0, r0), (w1, r1) in zip(vals, vals[1:]):
        assert 0.0 <= r1 < r0, (w0, r0, w1, r1)      # strictly decaying, one-sided
    assert vals[0][1] < 0.05                          # narrow window: still small
    assert vals[-1][1] < 1e-4                          # wide window: converged


def test_reference_grid_is_converged():
    """Doubling the reference-resolution must not move the reference itself."""
    a = _stats(REF_OMEGA, **CASES["head"])["significant_heave"]
    b = _stats(sk.uniform_grid(0.01, 20.0, 40001), **CASES["head"])["significant_heave"]
    assert abs(a - b) / b < 1e-6