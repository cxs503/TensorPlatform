"""Estimator, predictor, orchestrator and API tests (criterion c, d and e)."""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "localhost")

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.digital_twin import dt_orchestrator as orch_mod
from services.digital_twin.dt_orchestrator import DigitalTwinOrchestrator
from services.digital_twin.models.geometry import HullForm
from services.digital_twin.online_predictor import (EnvironmentForecast,
                                                    OnlinePredictor, VoyagePlan)
from services.digital_twin.state_estimator import (ExtendedKalmanFilter,
                                                   Measurement, convergence_study)
from services.digital_twin.surrogate_registry import get_registry


@pytest.fixture(scope="module")
def registry():
    return get_registry(180.0, 32.0, 11.5, 0.80, 10.0, n_samples=96)


@pytest.fixture(scope="module")
def hull():
    return HullForm(180.0, 32.0, 11.5, 0.80)


# ---------------------------------------------------------------------------
# state estimator
# ---------------------------------------------------------------------------

def test_ekf_converges_within_50_steps(registry, hull):
    """Criterion (c): RMSE < 5 % of truth, converged inside 50 steps."""
    rep = convergence_study(registry, hull, n_steps=100, sensor_noise=0.03)
    assert rep["rel_rmse_gm"] < 0.05
    assert rep["converged_within_50_steps"] is True
    assert rep["steps_to_5pct_gm"] <= 50
    assert rep["rel_rmse_resistance_bias"] < 0.05


def test_ekf_rejects_sensor_spike(registry, hull):
    """Innovation gating must drop a 10-sigma GM spike without corrupting the state."""
    ekf = ExtendedKalmanFilter(registry, hull, x0=np.array([1.45, 1.0, 1.0]))
    m = Measurement(gm_meas_m=1.45, speed_ms=6.0, ice_concentration=0.4,
                    ice_thickness=0.4, Hs=3.0, Tp=8.0)
    for _ in range(5):
        ekf.step(60.0, m)
    before = ekf.state["gm_m"]
    bad = Measurement(gm_meas_m=4.9, speed_ms=6.0, shaft_power_kW=9000.0,
                      ice_concentration=0.4, ice_thickness=0.4, Hs=3.0, Tp=8.0)
    rec = ekf.update(bad)
    assert rec["accepted"] is True            # other channels still used
    assert abs(ekf.state["gm_m"] - before) < 0.1
    assert ekf.rejected >= 1                  # the GM channel was gated out


def test_ekf_requires_supported_channel(registry, hull):
    ekf = ExtendedKalmanFilter(registry, hull)
    with pytest.raises(Exception):
        ekf.update(Measurement(ice_concentration=0.3))


# ---------------------------------------------------------------------------
# online predictor
# ---------------------------------------------------------------------------

def _env():
    return EnvironmentForecast(ice_concentration=[0.28, 0.30, 0.33, 0.30],
                               ice_thickness=0.30,
                               Hs=[3.0, 3.3, 3.5, 3.2], Tp=8.0)


def test_forecast_1h_mare_below_10pct(registry, hull):
    """Criterion (d): 1 h forecast vs fine reference simulation, MARE < 10 %."""
    pred = OnlinePredictor(registry, hull, 10.0)
    rep = pred.validate({"gm_m": 1.45, "resistance_bias": 1.05, "damage_scale": 1.05},
                        _env(), VoyagePlan(speed_ms=7.0), horizon_h=1.0)
    assert rep["mare"] < 0.10, rep["relative_errors"]
    assert rep["criterion_met"] is True
    assert rep["truth"]["n_steps"] > rep["forecast"]["n_steps"]   # finer truth grid


def test_forecast_horizons_are_consistent(registry, hull):
    pred = OnlinePredictor(registry, hull, 10.0)
    state = {"gm_m": 1.45, "resistance_bias": 1.0, "damage_scale": 1.0}
    plan = VoyagePlan(speed_ms=7.0)
    f1 = pred.forecast(state, _env(), plan, horizon_h=1.0)["summary"]
    f6 = pred.forecast(state, _env(), plan, horizon_h=6.0, dt_s=600.0)["summary"]
    f24 = pred.forecast(state, _env(), plan, horizon_h=24.0, dt_s=1200.0)["summary"]
    assert f1["fuel_kg"] < f6["fuel_kg"] < f24["fuel_kg"]
    assert f1["damage_increment"] <= f6["damage_increment"] <= f24["damage_increment"]
    assert 0.0 <= f24["speed_loss_pct"] <= 100.0
    assert all(len(r) for r in (f1["time_s"], f6["time_s"])) if isinstance(f1, dict) and "time_s" in f1 else True


def test_forecast_flags_stability_hazard(registry, hull):
    pred = OnlinePredictor(registry, hull, 10.0)
    heavy_icing = EnvironmentForecast(ice_concentration=1.0, ice_thickness=1.0,
                                      Hs=6.0, Tp=10.0)
    out = pred.forecast({"gm_m": 0.10, "resistance_bias": 1.0, "damage_scale": 1.0},
                        heavy_icing, VoyagePlan(speed_ms=4.0), horizon_h=6.0, dt_s=600.0)
    assert out["summary"]["stability_hazard"] is True
    assert out["summary"]["min_GM_margin_m"] < 0.15


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def twin(monkeypatch_module=None):
    orch_mod._ORCHESTRATORS.clear()
    t = DigitalTwinOrchestrator(n_samples=96)
    t.warmup()
    return t


