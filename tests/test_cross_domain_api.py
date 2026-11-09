#!/usr/bin/env python3
"""P145-N08: Cross-domain API integration tests — 7 domain endpoints.

Validates request-response chain for one synthetic case per domain:
  1. Resistance (POST /calculations/resistance)
  2. Propulsion (POST /calculations/propulsion/self_propulsion)
  3. Seakeeping (POST /seakeeping_rao)
  4. Manoeuvring (POST /calculations/manoeuvring)
  5. Stability (POST /calculations/stability)
  6. Ice / heat balance (POST /heat_balance)
  7. Green shipping / EEDI (POST /eedi)

Acceptance: 7/7 endpoints return 200 with valid JSON response.
Domain-neutral: all inputs are synthetic numbers, no domain vocabulary in test logic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── app assembly ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def app():
    """Build a minimal FastAPI app with all domain routers."""
    a = FastAPI(title="P145-N08 Cross-Domain API Test App")

    from api.calculations import router as r_calc
    from api.domain.ship_seakeeping import router as r_seak
    from api.domain.manoeuvring import router as r_man
    from api.domain.ship_icing import router as r_ice
    from api.domain.ship_green import router as r_green

    a.include_router(r_calc, prefix="/api/v1")
    a.include_router(r_seak, prefix="/api/v1")
    a.include_router(r_man, prefix="/api/v1")
    a.include_router(r_ice, prefix="/api/v1")
    a.include_router(r_green, prefix="/api/v1")
    return a


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


# ── synthetic ship particulars (domain-neutral numbers) ─────────────────────
SYNTHETIC_SHIP = {
    "length": 200.0,
    "beam": 32.0,
    "draft": 10.0,
    "cb": 0.65,
    "speed_ms": 7.5,
    "displacement_t": 42000.0,
}


# ── 1. Resistance ───────────────────────────────────────────────────────────
def test_resistance_endpoint(client):
    """POST /calculations/resistance returns 200 with numeric result."""
    resp = client.post("/api/v1/calculations/resistance", json={
        "ship_type": "generic",
        "speed_ms": SYNTHETIC_SHIP["speed_ms"],
        "length": SYNTHETIC_SHIP["length"],
        "beam": SYNTHETIC_SHIP["beam"],
        "draft": SYNTHETIC_SHIP["draft"],
        "cb": SYNTHETIC_SHIP["cb"],
    })
    assert resp.status_code == 200, f"resistance failed: {resp.text}"
    data = resp.json()
    assert "total_resistance_n" in data or "resistance_n" in data or "Ct" in data or "Ct_1" in data


# ── 2. Propulsion ───────────────────────────────────────────────────────────
def test_propulsion_endpoint(client):
    """POST /calculations/propulsion/self_propulsion returns 200."""
    resp = client.post("/api/v1/calculations/propulsion/self_propulsion", json={
        "resistance_n": 500000.0,
        "speed_ms": SYNTHETIC_SHIP["speed_ms"],
        "diameter_m": 5.5,
        "wake_fraction": 0.25,
        "thrust_deduction": 0.18,
    })
    assert resp.status_code == 200, f"propulsion failed: {resp.text}"
    data = resp.json()
    assert "thrust_n" in data or "rpm" in data or "eta_h" in data


# ── 3. Seakeeping ───────────────────────────────────────────────────────────
def test_seakeeping_rao_endpoint(client):
    """POST /seakeeping_rao returns 200 with RAO data."""
    resp = client.post("/api/v1/calculations/seakeeping_rao", json={
        "heading_deg": 180.0,
        "speed_ms": SYNTHETIC_SHIP["speed_ms"],
        "omega_min": 0.1,
        "omega_max": 2.0,
        "n_points": 21,
    })
    assert resp.status_code == 200, f"seakeeping RAO failed: {resp.text}"
    data = resp.json()
    assert "curves" in data or "natural_frequencies_rad_s" in data or "omega" in data


# ── 4. Manoeuvring ──────────────────────────────────────────────────────────
def test_manoeuvring_endpoint(client):
    """POST /calculations/manoeuvring returns 200 with manoeuvre data."""
    resp = client.post("/api/v1/calculations/manoeuvring", json={
        "length": SYNTHETIC_SHIP["length"],
        "beam": SYNTHETIC_SHIP["beam"],
        "draft": SYNTHETIC_SHIP["draft"],
        "cb": SYNTHETIC_SHIP["cb"],
        "design_speed": SYNTHETIC_SHIP["speed_ms"],
        "manoeuvre": "turning_circle",
        "rudder_deg": 35.0,
    })
    assert resp.status_code == 200, f"manoeuvring failed: {resp.text}"
    data = resp.json()
    assert "indicators" in data or "manoeuvre" in data or "scenario" in data


# ── 5. Stability ────────────────────────────────────────────────────────────
def test_stability_endpoint(client):
    """POST /calculations/stability returns 200 with stability data."""
    resp = client.post("/api/v1/calculations/stability", json={
        "ship_type": "generic",
        "displacement_t": SYNTHETIC_SHIP["displacement_t"],
        "beam_m": SYNTHETIC_SHIP["beam"],
        "draft_m": SYNTHETIC_SHIP["draft"],
        "kg_m": 8.0,
        "km_t": 9.5,
    })
    assert resp.status_code == 200, f"stability failed: {resp.text}"
    data = resp.json()
    assert "gm_effective_m" in data or "righting_arm_m" in data or "gm" in data


# ── 6. Ice / heat balance ──────────────────────────────────────────────────
def test_ice_heat_balance_endpoint(client):
    """POST /heat_balance returns 200 with thermal data."""
    resp = client.post("/api/v1/ship/icing/heat_balance", json={
        "t_air_c": -15.0,
        "wind_speed": 15.0,
        "t_water_c": 0.0,
        "salinity": 33.0,
        "rel_humidity": 0.8,
        "char_length": 5.0,
    })
    assert resp.status_code == 200, f"ice heat_balance failed: {resp.text}"
    data = resp.json()
    assert "q_conv_w_m2" in data or "ice_mass_flux_kg_m2s" in data or "regime" in data


# ── 7. Green shipping / EEDI ────────────────────────────────────────────────
def test_green_eedi_endpoint(client):
    """POST /eedi returns 200 with EEDI value."""
    resp = client.post("/api/v1/calculations/green/eedi", json={
        "ship_type": "bulk_carrier",
        "capacity_t": 80000.0,
        "v_ref_kn": 14.0,
        "p_me_kw": 12000.0,
        "main_fuel": {
            "fuel": "hfo",
            "sfoc_g_per_kwh": 175.0,
        },
    })
    assert resp.status_code == 200, f"EEDI failed: {resp.text}"
    data = resp.json()
    assert "attained_value" in data or "required_value" in data or "ratio" in data


# ── integration summary ─────────────────────────────────────────────────────
def test_all_seven_endpoints_respond(client):
    """Meta-test: all 7 endpoints must return 200 for synthetic inputs."""
    endpoints = [
        ("/api/v1/calculations/resistance", {
            "ship_type": "generic", "speed_ms": 7.5,
            "length": 200.0, "beam": 32.0, "draft": 10.0, "cb": 0.65}),
        ("/api/v1/calculations/propulsion/self_propulsion", {
            "resistance_n": 500000.0, "speed_ms": 7.5, "diameter_m": 5.5}),
        ("/api/v1/calculations/seakeeping_rao", {
            "heading_deg": 180.0, "speed_ms": 7.5,
            "omega_min": 0.1, "omega_max": 2.0, "n_points": 11}),
        ("/api/v1/calculations/manoeuvring", {
            "length": 200.0, "beam": 32.0, "draft": 10.0,
            "cb": 0.65, "design_speed": 7.5}),
        ("/api/v1/calculations/stability", {
            "displacement_t": 42000.0, "beam_m": 32.0,
            "draft_m": 10.0, "kg_m": 8.0, "km_t": 9.5}),
        ("/api/v1/ship/icing/heat_balance", {
            "t_air_c": -15.0, "wind_speed": 15.0,
            "t_water_c": 0.0, "char_length": 5.0}),
        ("/api/v1/calculations/green/eedi", {
            "ship_type": "bulk_carrier", "capacity_t": 80000.0,
            "v_ref_kn": 14.0, "p_me_kw": 12000.0,
            "main_fuel": {"fuel": "hfo", "sfoc_g_per_kwh": 175.0}}),
    ]
    passed = 0
    for path, body in endpoints:
        resp = client.post(path, json=body)
        if resp.status_code == 200:
            passed += 1
        else:
            print(f"FAIL: {path} → {resp.status_code}: {resp.text[:200]}")
    assert passed == 7, f"only {passed}/7 endpoints returned 200"
