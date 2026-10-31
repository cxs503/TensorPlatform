"""API contract tests for the polar voyage endpoints.

These lock the production interface: status codes, response fields, error
handling (400 for impossible input, 404 for unknown ship type) and multi-ship
type support.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.polar import router as polar_router  # noqa: E402


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(polar_router, prefix="/api/v1")
    return TestClient(app)


def test_capabilities_reports_module_and_models(client):
    r = client.get("/api/v1/polar/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["module"] == "tp-polar-voyage"
    assert body["evidence_level"] in {"REAL", "MOCK", "STUB"}
    assert "level_ice" in body["models"]


def test_ships_catalogue_is_multi_ship_type(client):
    r = client.get("/api/v1/polar/ships")
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["ships"]}
    assert {"handysize_bulker", "pc6_bulker", "aframax_tanker",
            "container_8000teu", "icebreaker_escort"} <= keys


def test_ship_geometry_validation_endpoint(client):
    ok = client.post("/api/v1/polar/ships/validate", json={"ship_type": "handysize_bulker"})
    assert ok.status_code == 200 and ok.json()["ok"] is True

    bad = client.post(
        "/api/v1/polar/ships/validate",
        json={"params": {"length_m": 180.0, "beam_m": 28.4, "draft_m": 10.5,
                         "cb": 0.55, "dwt_t": 35000.0}},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["failures"]


def test_level_ice_endpoint_returns_breakdown_and_reference(client):
    r = client.post(
        "/api/v1/polar/resistance/level-ice",
        json={"ship_type": "handysize_bulker", "speed_kn": 6.0, "h_m": 0.6, "sigma_mpa": 1.3},
    )
    assert r.status_code == 200
    body = r.json()
    for key in ("total_n", "ice_crushing_n", "ice_breaking_n", "ice_submersion_n",
                "open_water_n", "reference"):
        assert key in body
    assert body["total_n"] > body["ice_total_n"] > 0.0


def test_unknown_ship_type_is_404(client):
    r = client.post("/api/v1/polar/resistance/level-ice", json={"ship_type": "no_such_ship"})
    assert r.status_code == 404


def test_ice_field_summary_accepts_and_rejects_fields(client):
    field = client.get("/api/v1/polar/ice-field/scenario?include_grid=true").json()
    good = client.post("/api/v1/polar/ice-field/summary", json={"field": field})
    assert good.status_code == 200 and good.json()["valid"] is True

    broken = client.post(
        "/api/v1/polar/ice-field/summary",
        json={"field": {"h": [[1.0, 2.0]], "sigma": [[1.0]]}},
    )
    assert broken.status_code == 400


def test_grid_resistance_endpoint(client):
    field = client.get("/api/v1/polar/ice-field/scenario?include_grid=true").json()
    r = client.post(
        "/api/v1/polar/resistance/level-ice/grid",
        json={
            "ship_type": "pc6_bulker",
            "speed_kn": 6.0,
            "field": field,
            "track_km": [[15.0, 200.0], [200.0, 200.0], [360.0, 212.0]],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_n"] > 0.0
    assert body["track_length_km"] > 0.0

    blocked = client.post(
        "/api/v1/polar/resistance/level-ice/grid",
        json={"ship_type": "pc6_bulker", "speed_kn": 6.0, "field": field,
              "track_km": [[200.0, 250.0], [200.0, 295.0]]},
    )
    assert blocked.status_code == 400


def test_channel_endpoint_includes_calibrated_reference(client):
    r = client.post("/api/v1/polar/resistance/channel",
                    json={"concentration": 0.2, "h_b_m": 0.8, "speed_kn": 6.0})
    assert r.status_code == 200
    body = r.json()
    assert body["brash_n"] > 0.0
    assert abs(body["reference"]["rel_error_pct"]) < 10.0


def test_convoy_spacing_endpoint_reports_interior_minimum(client):
    r = client.post("/api/v1/polar/convoy/spacing", json={"followers": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["interior_minimum"] is True
    assert len(body["curve"]) >= 30


def test_cii_endpoint_returns_rating_and_eedi(client):
    r = client.post(
        "/api/v1/polar/cii",
        json={"ship_type": "aframax_tanker", "fuel_t": 420.0, "distance_nm": 3000.0, "year": 2026},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rating"] in {"A", "B", "C", "D", "E"}
    assert body["required_cii"] < body["cii_reference"]
    assert body["eedi"]["attained"] > 0.0
    assert "category" in body and body["category"] == "tanker"


def test_route_endpoint_returns_waypoints_and_speeds(client):
    r = client.post("/api/v1/polar/route/optimize",
                    json={"deadline_h": None, "stride": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body["points_km"]) >= 2
    assert body["fuel_kg"] > 0.0
    assert body["distance_km"] > 100.0


def test_route_endpoint_reports_infeasible_deadline(client):
    r = client.post("/api/v1/polar/route/optimize",
                    json={"deadline_h": 0.01, "stride": 5})
    assert r.status_code == 400


@pytest.mark.slow
def test_voyage_optimize_endpoint_end_to_end(client):
    r = client.post(
        "/api/v1/polar/voyage/optimize",
        json={"ship_type": "pc6_bulker", "deadline_h": 30.0, "stride": 4, "year": 2026},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["energy"]["fuel_t"] > 0.0
    assert body["efficiency"]["rating"] in {"A", "B", "C", "D", "E"}
    assert body["convoy"]["pass"] is True
    assert "saving_vs_best_experience_pct" in body