def test_orchestrator_ingest_and_forecast(twin):
    frame = {"gm_meas_m": 1.47, "speed_ms": 6.2, "shaft_power_kW": 9200.0,
             "strain_rms_MPa": 11.0, "ice_concentration": 0.45, "ice_thickness": 0.4,
             "Hs": 3.4, "Tp": 8.0, "timestamp_s": 1000.0}
    out = twin.ingest(frame, now_s=1000.0)
    assert out["accepted"] is True and out["degraded"] is False
    assert 0.5 < out["state"]["gm_m"] < 3.0
    fc = twin.forecast(1.0, VoyagePlan(speed_ms=7.0), _env())
    assert fc["summary"]["fuel_kg"] > 0
    assert fc["degraded"] is False
    snap = twin.snapshot()
    assert snap["state"] and snap["events"]
    health = twin.health()
    assert health["counters"]["frames"] >= 1
    assert health["latency"]["ingest"]["p95_ms"] < 100.0
    assert health["versions"]["contract_fingerprint"]


def test_orchestrator_marks_stale_sensor_degraded(twin):
    out = twin.ingest({"gm_meas_m": 1.46, "speed_ms": 6.0, "shaft_power_kW": 9000.0,
                       "timestamp_s": 1000.0}, now_s=1000.0 + 900.0)
    assert out["degraded"] is True and out["accepted"] is False
    assert "stale_sensor_frame" in out["reasons"]


def test_orchestrator_rejects_bad_frame(twin):
    with pytest.raises(Exception):
        twin.ingest({"speed_ms": 99.0})
    with pytest.raises(Exception):
        twin.ingest({})


def test_orchestrator_survives_surrogate_failure(twin):
    s = twin.registry.get("fatigue")
    original = s.pce.predict
    s.pce.predict = lambda values: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        out = twin.ingest({"gm_meas_m": 1.44, "speed_ms": 6.1, "shaft_power_kW": 9100.0,
                           "strain_rms_MPa": 11.5, "ice_concentration": 0.4,
                           "ice_thickness": 0.4, "Hs": 3.5, "Tp": 8.0,
                           "timestamp_s": 1060.0}, now_s=1060.0)
    finally:
        s.pce.predict = original
    assert out["state"]["gm_m"] > 0                     # loop still alive
    assert s.degraded >= 1


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(orch_mod, "get_registry",
               lambda *a, **kw: get_registry(180.0, 32.0, 11.5, 0.80, 10.0, n_samples=96))
    orch_mod._ORCHESTRATORS.clear()
    from api.digital_twin import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    yield TestClient(app)
    mp.undo()
    orch_mod._ORCHESTRATORS.clear()


def test_api_version_and_health(client):
    v = client.get("/api/v1/digital-twin/v1/version").json()
    assert v["versions"]["api_version"] == "1.0.0"
    h = client.get("/api/v1/digital-twin/v1/health")
    assert h.status_code == 200 and h.json()["status"] in ("ok", "degraded")
    s = client.get("/api/v1/digital-twin/v1/surrogates").json()
    assert s["n_surrogates"] == 5
    assert s["contract_fingerprint"]


def test_api_predict_estimate_forecast(client):
    p = client.post("/api/v1/digital-twin/v1/predict",
                    json={"surrogate": "resistance",
                          "inputs": {"V": 6.0, "ice_concentration": 0.4,
                                     "ice_thickness": 0.4}})
    assert p.status_code == 200
    body = p.json()
    assert body["outputs"]["R_total_kN"] > 0
    assert body["latency_ms"] < 100.0

    e = client.post("/api/v1/digital-twin/v1/state/estimate",
                    json={"frame": {"gm_meas_m": 1.45, "speed_ms": 6.0,
                                    "shaft_power_kW": 9000.0, "strain_rms_MPa": 11.5,
                                    "ice_concentration": 0.5, "ice_thickness": 0.4,
                                    "Hs": 3.5, "Tp": 8.0, "timestamp_s": 2000.0}})
    assert e.status_code == 200 and e.json()["state"]["gm_m"] > 0

    f = client.post("/api/v1/digital-twin/v1/forecast",
                    json={"horizon_h": 1.0, "speed_ms": 7.0,
                          "environment": {"ice_concentration": [0.3, 0.4],
                                          "ice_thickness": 0.35, "Hs": 3.2,
                                          "Tp": 8.0}})
    assert f.status_code == 200
    assert f.json()["summary"]["fuel_kg"] > 0


def test_api_error_codes(client):
    assert client.post("/api/v1/digital-twin/v1/predict",
                       json={"surrogate": "nope", "inputs": {}}).status_code == 404
    assert client.post("/api/v1/digital-twin/v1/predict",
                       json={"surrogate": "resistance",
                             "inputs": {"V": 99.0, "ice_concentration": 0.1,
                                        "ice_thickness": 0.1}}).status_code == 400
    assert client.post("/api/v1/digital-twin/v1/forecast",
                       json={"horizon_h": 100.0}).status_code == 422