"""MOCK-fingerprint gate — detectors (offline) + live propeller endpoint guard.

Two layers:

1. **Detector unit tests** (offline, no network): synthetic response sets are fed
   through ``tools/mock_fingerprint`` — one set reproduces the defect pattern seen
   on the ship-performance open-water endpoint (``KQ = KT/10`` exactly,
   ``eta0 = 10J/2pi`` blind to P/D, Z, Ae/Ao), the other set is a healthy
   implementation driven by the independent Wageningen B-series regression in
   ``tools/ref_anchor_lib.py``.  The same detectors must fire on the first and stay
   quiet on the second, so the gate itself is proven before it judges the platform.

2. **Live guard**: probes ``SHIP_API_BASE`` (default http://127.0.0.1:8001/api/v1)
   and pins the current verdict.  The ``xfail(strict=True)`` test states the
   required end state (endpoint agrees with the B-series regression): it is
   expected to fail while the endpoint is synthetic, and it will flip to XPASS —
   i.e. become a hard failure — the moment the defect is fixed, forcing this file
   to be updated.  The complementary test pins the *defect* so it cannot silently
   be re-introduced after the fix.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ref_anchor_lib as ref  # noqa: E402
from tools.mock_fingerprint import (  # noqa: E402
    ENDPOINT,
    judge,
    parameter_blindness,
    physical_bound,
    probe,
    ratio_lock,
    reference_deviation,
)

J_SWEEP = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
DESIGNS = [
    {"Z": 4, "Ae_Ao": 0.40, "P_D": 1.0},
    {"Z": 4, "Ae_Ao": 0.70, "P_D": 1.0},
    {"Z": 5, "Ae_Ao": 0.75, "P_D": 1.4},
]


def _synthetic_endpoint(designs=DESIGNS, j_sweep=J_SWEEP):
    """Reproduce the defect fingerprint: KQ = KT/10, eta0 = 10J/(2 pi)."""
    out = []
    for d in designs:
        for J in j_sweep:
            kt = max(0.0, 0.27 * (1.0 - J / 1.35))  # shape is irrelevant to the detectors
            kq = kt / 10.0
            eta = 0.0 if kq == 0 else (J / (2 * math.pi)) * kt / kq
            out.append({"J": J, "design": d, "KT": kt, "KQ": kq, "eta0": eta})
    return out


def _healthy_endpoint(designs=DESIGNS, j_sweep=J_SWEEP):
    """Independent B-series regression: the behaviour a REAL endpoint must show."""
    out = []
    for d in designs:
        for J in j_sweep:
            pt = ref.open_water_point(J, P_D=d["P_D"], Ae_Ao=d["Ae_Ao"], Z=d["Z"])
            out.append({"J": J, "design": d, "KT": pt["KT"], "KQ": pt["KQ"],
                        "eta0": pt["eta0"]})
    return out


# ── Layer 1: detectors proven on synthetic responses ────────────────────
def test_ratio_lock_fires_on_synthetic_endpoint():
    recs = [r for r in _synthetic_endpoint() if r["J"] > 0 and r["KT"] > 0]
    d = ratio_lock(recs, "KQ", "KT")
    assert d["triggered"] is True
    assert d["ratio"] == pytest.approx(0.1, rel=1e-12)
    assert d["rel_spread"] <= 1e-12


def test_ratio_lock_quiet_on_b_series_regression():
    recs = [r for r in _healthy_endpoint() if r["J"] > 0]
    d = ratio_lock(recs, "KQ", "KT")
    assert d["triggered"] is False
    # the real series ratio must move: a 39/47-term regression cannot be a fixed ratio
    assert d["rel_spread"] > 0.05


def test_parameter_blindness_fires_on_synthetic_and_quiet_on_regression():
    syn = [{"P_D": r["design"]["P_D"], "eta0": r["eta0"]}
           for r in _synthetic_endpoint() if r["J"] == 0.5]
    d = parameter_blindness(syn, "P_D", "eta0")
    assert d["triggered"] is True, d

    healthy = [{"P_D": r["design"]["P_D"], "eta0": r["eta0"]}
               for r in _healthy_endpoint() if r["J"] == 0.5]
    dh = parameter_blindness(healthy, "P_D", "eta0")
    assert dh["triggered"] is False, dh
    assert dh["rel_spread"] > 0.05


def test_physical_bound_eta_below_one():
    syn = _synthetic_endpoint()
    d = physical_bound(syn, "eta0", 1.0)
    assert d["triggered"] is True
    assert d["worst"]["eta0"] > 1.0

    healthy = _healthy_endpoint()
    dh = physical_bound(healthy, "eta0", 1.0)
    assert dh["triggered"] is False, dh["violations"]


def test_reference_deviation_threshold_and_direction():
    syn = [r for r in _synthetic_endpoint() if r["design"]["Z"] == 4
           and r["design"]["Ae_Ao"] == 0.70]

    def rf(r):
        pt = ref.open_water_point(r["J"], P_D=1.0, Ae_Ao=0.70, Z=4)
        return {"KT": pt["KT"], "KQ": pt["KQ"]}

    d = reference_deviation(syn, rf, ("KT", "KQ"))
    assert d["triggered"] is True
    assert d["n_fail"] >= 1
    healthy = [r for r in _healthy_endpoint() if r["design"]["Z"] == 4
               and r["design"]["Ae_Ao"] == 0.70]
    dh = reference_deviation(healthy, rf, ("KT", "KQ"))
    assert dh["triggered"] is False, dh["worst"]


def test_judge_maps_structural_fingerprint_to_mock():
    syn = _synthetic_endpoint()
    dets = [ratio_lock([r for r in syn if r["J"] > 0 and r["KT"] > 0], "KQ", "KT"),
            physical_bound(syn, "eta0", 1.0)]
    j = judge(dets)
    assert j["level"] == "MOCK"
    assert "ratio_lock" in j["fired"]
    assert judge([{"name": "reference_deviation", "triggered": False}])["level"] == "REAL"


# ── Layer 2: live guard against the real endpoint ──────────────────────
def _live_ledger():
    try:
        led = probe()
    except Exception as exc:  # pragma: no cover - network dependent
        pytest.skip(f"ship-performance API unreachable: {exc!r}")
    ok = [r for rows in led["series"].values() for r in rows if r.get("KT") is not None]
    if not ok:
        pytest.skip("ship-performance API returned no propeller data")
    return led


def test_live_endpoint_defect_is_pinned():
    """The synthetic relation must stay visible until it is fixed (regression pin)."""
    led = _live_ledger()
    det = {d["name"] + ":" + str(d.get("expr") or d.get("metric")): d for d in led["detectors"]}
    lock = [d for d in led["detectors"] if d["name"] == "ratio_lock"][0]
    assert lock["triggered"] is True, (
        "ratio lock disappeared — if the endpoint now returns a genuine regression, "
        "delete this pin and keep test_live_endpoint_matches_b_series_regression")
    assert led["verdict"]["level"] == "MOCK", det


@pytest.mark.xfail(strict=True, reason=(
    "open-water endpoint returns KQ = KT/10 (design-blind eta0); fix belongs to the "
    "ship-performance-platform serving layer — this test flips to XPASS on repair"))
def test_live_endpoint_matches_b_series_regression():
    led = _live_ledger()
    d = [x for x in led["detectors"] if x["name"] == "reference_deviation"][0]
    assert d["n_fail"] == 0, (
        f"{d['n_fail']}/{d['n_points']} points deviate >{d['tolerance']:.0%} from the "
        f"B4-70 regression, worst {d['worst']}")


def test_live_endpoint_is_reachable():
    led = _live_ledger()
    assert led["endpoint"].endswith(ENDPOINT)