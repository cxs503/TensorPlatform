"""Verification-debt guard for the platform transverse-stability (GZ) model.

Two layers:
  * the independent exact references must be self-consistent (voxel volume
    integration vs closed-form wall-sided cross curve, hydrostatics vs quadrature);
  * the recorded audit snapshot must keep reproducing the documented verdicts,
    so that any change of the platform GZ endpoint shows up as a test failure.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "verification", "gz_audit"))

import exact_gz as eg  # noqa: E402

ARTIFACT = os.path.join(ROOT, "artifacts", "gz_audit", "gz_audit.json")


def _load():
    if not os.path.exists(ARTIFACT):
        pytest.skip("audit snapshot missing: run tools/verification/gz_audit/run_audit.py")
    with open(ARTIFACT) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# reference self-consistency
# ---------------------------------------------------------------------------
def test_voxel_cross_curves_match_closed_form_box():
    r = eg.selftest_box()
    assert r["voxel_vs_closed_form_max_rel_err"] < 0.02
    assert math.isclose(r["V0_m3"], r["V0_analytic_m3"], rel_tol=1e-9)


def test_parabolic_hydrostatics_quadrature():
    hp = eg.parabolic_hydrostatics(100.0, 20.0, 8.0)
    assert math.isclose(hp["Cb"], 2.0 / 3.0, rel_tol=5e-3)
    assert math.isclose(hp["KB"], 4.0, rel_tol=1e-12)
    # BM = I/V with waterplane inertia I = (2/3) * int b^3 dx
    assert hp["BM"] > 0 and math.isclose(hp["KM"], hp["KB"] + hp["BM"], rel_tol=1e-12)


def test_wall_sided_small_angle_reduces_to_gm_sin():
    B, T, KG = 20.0, 10.0, 7.0
    hs = eg.box_hydrostatics(B, T)
    gm = hs["KM"] - KG
    for a in (0.5, 1.0, 2.0):
        gz = eg.wall_sided_gz(a, hs["KB"], hs["BM"], KG)
        assert abs(gz - gm * math.sin(math.radians(a))) < 5e-3


def test_box_deck_edge_angle():
    assert math.isclose(eg.box_deck_edge_angle(20.0, 10.0, 20.0), 45.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# snapshot: documented verdicts on the platform model
# ---------------------------------------------------------------------------
def _case(data, name):
    for c in data["cases"]:
        if c["case"] == name:
            return c
    raise AssertionError(f"case {name} not in snapshot")


def test_small_angle_accuracy_within_acceptance():
    """0-30 deg: the platform model stays inside the 15% acceptance band."""
    data = _load()
    for c in data["cases"]:
        dang = [(a, e) for a, e in zip(c["angles"], c["gz_err_norm_of_GZmax"]) if 5 <= a <= 30]
        assert max(e for _, e in dang) < 0.15, (c["case"], dang)


def test_large_angle_error_documented_over_15pct():
    """Beyond ~35-40 deg the model departs from the exact wall-sided cross curve."""
    data = _load()
    for c in data["cases"]:
        over = set(c["angles_over_15pct_norm"])
        assert max(over) >= 40.0
        assert any(a >= 40.0 for a in over), c["case"]


def test_area_0_40_under_predicted():
    """Righting-energy areas feeding IMO criteria are under-predicted."""
    data = _load()
    for c in data["cases"]:
        rel = (c["area_0_40_platform"] - c["area_0_40_reference"]) / c["area_0_40_reference"]
        assert rel < 0, c["case"]
        assert abs(rel) > 0.10, (c["case"], rel)


def test_gz_never_negative_due_to_clipping():
    """Characterisation of the deployed model: negative righting arms are clipped."""
    data = _load()
    for c in data["cases"]:
        assert min(c["gz_platform"]) >= 0.0


def test_declared_displacement_is_ignored():
    data = _load()
    assert data["displacement_invariance"]["gz_identical"] is True


def test_platform_kn_replicated_by_documented_formula():
    """The snapshot must indeed come from the transcribed KN formula (no drift)."""
    data = _load()
    for c in data["cases"]:
        assert c["kn_model_check_max_dev"] < 1e-3


def test_km_error_documented():
    data = _load()
    box = _case(data, "box_barge_Cb1.0")
    assert box["model_KB"] > box["exact_KB_first_principles"]  # KB = T(0.5+0.05Cb) over-predicts
    assert abs(box["model_KB"] - 5.5) < 1e-9
