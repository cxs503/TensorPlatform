"""Tests for the independent hydrostatics reference + hydro-curves audit (m1576).

Two things are proven here:

* the reference integrator is *correct* (closed-form calibration on a box and on
  a wall-sided hull with a parabolic waterplane, plus scipy.quad on the curved
  wetted surface), and
* the audit criterion set is *not vacuous*: fed a hydrostatic table produced by
  the verified reference, every identity criterion passes; fed the platform
  snapshot, it fails. A criterion set that only ever rejects would be worthless.
"""
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier", "geometry"))

# The KCS hydro-curve capture used to live in artifacts/verifier/, which the
# merge gate strips; it now ships (versioned, checksummed) under tests/data/
# with the legacy location kept as a fallback for already-restored trees.
SNAP_CANDIDATES = [
    os.path.join(ROOT, "tests", "data", "verifier_assets", "hydrostatics"),
    os.path.join(ROOT, "artifacts", "verifier"),
]


def _load_snapshot():
    for d in SNAP_CANDIDATES:
        path = os.path.join(d, "hydro_curves_kcs_snapshot.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    pytest.fail(
        "hydro_curves_kcs_snapshot.json 缺失（候选: %s）。该文件是平台水曲线探针的"
        "存档证据，属版本化验证资产，不得删除；重采集: "
        "python3 tools/verifier/geometry/audit_hydro_curves.py --live" % SNAP_CANDIDATES)

from hydrostatics_reference import (  # noqa: E402
    Box, ParabolicWallSided, closed_form_box, closed_form_parabolic, hydrostatics,
)
import audit_hydro_curves as A  # noqa: E402


class VSection:
    """Flared hull: b(x, z) = (B/2)(1-u^2) sqrt(z/T).

    Draft-dependent Cb (unlike a wall-sided hull), so it exercises the
    "one fixed hull form, integrate below the waterline" property.
    """

    def __init__(self, L=230.0, B=32.2, T=10.8):
        self.L, self.B, self.T = L, B, T

    def half_breadth(self, x, z):
        u = 2.0 * np.asarray(x, float) / self.L - 1.0
        prof = 0.5 * self.B * np.clip(1.0 - u ** 2, 0.0, None)
        zz = np.clip(np.asarray(z, float), 0.0, None) / self.T
        return np.broadcast_to(prof, np.broadcast(x, z).shape) * np.sqrt(zz)


def _rel(a, b):
    return abs(a - b) / abs(b)


# --------------------------------------------------------------------------
# 1. calibration of the reference integrator
# --------------------------------------------------------------------------
def test_box_matches_closed_form():
    L, B, T = 230.0, 32.2, 10.8
    got, ref = hydrostatics(Box(L, B, T), T), closed_form_box(L, B, T, T)
    for k in ("nabla", "Cb", "Awp", "Cwp", "VCB", "LCB", "IT", "BM", "wet_surface"):
        assert _rel(got[k], ref[k]) < 5e-3, (k, got[k], ref[k])


def test_parabolic_wall_sided_matches_closed_form():
    L, B, T = 230.0, 32.2, 10.8
    got, ref = hydrostatics(ParabolicWallSided(L, B, T), T), closed_form_parabolic(L, B, T, T)
    # analytic: nabla = 2/3 LBT, Cb = Cwp = 2/3, KB = T/2, BM = 2B^2/(35T)
    for k in ("nabla", "Cb", "Awp", "Cwp", "VCB", "LCB", "IT", "BM"):
        assert _rel(got[k], ref[k]) < 5e-3, (k, got[k], ref[k])


def test_wetted_surface_curved_side_matches_quad():
    pytest.importorskip("scipy")
    import math
    from scipy.integrate import quad

    L, B, T = 230.0, 32.2, 10.8
    f = lambda x: math.sqrt(1.0 + (-0.5 * B * 4.0 * (2.0 * x / L - 1.0) / L) ** 2)  # noqa: E731
    exact = 2.0 * T * quad(f, 0.0, L, limit=400)[0] + (2.0 / 3.0) * L * B
    got = hydrostatics(ParabolicWallSided(L, B, T), T)["wet_surface"]
    assert _rel(got, exact) < 1e-3


