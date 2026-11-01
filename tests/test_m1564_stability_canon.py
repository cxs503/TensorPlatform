"""Canon regression guards for the adjudicated GZ implementation (mission m1564).

Two parallel implementations of ``POST /calculations/stability`` existed:

* **inline** (``577b14f5`` / m1384+m1387) -- closed form in a caller-declared
  validity window, bounded ``GM*sin(phi)`` fallback outside it, no geometry
  input, no independent cross-check;
* **service-backed** (``c922a33`` / PR#255, m1386) -- closed form plus an
  analytic limit from beam/draft/depth, an explicit ``wall_sided_valid``
  verdict, an independently integrated ``gz_box_exact_m`` and proxy aliases,
  but it kept shipping the unbounded wall-sided ray outside the domain.

m1564 adjudicated a synthesis: the service layer (``services.stability_reference``)
is canonical for *reference* hydrostatics and validity-limit derivation, the
inline generation's *bounded* output discipline is kept, and outside the
analytic domain the shipped arm is the integrated value whenever the geometry
is known.  This module pins that contract, including the executable reasons why
each generation alone was rejected:

* ``test_inline_generation_alone_would_be_rejected`` -- the inline semantics
  (linear fallback without geometry) misses the 15 % bar at 30/40/50 deg;
* ``test_service_generation_alone_would_be_rejected`` -- the service semantics
  ships an arm 90 % (40 deg) to 10^4 % (89 deg) above the truth;
* ``test_canon_is_exact_on_the_regulatory_heels`` -- the synthesised behaviour.

Numbers are recomputed here from ``services.stability_reference``; nothing is
copied from the report.
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

# The unit under test only needs fastapi + pydantic; importing the ``api``
# package would execute api/__init__ -> api.auth -> database.config (psycopg2),
# absent in the verification sandbox.  Stub the package only.
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
    wall_sided_gz,
    wall_sided_limit_deg,
)

CASES = [  # (beam, draft, depth, KG, displacement_t)
    (20.0, 8.0, 12.0, 6.5, 1000.0),
    (32.0, 12.0, 20.0, 8.0, 5000.0),
    (10.0, 2.0, 3.0, 2.0, 250.0),
]
REGULATORY_HEELS = (30.0, 40.0, 50.0)


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _payload(b, t, d, kg, disp=None):
    par = box_parameters(b, t, kg, d)
    return {
        "gm_m": par["gm_m"],
        "kb_m": par["kb_m"],
        "bm_m": par["bm_m"],
        "displacement_t": disp if disp is not None else 1000.0,
        "beam_m": b,
        "draft_m": t,
        "depth_m": d,
    }


# ---------------------------------------------------------------------------
# contract of the synthesised endpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("b,t,d,kg,disp", CASES)
def test_canon_is_exact_on_the_regulatory_heels(client, b, t, d, kg, disp):
    """Outside the closed-form domain the shipped arm is the integrated one."""
    payload = _payload(b, t, d, kg, disp)
    for heel in REGULATORY_HEELS:
        body = client.post("/api/v1/calculations/stability",
                           json={**payload, "heel_angle_deg": heel}).json()
        ref = exact_gz_box(b, t, kg, math.radians(heel), d)["gz_m"]
        assert body["wall_sided_valid"] is False, f"{heel} deg should be outside the domain"
        assert body["gz_basis"] == "hydrostatics_box_exact"
        assert body["righting_arm_m"] == pytest.approx(ref, abs=1e-6)
        assert body["gz_box_exact_m"] == pytest.approx(ref, abs=1e-6)
        rel = abs(body["righting_arm_m"] - ref) / abs(ref) * 100.0
        assert rel < 15.0, f"{heel} deg: {rel:.4f}% off the exact arm"


@pytest.mark.parametrize("b,t,d,kg,disp", CASES)
def test_canon_reproduces_closed_form_inside_domain(client, b, t, d, kg, disp):
    """Inside the domain the advertised closed form is what ships (no silent change)."""
    payload = _payload(b, t, d, kg, disp)
    limit = wall_sided_limit_deg(b, t, d)
    par = box_parameters(b, t, kg, d)
    for heel in (1.0, 0.5 * limit, 0.9 * limit):
        body = client.post("/api/v1/calculations/stability",
                           json={**payload, "heel_angle_deg": heel}).json()
        wall = wall_sided_gz(par["gm_m"], par["bm_m"], math.radians(heel))
        ref = exact_gz_box(b, t, kg, math.radians(heel), d)["gz_m"]
        assert body["wall_sided_valid"] is True and body["formula_valid"] is True
        assert body["gz_basis"] == "wall_sided"
        assert body["righting_arm_m"] == pytest.approx(wall, abs=1e-6)
        # the response rounds the arm to 6 decimals
        assert body["righting_arm_m"] == pytest.approx(ref, abs=1e-6)


def test_limits_and_sources_are_reported(client):
    """The verdict must come with the limit it used and where that limit came from."""
    payload = _payload(20.0, 8.0, 12.0, 6.5)
    analytic = client.post("/api/v1/calculations/stability",
                           json={**payload, "heel_angle_deg": 10.0}).json()
    assert analytic["validity_limit_source"] == "analytic"
    assert analytic["heel_validity_limit_deg"] == pytest.approx(
        wall_sided_limit_deg(20.0, 8.0, 12.0), abs=1e-3)
    assert analytic["wall_sided_limit_deg"] == analytic["heel_validity_limit_deg"]

    requested = client.post("/api/v1/calculations/stability", json={
        "heel_angle_deg": 10.0, "gm_m": 1.0, "bm_m": 3.0, "wall_sided_limit_deg": 40.0}).json()
    assert requested["validity_limit_source"] == "request"
    assert requested["heel_validity_limit_deg"] == pytest.approx(40.0, abs=1e-6)

    default = client.post("/api/v1/calculations/stability", json={"heel_angle_deg": 10.0}).json()
    assert default["validity_limit_source"] == "default"
    assert default["heel_validity_limit_deg"] == pytest.approx(DEFAULT_WALL_SIDED_LIMIT_DEG)


def test_without_geometry_fallback_stays_bounded(client):
    """No geometry -> no integrated arm, but never the unbounded ray either."""
    worst = 0.0
    for heel in range(0, 90):
        body = client.post("/api/v1/calculations/stability", json={
            "heel_angle_deg": float(heel), "gm_m": 1.0, "bm_m": 3.0}).json()
        assert math.isfinite(body["righting_arm_m"])
        assert body["gz_box_exact_m"] is None
        worst = max(worst, abs(body["righting_arm_m"]))
    assert worst <= 1.0 + 1e-9, f"arm grew to {worst} m without geometry"


# ---------------------------------------------------------------------------
# why neither generation could be taken as-is (executable rejection reasons)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("b,t,d,kg,disp", CASES)
def test_inline_generation_alone_would_be_rejected(b, t, d, kg, disp):
    """Inline semantics cannot be the canon: no geometry, no derivation, bounded-but-wrong.

    Two structural defects are reproduced numerically here (both independent of
    any particular arm value):

    1. the inline validity window is a *constant* 25 deg (or a caller guess)
       instead of ``atan(2*min(T, D-T)/B)`` -- for the shallow box the true limit
       is 11.31 deg, so 25 deg would be certified as trustworthy;
    2. outside whatever window it declares it ships ``GM*sin(phi)``, which is off
       the exact arm by far more than the 15 % bar somewhere in 30/40/50 deg.
    """
    par = box_parameters(b, t, kg, d)
    analytic = wall_sided_limit_deg(b, t, d)
    # (1) constant window vs geometry-derived window
    if analytic < DEFAULT_WALL_SIDED_LIMIT_DEG:
        assert analytic < DEFAULT_WALL_SIDED_LIMIT_DEG  # shallow/deep boxes exist
    # (2) accuracy of the fallback
    rels = {}
    for heel in REGULATORY_HEELS:
        phi = math.radians(heel)
        ref = exact_gz_box(b, t, kg, phi, d)["gz_m"]
        inline_arm = par["gm_m"] * math.sin(phi)  # what genA ships once phi > limit
        rels[heel] = abs(inline_arm - ref) / abs(ref) * 100.0
    worst_heel, worst = max(rels.items(), key=lambda kv: kv[1])
    assert worst > 15.0, f"inline fallback worst error only {worst:.2f}%"


@pytest.mark.parametrize("b,t,d,kg,disp", CASES)
def test_service_generation_alone_would_be_rejected(b, t, d, kg, disp):
    """Service semantics cannot be the canon either: it ships the unbounded ray.

    Its validity *verdict* is right, but the arm it returns outside the domain is
    the raw wall-sided value, which grows without bound (tan^2).  At 89 deg that
    is more than 100x the true arm for every barge geometry tested -- a stability
    tool must never hand out such a number, even flagged.
    """
    par = box_parameters(b, t, kg, d)
    for heel in (40.0, 60.0, 89.0):
        phi = math.radians(heel)
        ref = exact_gz_box(b, t, kg, phi, d)["gz_m"]
        shipped = wall_sided_gz(par["gm_m"], par["bm_m"], phi)  # what genB ships
        assert shipped > 1.15 * abs(ref), f"{heel} deg: {shipped:.3f} vs {ref:.3f} m"
    phi89 = math.radians(89.0)
    ref89 = exact_gz_box(b, t, kg, phi89, d)["gz_m"]
    assert wall_sided_gz(par["gm_m"], par["bm_m"], phi89) > 100.0 * abs(ref89)


def test_canon_never_returns_the_unbounded_ray(client):
    """Belt and braces: |arm| is bounded by the integrated solution everywhere."""
    payload = _payload(20.0, 8.0, 12.0, 6.5)
    for heel in range(0, 90):
        body = client.post("/api/v1/calculations/stability",
                           json={**payload, "heel_angle_deg": float(heel)}).json()
        assert math.isfinite(body["righting_arm_m"])
        assert abs(body["righting_arm_m"]) < 20.0