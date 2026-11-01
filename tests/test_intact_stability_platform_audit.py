"""Platform-facing audit of the intact-stability endpoint (verification debt QR-INTACT).

Companion to ``tests/test_intact_stability_baseline.py``.  This module *requires* the
ship platform to be reachable (``SHIP_PLATFORM_URL``, default ``http://127.0.0.1:8001``);
it skips cleanly when the service is down so unit CI stays hermetic.

Two families of assertions:

* ``test_*_green`` / attribution tests: encode what the audit positively established
  (the endpoint's GZ curve is exactly ``(GM + 0.5*BM*sin^2(phi))*sin(phi)`` with a
  ``(60-phi)/20`` taper above 40 deg, so the defects are attributed, not guessed).
* ``xfail(strict=False)`` tests: encode the *physical requirement* the endpoint currently
  violates (box hydrostatics, 3% GZ, freeboard response, falsifiable IMO verdicts).
  They are marked expected-fail with the recorded deviation so CI stays green while the
  debt is documented; once the ship side is fixed they flip to XPASS, which is how the
  fix gets noticed rather than silently absorbed.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VERIFY = _ROOT / "tools" / "verify"
if str(_VERIFY) not in sys.path:
    sys.path.insert(0, str(_VERIFY))

import intact_stability_baseline as bl  # noqa: E402

BASE = os.getenv("SHIP_PLATFORM_URL", "http://127.0.0.1:8001")
ENDPOINT = f"{BASE.rstrip('/')}/api/v1/calculations/stability"

BARGE = {"length": 100.0, "beam": 20.0, "depth": 10.0, "draft": 6.0, "cb": 1.0,
         "kg": 5.0, "displacement": 12000.0}
LOW_FREEBOARD = dict(BARGE, depth=6.3)


def _call(payload):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _probe():
    try:
        return _call(BARGE)
    except (urllib.error.URLError, OSError, ValueError) as exc:  # pragma: no cover
        pytest.skip(f"ship platform unreachable at {ENDPOINT}: {exc}", allow_module_level=True)


API = _probe()


def _platform_curve(gm, bm, angles):
    out = []
    for a in angles:
        p = math.radians(a)
        gz = (gm + 0.5 * bm * math.sin(p) ** 2) * math.sin(p)
        if a > 40:
            gz *= (60.0 - a) / 20.0
        out.append(max(0.0, gz))
    return out


# ---------------------------------------------------------------------------
# Attribution: does the endpoint really use that formula?
# ---------------------------------------------------------------------------
def test_attribution_gzmax():
    """API GZmax equals the reconstructed formula value -> the algorithm is identified."""
    angles = [float(a) for a in range(0, 61, 5)]
    curve = _platform_curve(API["gm"], API["bm"], angles)
    assert max(curve) == pytest.approx(API["gz_max"], rel=0.005)
    assert angles[curve.index(max(curve))] == pytest.approx(API["gz_max_angle"])


def test_attribution_areas():
    angles = [float(a) for a in range(0, 61, 5)]
    areas = bl.imo_areas(angles, _platform_curve(API["gm"], API["bm"], angles))
    assert areas["area_0_30"] == pytest.approx(API["area_0_30"], rel=0.01)
    assert areas["area_30_40"] == pytest.approx(API["area_30_40"], rel=0.01)
    assert areas["area_0_40"] == pytest.approx(API["area_0_40"], rel=0.01)


def test_attribution_taper_forces_vanishing_angle():
    """The 40-60 deg taper makes GZ(60 deg)=0 and the vanishing angle a constant 60 deg."""
    assert API["vanishing_angle"] == 60.0
    curve = _platform_curve(API["gm"], API["bm"], [60.0])
    assert curve[0] == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Physical requirements currently violated (documented verification debt)
# ---------------------------------------------------------------------------
def test_box_hydrostatics_within_2pct():
    B, T, KG = BARGE["beam"], BARGE["draft"], BARGE["kg"]
    assert API["kb"] == pytest.approx(bl.box_kb(T), rel=0.02)
    assert API["bm"] == pytest.approx(bl.box_bm(B, T), rel=0.02)
    assert API["gm"] == pytest.approx(bl.box_gm(B, T, KG), rel=0.02)


def test_gz_within_3pct_up_to_downflooding():
    B, D, T, KG = BARGE["beam"], BARGE["depth"], BARGE["draft"], BARGE["kg"]
    phi_f = bl.flooding_angle_deg(B, D, T)
    angles = [float(a) for a in range(5, 61, 5) if a <= phi_f]
    curve = _platform_curve(API["gm"], API["bm"], angles)
    for a, gp in zip(angles, curve):
        ge = bl.box_gz(B, D, T, KG, a)
        assert abs(gp - ge) / ge <= 0.03, f"phi={a}deg: platform {gp:.4f} vs exact {ge:.4f}"


def test_gzmax_within_5pct_of_physical_curve():
    B, D, T, KG = BARGE["beam"], BARGE["depth"], BARGE["draft"], BARGE["kg"]
    ref = bl.barge_baseline(B, D, T, KG, open_top=True)
    assert API["gz_max"] == pytest.approx(ref["gz_max"], rel=0.05)


def test_endpoint_responds_to_freeboard():
    """Same B/T/KG, more freeboard -> materially different GZmax and curve end angle."""
    lo, hi = _call(LOW_FREEBOARD), _call(dict(BARGE, depth=20.0))
    assert abs(hi["gz_max"] - lo["gz_max"]) / max(lo["gz_max"], 1e-9) > 0.10
    assert hi["vanishing_angle"] - lo["vanishing_angle"] > 10.0


def test_imo_verdicts_are_falsifiable():
    """A 0.3 m-freeboard barge has a righting curve ending at ~1.7 deg: it must fail."""
    ref = bl.barge_baseline(BARGE["beam"], LOW_FREEBOARD["depth"], BARGE["draft"], BARGE["kg"],
                            open_top=True)
    assert sum(1 for v in ref["imo"].values() if not v) >= 4  # baseline: genuinely unsafe
    api = _call(LOW_FREEBOARD)
    fails = [k for k, v in api["imo_criteria"].items() if not v]
    assert fails, f"endpoint reports all criteria passed for an unsafe hull: {api['imo_criteria']}"


# mark the requirement tests as documented debt (see docs/reports/QR-INTACT-*.md)
_DEBT = pytest.mark.xfail(strict=False, reason="QR-INTACT documented verification debt (ship platform side)")
for _t in (test_box_hydrostatics_within_2pct, test_gz_within_3pct_up_to_downflooding,
           test_gzmax_within_5pct_of_physical_curve, test_endpoint_responds_to_freeboard,
           test_imo_verdicts_are_falsifiable):
    _DEBT(_t)