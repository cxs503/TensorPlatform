#!/usr/bin/env python3
"""
Regression tests for the wall-sided GZ formula and its validity domain.

Capability under test: ``calc.stability`` — POST /api/v1/calculations/stability.
Reference: exact 2-D hydrostatics of a rectangular section
(``tools/validation/wall_sided_gz_reference.py``) — volume-balanced waterline by
bisection + polygon-clipped centroid, independent of the closed form.

Acceptance criteria encoded below:
  * inside the wall-sided domain (phi <= atan(2(D-T)/B)) the closed form must match
    the exact solution to machine precision (rel <= 1e-6) -> the formula is REAL;
  * beyond deck-edge immersion the error must exceed the 15 % acceptability bound
    at 45 deg -> the formula is NOT valid there, and the endpoint must say so.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "validation"))

from wall_sided_gz_reference import BoxCase, exact_gz, wall_sided_gz  # noqa: E402


def _load_calculations():
    """Load backend/api/calculations.py without pulling in the whole api package."""
    spec = importlib.util.spec_from_file_location(
        "calculations_under_test", ROOT / "backend" / "api" / "calculations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calculations_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


CALC = _load_calculations()

CASES = [
    BoxCase(beam_m=20.0, draft_m=8.0, depth_m=12.0, kg_m=6.0),   # barge
    BoxCase(beam_m=16.0, draft_m=5.0, depth_m=6.0, kg_m=3.4),    # shallow, full
    BoxCase(beam_m=11.0, draft_m=6.5, depth_m=9.5, kg_m=4.6),    # deep, fine
]


@pytest.fixture(scope="module")
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(CALC.router, prefix="/calc")
    return TestClient(app)


def _post(client, case: BoxCase, heel_deg: float, **overrides):
    body = {
        "displacement_t": 1000.0,
        "gm_m": case.gm_m,
        "kb_m": case.kb_m,
        "bm_m": case.bm_m,
        "beam_m": case.beam_m,
        "draft_m": case.draft_m,
        "depth_m": case.depth_m,
        "heel_angle_deg": heel_deg,
    }
    body.update(overrides)
    return client.post("/calc/calculations/stability", json=body)


# --------------------------------------------------------------------------- #
# reference integrity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"B{c.beam_m:g}T{c.draft_m:g}")
def test_reference_volume_balance(case):
    """The exact solver must conserve submerged area (-> displacement) at every heel."""
    for heel in (0.0, 5.0, 15.0, 30.0, 45.0, 60.0, 80.0):
        res = exact_gz(case, heel)
        assert res["delta_area_rel"] < 1e-10


def test_reference_reproduces_metacentric_limit():
    """Small-angle slope of the exact GZ curve must equal GM (independent cross-check)."""
    case = CASES[0]
    h = 1e-3
    slope = (exact_gz(case, h)["gz_m"] - exact_gz(case, 0.0)["gz_m"]) / math.radians(h)
    assert slope == pytest.approx(case.gm_m, rel=2e-3)


# --------------------------------------------------------------------------- #
# the formula is exact inside its domain
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"B{c.beam_m:g}T{c.draft_m:g}")
def test_wall_sided_exact_inside_domain(case):
    phi_de = case.deck_edge_immersion_deg
    heels = [h for h in (0.0, 2.0, 5.0, 10.0, 0.5 * phi_de, 0.9 * phi_de) if h < phi_de]
    heels.append(phi_de - 1e-6)  # right at the deck-edge immersion limit
    for heel in heels:
        ref = exact_gz(case, heel)["gz_m"]
        got = wall_sided_gz(case.gm_m, case.bm_m, heel)
        assert abs(got - ref) <= 1e-6 * max(abs(ref), 1e-9) + 1e-12


# --------------------------------------------------------------------------- #
# and invalid beyond it — documented, quantified
# --------------------------------------------------------------------------- #
def test_error_exceeds_acceptability_beyond_domain():
    """At 45 deg every case over-predicts GZ by more than the 15 % acceptability bound."""
    for case in CASES:
        assert 45.0 > case.deck_edge_immersion_deg
        ref = exact_gz(case, 45.0)["gz_m"]
        err = (wall_sided_gz(case.gm_m, case.bm_m, 45.0) - ref) / ref
        assert err > 0.15, f"expected >15% over-prediction, got {err:.1%}"


def test_formula_is_unbounded_near_90deg():
    """The closed form has no peak and no vanishing angle — it diverges as phi -> 90 deg."""
    case = CASES[0]
    assert wall_sided_gz(case.gm_m, case.bm_m, 85.0) > 10.0 * wall_sided_gz(case.gm_m, case.bm_m, 45.0)
    # while the exact arm at 85 deg is smaller than its own peak
    peak = max(exact_gz(case, a)["gz_m"] for a in range(0, 90, 5))
    assert exact_gz(case, 85.0)["gz_m"] < peak


# --------------------------------------------------------------------------- #
# endpoint behaviour
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"B{c.beam_m:g}T{c.draft_m:g}")
def test_endpoint_value_matches_formula_and_reference(client, case):
    heel = round(0.5 * case.deck_edge_immersion_deg, 4)
    resp = _post(client, case, heel)
    assert resp.status_code == 200
    data = resp.json()
    expected = wall_sided_gz(case.gm_m, case.bm_m, heel)
    assert data["righting_arm_m"] == pytest.approx(expected, abs=1e-6)  # response is rounded to 1e-6 m
    assert data["righting_arm_m"] == pytest.approx(exact_gz(case, heel)["gz_m"], abs=1e-5)
    assert data["wall_sided_valid"] is True
    assert data["deck_edge_immersion_deg"] == pytest.approx(case.deck_edge_immersion_deg, abs=1e-3)
    assert data["validity_note"] is None


def test_endpoint_flags_out_of_domain(client):
    case = CASES[0]
    resp = _post(client, case, case.deck_edge_immersion_deg + 5.0)
    assert resp.status_code == 200
    data = resp.json()
    assert data["wall_sided_valid"] is False
    assert data["validity_note"] and "甲板边缘入水角" in data["validity_note"]


def test_endpoint_reports_unknown_domain_without_geometry(client):
    resp = client.post(
        "/calc/calculations/stability",
        json={"displacement_t": 1000.0, "gm_m": 1.0, "kb_m": 2.0, "bm_m": 3.0, "heel_angle_deg": 10.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["wall_sided_valid"] is None
    assert data["deck_edge_immersion_deg"] is None
    assert data["validity_note"]
    # legacy numeric fields are untouched by the hardening
    assert data["righting_arm_m"] == pytest.approx(
        math.sin(math.radians(10.0)) * (1.0 + 0.5 * 3.0 * math.tan(math.radians(10.0)) ** 2), abs=1e-6
    )


@pytest.mark.parametrize("heel", [90.0, -90.0, 120.0])
def test_endpoint_rejects_undefined_heel(client, heel):
    case = CASES[0]
    assert _post(client, case, heel).status_code == 422


def test_endpoint_accepts_negative_gm(client):
    """An unstable ship (GM < 0) must be analysable and yield a negative arm at small heel."""
    case = CASES[0]
    resp = client.post(
        "/calc/calculations/stability",
        json={"displacement_t": 1000.0, "gm_m": -0.2, "kb_m": case.kb_m, "bm_m": case.bm_m,
              "beam_m": case.beam_m, "draft_m": case.draft_m, "depth_m": case.depth_m,
              "heel_angle_deg": 5.0},
    )
    assert resp.status_code == 200
    assert resp.json()["righting_arm_m"] < 0.0


def test_deck_edge_helper():
    assert CALC.deck_edge_immersion_deg(20.0, 8.0, 12.0) == pytest.approx(21.8014, abs=1e-3)
    assert CALC.deck_edge_immersion_deg(None, 8.0, 12.0) is None
    assert CALC.deck_edge_immersion_deg(20.0, 8.0, 8.0) == 0.0  # no freeboard