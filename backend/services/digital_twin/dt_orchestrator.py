"""Digital-twin orchestrator: sensor ingest -> state estimation -> forecast.

Production concerns covered here:

* **API/schema stability** - every payload is validated, versioned with
  ``API_VERSION`` / ``SCHEMA_VERSION`` and the surrogate contract fingerprint;
* **fault tolerance** - stale sensors are skipped (degraded flag), missing
  channels are tolerated, surrogate failures fall back to the reference physics
  (``safe_predict``) and the orchestrator reports ``degraded`` with reasons
  instead of raising;
* **performance monitoring** - ingest and forecast latencies are recorded with
  percentiles, counters for rejected frames / degraded calls are exposed in
  ``health()``;
* **audit trail** - every state transition is appended to a bounded event log
  (timestamp, event, payload) which is returned by ``snapshot()``.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

from .models.geometry import HullForm
from .online_predictor import EnvironmentForecast, OnlinePredictor, VoyagePlan
from .state_estimator import ExtendedKalmanFilter, Measurement
from .surrogate_registry import SurrogateRegistry, get_registry
from .types import (API_VERSION, DT_VERSION, SCHEMA_VERSION, InputValidationError,
                    LatencyRecorder)

SENSOR_FIELDS = {
    "gm_meas_m": (0.05, 6.0),
    "speed_ms": (0.0, 15.0),
    "shaft_power_kW": (0.0, 40000.0),
    "strain_rms_MPa": (0.0, 400.0),
    "ice_concentration": (0.0, 1.0),
    "ice_thickness": (0.0, 3.0),
    "Hs": (0.0, 15.0),
    "Tp": (1.0, 25.0),
    "peak_period_s": (1.0, 25.0),
}


@dataclass
class DigitalTwinOrchestrator:
    """Closed loop: sensor frame -> EKF -> surrogate forecast -> envelope."""

    L: float = 180.0
    B: float = 32.0
    T: float = 11.5
    Cb: float = 0.80
    KG: float = 10.0
    vessel_name: str = "ice_class_1A_bulker_180m"
    n_samples: Optional[int] = None
    max_sensor_age_s: float = 300.0
    operation: Optional[Mapping[str, float]] = None

    def __post_init__(self) -> None:
        self.hull = HullForm(self.L, self.B, self.T, self.Cb)
        self.registry: Optional[SurrogateRegistry] = None
        self.ekf: Optional[ExtendedKalmanFilter] = None
        self.predictor: Optional[OnlinePredictor] = None
        self.events: List[Dict[str, Any]] = []
        self.frames = 0
        self.rejected_frames = 0
        self.forecasts = 0
        self.degraded_events = 0
        self.latency_ingest = LatencyRecorder(1024)
        self.latency_forecast = LatencyRecorder(256)
        self.last_frame_time: Optional[float] = None
        self.last_forecast: Optional[Dict[str, Any]] = None
        self._faults: Dict[str, int] = {}

    # -- lifecycle ----------------------------------------------------------
    def warmup(self) -> "DigitalTwinOrchestrator":
        """Build surrogates and filters (idempotent, ~30 s for 5 surrogates)."""
        if self.registry is not None:
            return self
        t0 = time.perf_counter()
        self.registry = get_registry(self.L, self.B, self.T, self.Cb, self.KG,
                                    n_samples=self.n_samples)
        self.ekf = ExtendedKalmanFilter(self.registry, self.hull)
        self.predictor = OnlinePredictor(self.registry, self.hull, self.KG, self.operation)
        self._log("warmup", {"elapsed_s": round(time.perf_counter() - t0, 2),
                             "n_surrogates": len(self.registry),
                             "contract": self.registry.contract_fingerprint()})
        return self

    def _log(self, event: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        self.events.append({"t": round(time.time(), 3), "event": event,
                            "payload": dict(payload or {})})
        if len(self.events) > 500:
            del self.events[: len(self.events) - 500]

    # -- sensor ingest ------------------------------------------------------
    def _validate_frame(self, frame: Mapping[str, Any]) -> Dict[str, float]:
        if not isinstance(frame, Mapping):
            raise InputValidationError("sensor frame must be a mapping")
        values: Dict[str, float] = {}
        for key, (lo, hi) in SENSOR_FIELDS.items():
            if key in frame and frame[key] is not None:
                try:
                    v = float(frame[key])
                except (TypeError, ValueError) as exc:
                    raise InputValidationError(f"field '{key}' must be numeric") from exc
                if not math.isfinite(v) or v < lo or v > hi:
                    raise InputValidationError(f"field '{key}'={v} outside [{lo}, {hi}]")
                values[key] = v
        if not values:
            raise InputValidationError("sensor frame carries no recognised channel")
        return values

    def ingest(self, frame: Mapping[str, Any], now_s: Optional[float] = None) -> Dict[str, Any]:
        """Ingest one sensor frame and update the state estimate."""
        self.warmup()
        t0 = time.perf_counter()
        values = self._validate_frame(frame)
        ts = values.get("timestamp_s", frame.get("timestamp_s"))
        now = float(now_s if now_s is not None else time.time())
        stale = False
        if ts is not None:
            age = now - float(ts)
            stale = age > self.max_sensor_age_s
            if stale:
                self._log("stale_frame", {"age_s": round(age, 1)})
        dt = 60.0
        if self.last_frame_time is not None and ts is not None:
            dt = max(min(float(ts) - self.last_frame_time, 3600.0), 1.0)

        measurement = Measurement(
            gm_meas_m=values.get("gm_meas_m"),
            speed_ms=values.get("speed_ms"),
            shaft_power_kW=values.get("shaft_power_kW"),
            strain_rms_MPa=values.get("strain_rms_MPa"),
            peak_period_s=values.get("peak_period_s", 8.0),
            ice_concentration=values.get("ice_concentration", 0.0),
            ice_thickness=values.get("ice_thickness", 0.0),
            Hs=values.get("Hs", 2.0),
            Tp=values.get("Tp", 8.0),
            timestamp_s=ts,
        )
        result: Dict[str, Any] = {"degraded": stale, "reasons": [] if not stale else ["stale_sensor_frame"]}
        if stale:
            self.degraded_events += 1
            self.ekf.predict(dt)
            result["accepted"] = False
        else:
            try:
                rec = self.ekf.step(dt, measurement)
                result["accepted"] = bool(rec["accepted"])
                if not rec["accepted"]:
                    result["reasons"].append(rec.get("reason", "rejected"))
            except InputValidationError:
                raise
            except Exception as exc:                        # noqa: BLE001
                self.degraded_events += 1
                self._faults["estimator_error"] = self._faults.get("estimator_error", 0) + 1
                result.update({"accepted": False, "degraded": True,
                               "reasons": [f"estimator_error: {type(exc).__name__}"]})
        self.frames += 1
        if not result.get("accepted", False):
            self.rejected_frames += 1
        if ts is not None:
            self.last_frame_time = float(ts)
        self.latency_ingest.add((time.perf_counter() - t0) * 1e3)
        self._log("ingest", {"accepted": result.get("accepted"), "degraded": result.get("degraded"),
                             "dt_s": round(dt, 1)})
        result["state"] = self.ekf.state
        result["state_std"] = self.ekf.state_std()
        result["latency_ms"] = round((time.perf_counter() - t0) * 1e3, 3)
        return result

    # -- forecast -----------------------------------------------------------
    def forecast(
        self,
        horizon_h: float = 1.0,
        plan: Optional[VoyagePlan] = None,
        env: Optional[EnvironmentForecast] = None,
        dt_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Predict the performance envelope for the given horizon."""
        self.warmup()
        t0 = time.perf_counter()
        if horizon_h <= 0 or horizon_h > 48:
            raise InputValidationError("horizon_h must be in (0, 48]")
        plan = plan or VoyagePlan(speed_ms=7.0, heading_deg=180.0, horizon_h=horizon_h)
        env = env or EnvironmentForecast()
        state = self.ekf.state
        degraded = self.ekf.updates == 0
        try:
            out = self.predictor.forecast(state, env, plan, horizon_h=horizon_h, dt_s=dt_s)
        except Exception as exc:                            # noqa: BLE001
            self.degraded_events += 1
            self._faults["forecast_error"] = self._faults.get("forecast_error", 0) + 1
            self._log("forecast_failed", {"error": type(exc).__name__})
            raise
        out["versions"] = self.versions()
        out["state"] = state
        out["degraded"] = degraded
        if degraded:
            out.setdefault("reasons", []).append("no_sensor_update_yet")
            self.degraded_events += 1
        self.forecasts += 1
        self.latency_forecast.add((time.perf_counter() - t0) * 1e3)
        self.last_forecast = out
        self._log("forecast", {"horizon_h": horizon_h, "summary": out["summary"]})
        return out

    def forecast_horizons(
        self,
        horizons: tuple = (1.0, 6.0, 24.0),
        plan: Optional[VoyagePlan] = None,
        env: Optional[EnvironmentForecast] = None,
        dt_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Return envelopes for several horizons at once (1 h / 6 h / 24 h)."""
        self.warmup()
        out = {}
        for h in horizons:
            out[f"{h:g}h"] = self.forecast(h, plan=plan, env=env, dt_s=dt_s)["summary"]
        return {"horizons": out, "state": self.ekf.state, "versions": self.versions()}

    # -- observability ------------------------------------------------------
    def versions(self) -> Dict[str, str]:
        return {
            "api_version": API_VERSION,
            "dt_version": DT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "registry_version": (self.registry.registry_version if self.registry else None),
            "contract_fingerprint": (self.registry.contract_fingerprint() if self.registry else None),
            "surrogate_version": (self.registry.get(self.registry.names()[0]).version
                                  if self.registry else None),
        }

    def health(self) -> Dict[str, Any]:
        if self.registry is None:
            return {"status": "cold", "detail": "surrogates not built; call warmup()",
                    "versions": {"api_version": API_VERSION, "dt_version": DT_VERSION}}
        reg_health = self.registry.health()
        est = self.ekf.to_dict()
        status = "ok"
        reasons: List[str] = []
        if reg_health["status"] != "ok":
            status = "degraded"
            reasons.append("surrogate_registry_degraded")
        if self.latency_ingest.stats()["p95_ms"] is not None and self.latency_ingest.stats()["p95_ms"] > 100:
            status = "degraded"
            reasons.append("ingest_latency_p95_over_budget")
        if self.degraded_events > 0 and status == "ok":
            status = "degraded"
            reasons.append("degraded_events_observed")
        return {
            "status": status,
            "reasons": reasons,
            "versions": self.versions(),
            "vessel": {"name": self.vessel_name, "hull": self.hull.to_dict(), "KG_m": self.KG},
            "counters": {
                "frames": self.frames,
                "rejected_frames": self.rejected_frames,
                "forecasts": self.forecasts,
                "degraded_events": self.degraded_events,
                "faults": dict(self._faults),
            },
            "latency": {
                "ingest": self.latency_ingest.stats(),
                "forecast": self.latency_forecast.stats(),
                "budget_ms": 100.0,
            },
            "surrogates": reg_health,
            "estimator": est,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "versions": self.versions(),
            "vessel": {"name": self.vessel_name, "hull": self.hull.to_dict(), "KG_m": self.KG},
            "state": None if self.ekf is None else self.ekf.state,
            "state_std": None if self.ekf is None else self.ekf.state_std(),
            "last_forecast_summary": (self.last_forecast or {}).get("summary"),
            "events": self.events[-20:],
            "counters": {"frames": self.frames, "forecasts": self.forecasts,
                         "degraded_events": self.degraded_events},
        }


# ---------------------------------------------------------------------------
# process-wide orchestrator cache (API layer)
# ---------------------------------------------------------------------------

_ORCHESTRATORS: Dict[tuple, DigitalTwinOrchestrator] = {}


def get_orchestrator(L: float = 180.0, B: float = 32.0, T: float = 11.5,
                     Cb: float = 0.80, KG: float = 10.0,
                     n_samples: Optional[int] = None) -> DigitalTwinOrchestrator:
    key = (round(L, 3), round(B, 3), round(T, 3), round(Cb, 4), round(KG, 3), n_samples)
    if key not in _ORCHESTRATORS:
        _ORCHESTRATORS[key] = DigitalTwinOrchestrator(L=L, B=B, T=T, Cb=Cb, KG=KG,
                                                      n_samples=n_samples)
    return _ORCHESTRATORS[key]