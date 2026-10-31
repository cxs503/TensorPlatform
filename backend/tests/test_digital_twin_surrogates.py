"""Registry-level tests: uniform contract, latency budget, fallback, validation."""
import time

import numpy as np
import pytest

from services.digital_twin.models.geometry import HullForm
from services.digital_twin.surrogate_registry import SurrogateRegistry, get_registry
from services.digital_twin.types import (InputValidationError, SurrogateNotFound)

INPUTS = {
    "resistance": {"V": 6.0, "ice_concentration": 0.5, "ice_thickness": 0.5},
    "seakeeping": {"Hs": 4.0, "Tp": 8.0, "heading_deg": 180.0, "V": 6.0},
    "manoeuvring": {"V": 6.0, "rudder_deg": 30.0},
    "stability": {"heel_deg": 15.0, "KG": 10.0},
    "fatigue": {"Hs": 4.0, "Tp": 8.0, "ice_concentration": 0.5, "ice_thickness": 0.5},
}


@pytest.fixture(scope="module")
def registry():
    reg = get_registry(180.0, 32.0, 11.5, 0.80, 10.0, n_samples=96)
    reg.warmup_hint = True                       # touch nothing; build is lazy in get_registry
    return reg


def test_five_surrogates_registered(registry):
    assert sorted(registry.names()) == sorted(INPUTS)
    for name, payload in INPUTS.items():
        out = registry.predict(name, payload)
        assert isinstance(out, dict) and out, name
        assert all(isinstance(v, float) for v in out.values())


def test_uniform_interface(registry):
    """Every surrogate exposes the same predict / describe / health surface."""
    for name in registry.names():
        s = registry.get(name)
        for attr in ("predict", "safe_predict", "describe", "health"):
            assert callable(getattr(s, attr)), (name, attr)
        desc = s.describe()
        assert desc["name"] == name and desc["version"]
        assert desc["inputs"]["fields"] and desc["outputs"]["fields"]
        h = s.health()
        assert h["latency_budget_ms"] == 100.0
        assert set(["calls", "errors", "latency", "fit"]).issubset(h)


def test_predict_latency_p95_below_budget(registry):
    """Criterion (b): single predict wall-clock P95 < 100 ms."""
    for name, payload in INPUTS.items():
        reg = registry.get(name)
        for _ in range(200):
            registry.predict(name, payload)
        p95 = reg.latency.stats()["p95_ms"]
        assert p95 is not None and p95 < 100.0, (name, p95)


def test_surrogate_matches_reference(registry):
    """Surrogate reproduces its own reference model well inside the 15 % rule."""
    checks = {
        "resistance": ("R_total_kN", {}),
        "seakeeping": ("heave_rms_m", {}),
        "manoeuvring": ("turning_diameter_L", {}),
        "stability": ("GZ_m", {}),
        "fatigue": ("sigma_rms_MPa", {}),
    }
    for name, (key, _) in checks.items():
        s = registry.get(name)
        ref = s._predict_reference(INPUTS[name])
        got = registry.predict(name, INPUTS[name])
        rel = abs(got[key] - ref[key]) / max(abs(ref[key]), 1e-9)
        assert rel < 0.15, (name, key, got[key], ref[key], rel)


def test_fit_quality_reported(registry):
    for name in registry.names():
        rep = registry.get(name).fit_report
        assert rep["n_basis"] > 0 and rep["training_samples"] > 0
        assert all(v > 0.85 for v in rep["r2"].values()), (name, rep["r2"])


def test_invalid_inputs_rejected(registry):
    with pytest.raises(InputValidationError):
        registry.predict("resistance", {"V": -2.0, "ice_concentration": 0.1,
                                        "ice_thickness": 0.1})
    with pytest.raises(InputValidationError):
        registry.predict("stability", {"heel_deg": 12.0})            # missing KG
    with pytest.raises(SurrogateNotFound):
        registry.predict("no_such_model", {})


def test_fallback_on_injected_surrogate_failure(registry):
    """A broken polynomial must degrade to the reference model, not raise."""
    s = registry.get("resistance")
    original = s.pce.predict
    s.pce.predict = lambda values: (_ for _ in ()).throw(RuntimeError("injected"))
    try:
        out = registry.safe_predict("resistance", INPUTS["resistance"])
    finally:
        s.pce.predict = original
    assert out["degraded"] is True
    assert out["error"] and "injected" in out["error"]
    assert out["outputs"]["R_total_kN"] > 0
    assert s.degraded >= 1


def test_contract_fingerprint_and_health(registry):
    fp = registry.contract_fingerprint()
    assert len(fp) == 16
    assert registry.contract_fingerprint() == fp          # stable
    health = registry.health()
    assert health["n_surrogates"] == 5
    assert health["latency_budget_ms"] == 100.0
    assert health["vessel"]["hull"]["Cb"] == pytest.approx(0.80)
    assert health["vessel"]["geometry_check"]["volume_rel_error"] < 1e-4


def test_predict_all_bundles_results(registry):
    out = registry.predict_all({k: v for k, v in INPUTS.items()
                                if k in ("resistance", "stability")})
    assert set(out["results"]) == {"resistance", "stability"}
    assert out["degraded"] is False
    assert out["registry_version"]


def test_registry_reuse_is_cached():
    t0 = time.perf_counter()
    a = get_registry(180.0, 32.0, 11.5, 0.80, 10.0, n_samples=96)
    dt = time.perf_counter() - t0
    b = get_registry(180.0, 32.0, 11.5, 0.80, 10.0, n_samples=96)
    assert a is b and dt < 1.0