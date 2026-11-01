"""Verification of the merged GZ / righting-arm capability (mission m1386).

The merged ``POST /calculations/stability`` endpoint evaluates the wall-sided
identity

    GZ(phi) = sin(phi) * (GM + 0.5 * BM * tan(phi)**2)

This suite verifies it against an **independently integrated** hydrostatic
reference (``services.stability_reference.exact_gz_box``): the exact
constant-displacement righting arm of a rectangular box barge, obtained by
polygon-clipping the heeled section against a floating waterline.

Two kinds of assertions:

* **agreement inside the analytic domain** -- proves the shipped formula and
  its implementation are correct where they claim to be;
* **quantified failure outside it** -- proves the old behaviour (no validity
  verdict, arm growing to 1e32 m near 90 deg) is caught, so the new gate can
  actually fail (QUALITY_BAR R1: a gate that can never fail is not a gate).
"""
from __future__ import annotations

import math
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, _BACKEND)

# The unit under test only needs fastapi + pydantic, but importing the ``api``
# package executes api/__init__ -> api.auth -> database.config, which requires
# psycopg2/cryptography (absent in the verification sandbox).  Registering a
# bare package stub keeps this suite importable everywhere; nothing else is
# stubbed.
if "api" not in sys.modules:
    import types

    _pkg = types.ModuleType("api")
    _pkg.__path__ = [os.path.join(_BACKEND, "api")]
    sys.modules["api"] = _pkg

from api.calculations import (  # noqa: E402
    DEFAULT_WALL_SIDED_LIMIT_DEG,
    router,
)
from services.stability_reference import (  # noqa: E402
    box_parameters,
    exact_gz_box,
    gz_error_envelope,
    max_true_gz,
    wall_sided_gz,
    wall_sided_limit_deg,
)

# (beam, draft, depth, KG) -- a full-form barge, a slender full-scale hull and
# a shallow-draft box, spanning the realistic spread of 2*min(T, D-T)/B.
CASES = [
    (20.0, 8.0, 12.0, 6.5),
    (32.0, 12.0, 20.0, 8.0),
    (10.0, 2.0, 3.0, 2.0),
]


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Phase 1 — reference solution self-checks (the benchmark must be trustworthy)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("beam,draft,depth,kg", CASES)
def test_displacement_is_conserved_at_every_heel(beam, draft, depth, kg):
    """Constant-volume heel: the clipped immersed area must equal B*T exactly."""
    for heel in (0.0, 5.0, 17.5, 35.0, 60.0, 85.0):
        section = exact_gz_box(beam, draft, kg, math.radians(heel), depth)
        assert section["immersed_area_m2"] == pytest.approx(beam * draft, rel=1e-10)


@pytest.mark.parametrize("beam,draft,depth,kg", CASES)
def test_small_angle_limit_reproduces_gm(beam, draft, depth, kg):
    """GZ -> GM*sin(phi) as phi -> 0 (independent check of the reference sign/scale)."""
    gm = box_parameters(beam, draft, kg, depth)["gm_m"]
    phi = math.radians(1.0)
    gz = exact_gz_box(beam, draft, kg, phi, depth)["gz_m"]
    assert gz == pytest.approx(gm * math.sin(phi), rel=1e-3)
    assert exact_gz_box(beam, draft, kg, 0.0, depth)["gz_m"] == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Phase 2 — the merged closed form vs the reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("beam,draft,depth,kg", CASES)
def test_wall_sided_matches_reference_inside_domain(beam, draft, depth, kg):
    """Inside atan(2*min(T, D-T)/B) the shipped formula must be exact."""
    par = box_parameters(beam, draft, kg, depth)
    limit = wall_sided_limit_deg(beam, draft, depth)
    for heel in (0.5, 1.0, 5.0, 10.0, 0.5 * limit, 0.9 * limit, limit - 1e-6):
        phi = math.radians(heel)
        exact = exact_gz_box(beam, draft, kg, phi, depth)["gz_m"]
        wall = wall_sided_gz(par["gm_m"], par["bm_m"], phi)
        assert wall == pytest.approx(exact, rel=1e-7, abs=1e-9), f"{heel} deg"