def test_grid_convergence():
    h1 = hydrostatics(VSection(), 10.8, nx=401, nz=101)
    h2 = hydrostatics(VSection(), 10.8, nx=801, nz=201)
    assert _rel(h1["nabla"], h2["nabla"]) < 1e-3


# --------------------------------------------------------------------------
# 2. the physics the audited endpoint violates
# --------------------------------------------------------------------------
def test_fixed_hull_gives_draft_dependent_Cb():
    h = VSection()
    cb = [hydrostatics(h, d)["Cb"] for d in (2.7, 5.4, 8.1, 10.8)]
    assert all(b > a for a, b in zip(cb, cb[1:])), cb      # Cb must grow with draft
    assert cb[-1] < 1.0
    # ... whereas the platform's curves are draft-invariant (bug signature)
    snap = _load_snapshot()
    cbp = np.asarray(snap["curves"]["Cb"], float)
    assert np.ptp(cbp) < 1e-3, "endpoint Cb is exactly draft-invariant"


def test_reference_satisfies_dnabla_dT_equals_Awp():
    h = VSection()
    d1, d2 = 8.0, 9.0
    h1, h2 = hydrostatics(h, d1), hydrostatics(h, d2)
    slope = (h2["nabla"] - h1["nabla"]) / (d2 - d1)
    awp = hydrostatics(h, 0.5 * (d1 + d2))["Awp"]
    assert _rel(slope, awp) < 0.01, (slope, awp)


# --------------------------------------------------------------------------
# 3. criterion set is not degenerate (negative control) + live-snapshot verdict
# --------------------------------------------------------------------------
def _reference_curves(shape, n=21):
    T = shape.T
    drafts = [0.1 * T + i * (0.9 * T) / (n - 1) for i in range(n)]
    hs = [hydrostatics(shape, d) for d in drafts]
    return {"curves": {
        "drafts": [round(d, 3) for d in drafts],
        "displacement": [h["displacement_t"] for h in hs],
        "Cb": [h["Cb"] for h in hs],
        "Awp": [h["Awp"] for h in hs],
        "LCB": [h["LCB"] for h in hs],
        "VCB": [h["VCB"] for h in hs],
        "TPC": [1.025 * h["Awp"] / 100.0 for h in hs],
        "MTC": [0.0 for _ in hs],
        "wet_surface": [h["wet_surface"] for h in hs],
    }, "meta": {"L": shape.L, "B": shape.B, "T": T, "Cb": hs[-1]["Cb"]}}


def test_criteria_pass_on_a_physically_correct_table():
    snap = _reference_curves(VSection())
    res = A.audit(snap)
    ident = [c for c in res["criteria"] if c["id"].startswith("I")]
    assert all(c["pass"] for c in ident), [(c["id"], c["rel_err"]) for c in ident]


def test_platform_snapshot_fails_the_identities():
    snap = _load_snapshot()
    res = A.audit(snap)
    verdict = {c["id"]: c["pass"] for c in res["criteria"]}
    assert res["verdict"] == "FAIL"
    # impossible block coefficient, broken d(nabla)/dT = Awp, VCB/d = 0.91
    for cid in ("I1", "I3", "I4", "I7", "B1", "B2"):
        assert verdict[cid] is False, cid
    # endpoint is internally consistent where it must be, which is what
    # shows the failure is *physical*, not merely a formatting mismatch
    assert verdict["I2"] is True and verdict["I6"] is True


def test_snapshot_displacement_error_exceeds_gate():
    snap = _load_snapshot()
    res = A.audit(snap)
    b1 = [c for c in res["criteria"] if c["id"] == "B1"][0]
    assert b1["rel_err"] > 0.15 and b1["rel_err"] < 3.0   # gross, but a bug not noise