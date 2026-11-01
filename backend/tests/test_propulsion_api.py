"""HTTP-level tests for the propulsion endpoints in ``api/calculations.py``.

The platform router is loaded directly from file so these tests do not need the
full application (which pulls in PostgreSQL/cryptography at import time).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CALC_PATH = BACKEND_DIR / "api" / "calculations.py"


@pytest.fixture(scope="module")
def client():
    spec = importlib.util.spec_from_file_location("platform_calculations", CALC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    return TestClient(app)


class TestActuatorDiskEndpoint:

    def test_analytic_fields_agree(self, client):
        r = client.post("/api/v1/calculations/propulsion/actuator_disk",
                        json={"speed_ms": 7.2, "thrust_n": 350_000.0,
                              "diameter_m": 6.0})
        assert r.status_code == 200
        body = r.json()
        assert body["ideal_efficiency"] == pytest.approx(
            body["ideal_efficiency_analytic"], rel=1e-8)
        assert body["induced_velocity_ms"] == pytest.approx(
            body["induced_velocity_analytic_ms"], rel=1e-8)
        assert abs(body["momentum_residual_n"]) < 1e-3
        assert 0.0 < body["ideal_efficiency"] < 1.0
        assert body["effective_power_w"] == pytest.approx(
            350_000.0 * 7.2, rel=1e-9)

    def test_zero_thrust_is_accepted(self, client):
        r = client.post("/api/v1/calculations/propulsion/actuator_disk",
                        json={"speed_ms": 5.0, "thrust_n": 0.0, "diameter_m": 3.0})
        assert r.status_code == 200
        assert r.json()["ideal_efficiency"] == pytest.approx(1.0, rel=1e-9)

    @pytest.mark.parametrize("payload", [
        {"speed_ms": 0.0, "thrust_n": 1000.0, "diameter_m": 3.0},
        {"speed_ms": 5.0, "thrust_n": -1.0, "diameter_m": 3.0},
        {"speed_ms": 5.0, "thrust_n": 1000.0, "diameter_m": 0.0},
    ])
    def test_invalid_payload_rejected(self, client, payload):
        r = client.post("/api/v1/calculations/propulsion/actuator_disk",
                        json=payload)
        assert r.status_code == 422


class TestSelfPropulsionEndpoint:

    def test_default_case_balances_thrust(self, client):
        r = client.post("/api/v1/calculations/propulsion/self_propulsion", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["required_thrust_n"] == pytest.approx(
            180_000.0 / (1.0 - 0.18), rel=1e-6)
        assert abs(body["thrust_balance_residual_n"]) < 1e-3 * body["required_thrust_n"]
        assert body["quasi_propulsive_efficiency"] == pytest.approx(
            body["quasi_propulsive_efficiency_identity"], rel=1e-8)
        assert body["delivered_power_w"] > body["effective_power_w"] > 0.0

    def test_custom_curves_and_efficiency_chain(self, client):
        payload = {"resistance_n": 240_000.0, "speed_ms": 7.2, "diameter_m": 6.0,
                   "wake_fraction": 0.25, "thrust_deduction": 0.20,
                   "relative_rotative_efficiency": 0.98}
        r = client.post("/api/v1/calculations/propulsion/self_propulsion",
                        json=payload)
        assert r.status_code == 200
        body = r.json()
        expected = (body["open_water_efficiency"] * 0.98
                    * (1.0 - 0.20) / (1.0 - 0.25))
        assert body["quasi_propulsive_efficiency"] == pytest.approx(expected,
                                                                   rel=1e-8)

    def test_out_of_range_hull_factors_rejected(self, client):
        for payload in ({"wake_fraction": 1.0}, {"thrust_deduction": 1.0},
                        {"relative_rotative_efficiency": 0.0}):
            r = client.post("/api/v1/calculations/propulsion/self_propulsion",
                            json=payload)
            assert r.status_code == 422, payload

    def test_infeasible_thrust_surfaces_as_400(self, client):
        r = client.post("/api/v1/calculations/propulsion/self_propulsion",
                        json={"resistance_n": 5.0e9, "speed_ms": 7.2,
                              "diameter_m": 6.0, "wake_fraction": 0.28,
                              "thrust_deduction": 0.18,
                              # a weak curve cannot reach the thrust below the
                              # feasible shaft-rate ceiling -> 400, not a hang
                              "kt_coefficients": [0.02],
                              "kq_coefficients": [0.01]})
        assert r.status_code == 400
        assert "self_propulsion" in r.json()["detail"]


class TestResistanceEndpointStillIntact:
    """Regression guard: the propulsion addition must not disturb resistance."""

    def test_resistance_contract(self, client):
        r = client.post("/api/v1/calculations/resistance", json={"speed_ms": 3.0})
        assert r.status_code == 200
        body = r.json()
        for key in ("total_resistance_n", "frictional_resistance_n",
                    "residual_resistance_n", "reynolds_number", "froude_number"):
            assert key in body
        assert body["total_resistance_n"] > 0.0

    def test_stability_contract(self, client):
        r = client.post("/api/v1/calculations/stability",
                        json={"heel_angle_deg": 10.0})
        assert r.status_code == 200
        assert r.json()["righting_arm_m"] > 0.0