@pytest.mark.parametrize("beam,draft,depth,kg", CASES)
def test_error_grows_past_15pct_outside_domain(beam, draft, depth, kg):
    """The 15 % accuracy bar is violated somewhere above the analytic limit."""
    limit = wall_sided_limit_deg(beam, draft, depth)
    rows = gz_error_envelope(beam, draft, kg, depth_m=depth)
    breaches = [r for r in rows if abs(r["rel_err_pct"]) > 15.0]
    assert breaches, "wall-sided form must break the 15% bar somewhere"
    first = breaches[0]
    assert first["heel_deg"] > limit, f"deviation starts at {first['heel_deg']} < limit {limit}"
    assert first["heel_deg"] <= 60.0, "divergence should appear well before 60 deg"


@pytest.mark.parametrize("beam,draft,depth,kg", CASES)
def test_true_gz_stays_finite_while_closed_form_diverges(beam, draft, depth, kg):
    """Exact righting arm is bounded; tan(phi)**2 makes the closed form explode."""
    par = box_parameters(beam, draft, kg, depth)
    peak = max_true_gz(beam, draft, kg, depth, step_deg=0.25)
    assert 0.0 < peak["gz_max_m"] < depth  # a sane lever for a barge
    exact_80 = abs(exact_gz_box(beam, draft, kg, math.radians(80.0), depth)["gz_m"])
    wall_80 = wall_sided_gz(par["gm_m"], par["bm_m"], math.radians(80.0))
    assert wall_80 > 10.0 * exact_80, "closed form must over-predict grossly at 80 deg"


# ---------------------------------------------------------------------------
# Phase 3 — endpoint behaviour and regression gates
# ---------------------------------------------------------------------------

def test_default_request_unchanged(client):
    """Backward compatibility: the pre-existing call returns the same arm."""
    r = client.post("/api/v1/calculations/stability", json={})
    assert r.status_code == 200
    body = r.json()
    expected = math.sin(math.radians(10.0)) + 0.5 * 3.0 * math.tan(math.radians(10.0)) ** 2 \
        * math.sin(math.radians(10.0))
    assert body["righting_arm_m"] == pytest.approx(expected, abs=1e-6)
    assert body["righting_moment_knm"] == pytest.approx(
        1000 * 1000 * 9.81 * body["righting_arm_m"] / 1000, rel=1e-5)
    assert body["gm_effective_m"] == 1.0
    # no geometry supplied -> conservative default verdict, still inside it at 10 deg
    assert body["wall_sided_valid"] is True
    assert body["heel_validity_limit_deg"] == pytest.approx(DEFAULT_WALL_SIDED_LIMIT_DEG)
    assert body["gz_box_exact_m"] is None


@pytest.mark.parametrize("heel", [90.0, -90.0, 120.0, -120.0])
def test_singular_heel_is_rejected(client, heel):
    """Regression gate: tan(phi)**2 is singular at 90 deg; ~1e32 m arms were returned.

    ADJUDICATED (m1564): the inline generation kept the schema domain 0 <= phi <= 90
    (heel *magnitude*), so a *negative* heel is refused one layer earlier. What this
    gate pins is that no such request may ever produce an arm: 400 for the in-domain
    singular point, 422 for an out-of-schema heel.
    """
    r = client.post("/api/v1/calculations/stability", json={"heel_angle_deg": heel})
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert "out of range" in r.json()["detail"]


def test_default_limit_flags_excessive_heel(client):
    """Without geometry a 60 deg heel is still flagged invalid (default 25 deg)."""
    r = client.post("/api/v1/calculations/stability", json={"heel_angle_deg": 60.0})
    body = r.json()
    assert r.status_code == 200
    assert body["wall_sided_valid"] is False
    assert "OUTSIDE" in body["validity_note"]


def test_geometry_gives_analytic_limit_and_exact_crosscheck(client):
    """B=20, T=8, D=12, KG=6.5 -> limit atan(16/20)=38.66.../... deg, GM=1.667."""
    kg = 6.5  # KG inferred by the endpoint as KB + BM - GM
    payload = {
        "displacement_t": 1000.0,
        "gm_m": 1.6666667,
        "kb_m": 4.0,
        "bm_m": 20.0 ** 2 / (12.0 * 8.0),
        "beam_m": 20.0,
        "draft_m": 8.0,
        "depth_m": 12.0,
    }
    ok = client.post("/api/v1/calculations/stability", json={**payload, "heel_angle_deg": 20.0}).json()
    bad = client.post("/api/v1/calculations/stability", json={**payload, "heel_angle_deg": 45.0}).json()
    limit = wall_sided_limit_deg(20.0, 8.0, 12.0)
    assert ok["heel_validity_limit_deg"] == pytest.approx(limit, abs=1e-3)
    assert ok["wall_sided_valid"] is True and bad["wall_sided_valid"] is False
    # cross-check arm is the independently integrated box solution
    ref = exact_gz_box(20.0, 8.0, kg, math.radians(45.0), 12.0)["gz_m"]
    assert bad["gz_box_exact_m"] == pytest.approx(ref, abs=1e-5)
    # ADJUDICATED (m1564): the closed form is demonstrably >15% off at 45 deg ...
    par = box_parameters(20.0, 8.0, kg, 12.0)
    wall = wall_sided_gz(par["gm_m"], par["bm_m"], math.radians(45.0))
    assert wall > ref * 1.15, "wall-sided form must over-predict grossly outside its domain"
    # ... and, since m1564, the endpoint ships the integrated arm instead of that ray
    # (inline generation returned the unbounded wall-sided value here).
    assert bad["righting_arm_m"] == pytest.approx(ref, abs=1e-5)
    assert bad["gz_basis"] == "hydrostatics_box_exact"


def test_proxy_style_aliases_accepted(client):
    """``services/ship_proxy`` posts beam/draft/displacement; they must bind."""
    r = client.post("/api/v1/calculations/stability", json={
        "beam": 20.0, "draft": 8.0, "depth": 12.0, "displacement": 5000.0,
        "gm_m": 1.6666667, "kb_m": 4.0, "bm_m": 41.6666667, "heel_angle_deg": 15.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["heel_validity_limit_deg"] == pytest.approx(wall_sided_limit_deg(20.0, 8.0, 12.0), abs=1e-3)
    assert body["righting_moment_knm"] == pytest.approx(
        5000 * 1000 * 9.81 * body["righting_arm_m"] / 1000, rel=1e-5)


def test_invalid_geometry_is_rejected(client):
    """depth <= draft has no freeboard to heel into -> 422 (field) or 400 (physics)."""
    r = client.post("/api/v1/calculations/stability", json={
        "beam_m": 20.0, "draft_m": 8.0, "depth_m": 8.0, "heel_angle_deg": 10.0,
    })
    assert r.status_code in (400, 422)

def test_validity_flag_is_not_hardwired(client, monkeypatch):
    """Mutation probe (QUALITY_BAR R1): the verdict must follow the limit, not a constant."""
    import api.calculations as calc

    payload = {
        "gm_m": 1.6666667, "kb_m": 4.0, "bm_m": 20.0 ** 2 / (12.0 * 8.0),
        "beam_m": 20.0, "draft_m": 8.0, "depth_m": 12.0, "heel_angle_deg": 60.0,
    }
    assert client.post("/api/v1/calculations/stability", json=payload).json()["wall_sided_valid"] is False
    monkeypatch.setattr(calc, "wall_sided_limit_deg", lambda b, t, d=None: 89.0)
    assert client.post("/api/v1/calculations/stability", json=payload).json()["wall_sided_valid"] is True
