"""Surrogate model registry: one uniform ``predict(inputs) -> outputs`` contract.

Five physics surrogates are registered for a given vessel (hull + KG):

=============  =========================================  =====================
name           reference model                            lifetime
=============  =========================================  =====================
resistance     Holtrop-Mennen + Lindqvist level ice       PCE (degree 3)
seakeeping     2-DOF modal heave/pitch + empirical R_AW   PCE (degree 3)
manoeuvring    MMG 3-DOF turning circle / zigzag          PCE (degree 2)
stability      numerical KN/GZ hydrostatics               PCE (degree 2)
fatigue        spectral Miner (DNV curve F, bilinear)     PCE (degree 3)
=============  =========================================  =====================

Each surrogate exposes ``predict``, ``describe``, ``health`` and a measured
latency distribution.  ``predict`` is a basis evaluation (~50 terms) plus a dot
product, so the P95 latency is three orders of magnitude below the 100 ms budget;
this is asserted by the test-suite over 200 consecutive calls.

Robustness: every call is validated against the declared schema, wrapped in a
latency/error counter, and falls back to the reference physics model (slower but
exact) if the polynomial evaluation ever fails - the caller only sees a
``degraded`` flag.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np

from .models.fatigue import spectral_fatigue
from .models.geometry import HullForm
from .models.manoeuvring import turning_circle
from .models.resistance import total_resistance
from .models.seakeeping import irregular_seastate
from .models.stability import kn_curve
from .pce import PolynomialChaos
from .types import (API_VERSION, DT_VERSION, Field, InputValidationError, LatencyRecorder,
                    Schema, SurrogateExecutionError, SurrogateNotFound, HullGeometry)

SURROGATE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# base class
# ---------------------------------------------------------------------------

@dataclass
class Surrogate:
    """Uniform interface every digital-twin surrogate must implement."""

    name: str
    version: str
    description: str
    inputs: Schema
    outputs: Schema
    reference_model: str

    def __post_init__(self) -> None:
        self.calls = 0
        self.errors = 0
        self.degraded = 0
        self.latency = LatencyRecorder(capacity=4096)
        self.fit_report: Dict[str, Any] = {}

    # -- interface ----------------------------------------------------------
    def predict(self, inputs: Mapping[str, float]) -> Dict[str, float]:
        raise NotImplementedError

    def _predict_reference(self, values: Dict[str, float]) -> Dict[str, float]:
        """Fallback: evaluate the reference physics model directly."""
        raise NotImplementedError

    # -- instrumentation ----------------------------------------------------
    def _record(self, ok: bool, ms: float) -> None:
        self.calls += 1
        if not ok:
            self.errors += 1
        self.latency.add(ms)

    def safe_predict(self, inputs: Mapping[str, float]) -> Dict[str, Any]:
        """Never raises: returns ``{outputs, degraded, error, latency_ms}``."""
        t0 = time.perf_counter()
        try:
            values = inputs.validate(inputs) if isinstance(inputs, Schema) else dict(inputs)
            out = self.predict(values)
            ok, err, degraded = True, None, False
        except InputValidationError:
            raise
        except Exception as exc:                      # noqa: BLE001 - guarded fallback
            self.errors += 1
            try:
                out = self._predict_reference(dict(inputs))
                ok, err, degraded = False, f"{type(exc).__name__}: {exc}", True
                self.degraded += 1
            except Exception:                          # noqa: BLE001
                self._record(False, (time.perf_counter() - t0) * 1e3)
                raise SurrogateExecutionError(
                    f"surrogate '{self.name}' failed and fallback failed"
                ) from exc
        if degraded:
            self.latency.add((time.perf_counter() - t0) * 1e3)
        return {"outputs": out, "degraded": degraded, "error": err,
                "latency_ms": round((time.perf_counter() - t0) * 1e3, 4)}

    def health(self) -> Dict[str, Any]:
        stats = self.latency.stats()
        return {
            "name": self.name,
            "version": self.version,
            "reference_model": self.reference_model,
            "calls": self.calls,
            "errors": self.errors,
            "degraded_calls": self.degraded,
            "latency": stats,
            "latency_budget_ms": 100.0,
            "latency_ok": (stats["p95_ms"] is None) or (stats["p95_ms"] < 100.0),
            "fit": self.fit_report,
            "status": "ok" if self.errors == 0 else "degraded",
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "reference_model": self.reference_model,
            "inputs": self.inputs.to_dict(),
            "outputs": self.outputs.to_dict(),
            "fit": self.fit_report,
        }


# ---------------------------------------------------------------------------
# generic PCE-backed surrogate
# ---------------------------------------------------------------------------

def _adaptive_degree(n_inputs: int, requested: int, n_samples: int) -> int:
    """Lower the PCE degree when the design of experiments is too sparse.

    Rule: keep at least four training samples per basis term (avoids the
    over-fitting seen when a 35-term 4-D basis is fitted on 30 samples).
    """
    for d in range(int(requested), 0, -1):
        n_terms = math.comb(n_inputs + d, d)
        if n_terms <= max(n_samples // 4, 3):
            return d
    return 1


class PCESurrogate(Surrogate):
    """Surrogate whose reference model is wrapped by a truncated PCE."""

    def __init__(
        self,
        name: str,
        description: str,
        reference_model: str,
        hull: HullForm,
        input_fields: Sequence[Field],
        output_names: Sequence[str],
        reference_fn: Callable[[np.ndarray], np.ndarray],
        reference_scalar: Optional[Callable[[Dict[str, float]], Dict[str, float]]] = None,
        degree: int = 3,
        n_samples: int = 256,
        output_units: Optional[Dict[str, str]] = None,
        log10_outputs: Sequence[str] = (),
        postprocess: Optional[Callable[[Dict[str, float]], Dict[str, float]]] = None,
    ):
        outputs = [Field(n, unit=(output_units or {}).get(n, "-")) for n in output_names]
        super().__init__(
            name=name,
            version=SURROGATE_VERSION,
            description=description,
            inputs=Schema(list(input_fields)),
            outputs=Schema(outputs),
            reference_model=reference_model,
        )
        self.hull = hull
        self._scalar = reference_scalar
        self._ref_fn = reference_fn
        self.log10_outputs = list(log10_outputs)
        self._log_idx = [i for i, n in enumerate(output_names) if n in self.log10_outputs]
        self._postprocess = postprocess
        lower = [f.lower for f in input_fields]
        upper = [f.upper for f in input_fields]
        degree = _adaptive_degree(len(input_fields), degree, n_samples)
        self.pce = PolynomialChaos([f.name for f in input_fields], lower, upper, degree=degree)
        t0 = time.perf_counter()
        def _wrapped(X: np.ndarray) -> np.ndarray:
            Y = np.array(reference_fn(X), dtype=float, copy=True)
            for j in self._log_idx:
                Y[:, j] = np.log10(np.clip(Y[:, j], 1e-12, None))
            return Y

        self.pce.fit(_wrapped, n_samples=n_samples, output_names=output_names)
        self.fit_report = dict(self.pce.fit_report)
        self.fit_report["fit_time_s"] = round(time.perf_counter() - t0, 3)
        self.fit_report["training_samples"] = n_samples

    # -- validation ---------------------------------------------------------
    def _prepare(self, inputs: Mapping[str, float]) -> Dict[str, float]:
        values: Dict[str, float] = {}
        for f in self.inputs.fields:
            if f.name in inputs:
                values[f.name] = f.check(inputs[f.name])
            else:
                raise InputValidationError(f"missing required field '{f.name}'")
        return values

    def predict(self, inputs: Mapping[str, float]) -> Dict[str, float]:
        t0 = time.perf_counter()
        values = self._prepare(inputs)
        raw = self.pce.predict(values)
        out = {k: float(v) for k, v in raw.items()}
        for name in self.log10_outputs:
            if name in out:
                out[name] = float(10.0 ** np.clip(out[name], -12.0, 12.0))
        if self._postprocess is not None:
            out = self._postprocess(out)
        self._record(True, (time.perf_counter() - t0) * 1e3)
        return out

    def _predict_reference(self, inputs: Mapping[str, float]) -> Dict[str, float]:
        values = self._prepare(inputs)
        if self._scalar is None:
            raise SurrogateExecutionError("no scalar reference model")
        return self._scalar(values)

    def predict_with_uncertainty(self, inputs: Mapping[str, float]) -> Dict[str, Any]:
        values = self._prepare(inputs)
        mean = self.predict(values)
        std = self.pce.predict_std(values)
        return {"mean": mean, "std": std}


# ---------------------------------------------------------------------------
# the five reference wrappers
# ---------------------------------------------------------------------------

def _make_resistance(hull: HullForm, kg: float, n_samples: int = 256) -> PCESurrogate:
    fields = [
        Field("V", "m/s", 1.0, 9.5, "ship speed through water"),
        Field("ice_concentration", "-", 0.0, 1.0, "ice concentration"),
        Field("ice_thickness", "m", 0.0, 1.2, "level ice thickness"),
    ]
    names = ["R_calm_kN", "R_ice_kN", "R_total_kN", "P_D_kW"]

    def ref(X: np.ndarray) -> np.ndarray:
        out = np.empty((X.shape[0], 4))
        for i, (V, conc, h) in enumerate(X):
            r = total_resistance(float(V), hull.L, hull.B, hull.T, hull.Cb,
                                 float(conc), float(h), 0.0, rho=hull.rho)
            out[i] = (r["R_calm_N"] / 1e3, r["R_ice_N"] / 1e3,
                      r["R_total_N"] / 1e3, r["P_D_kW"])
        return out

    def scalar(v: Dict[str, float]) -> Dict[str, float]:
        r = total_resistance(v["V"], hull.L, hull.B, hull.T, hull.Cb,
                             v["ice_concentration"], v["ice_thickness"], 0.0, rho=hull.rho)
        return {"R_calm_kN": r["R_calm_N"] / 1e3, "R_ice_kN": r["R_ice_N"] / 1e3,
                "R_total_kN": r["R_total_N"] / 1e3, "P_D_kW": r["P_D_kW"]}

    return PCESurrogate(
        name="resistance",
        description="Calm-water (Holtrop-Mennen) + level-ice (Lindqvist) resistance and delivered power",
        reference_model="holtrop_mennen_1982 + lindqvist_1989",
        hull=hull, input_fields=fields, output_names=names,
        reference_fn=ref, reference_scalar=scalar, degree=3, n_samples=n_samples,
        output_units={"R_calm_kN": "kN", "R_ice_kN": "kN", "R_total_kN": "kN", "P_D_kW": "kW"},
    )


def _make_seakeeping(hull: HullForm, kg: float, n_samples: int = 192) -> PCESurrogate:
    fields = [
        Field("Hs", "m", 0.2, 8.0, "significant wave height"),
        Field("Tp", "s", 3.0, 14.0, "peak period"),
        Field("heading_deg", "deg", 90.0, 180.0, "absolute wave heading (180 = head)"),
        Field("V", "m/s", 1.0, 9.5, "ship speed"),
    ]
    names = ["heave_rms_m", "pitch_rms_deg", "R_aw_kN"]

    def ref(X: np.ndarray) -> np.ndarray:
        out = np.empty((X.shape[0], 3))
        for i, (Hs, Tp, hd, V) in enumerate(X):
            r = irregular_seastate(float(Hs), float(Tp), float(hd), hull, kg, float(V),
                                   n_freq=16, n_sections=31)
            out[i] = (r["heave_rms_m"], r["pitch_rms_deg"], r["R_aw_kN"])
        return out

    def scalar(v: Dict[str, float]) -> Dict[str, float]:
        r = irregular_seastate(v["Hs"], v["Tp"], v["heading_deg"], hull, kg, v["V"])
        return {"heave_rms_m": r["heave_rms_m"], "pitch_rms_deg": r["pitch_rms_deg"],
                "R_aw_kN": r["R_aw_kN"]}

    return PCESurrogate(
        name="seakeeping",
        description="Heave/pitch RMS and added resistance in a JONSWAP sea state",
        reference_model="2dof_modal_strip + gerritsma_added_resistance",
        hull=hull, input_fields=fields, output_names=names,
        reference_fn=ref, reference_scalar=scalar, degree=3, n_samples=n_samples,
        output_units={"heave_rms_m": "m", "pitch_rms_deg": "deg", "R_aw_kN": "kN"},
    )


def _make_manoeuvring(hull: HullForm, kg: float, n_samples: int = 48) -> PCESurrogate:
    fields = [
        Field("V", "m/s", 2.0, 9.5, "approach speed"),
        Field("rudder_deg", "deg", 10.0, 35.0, "rudder angle"),
    ]
    names = ["turning_diameter_L", "advance_L", "speed_loss_pct", "steady_yaw_rate_deg_s"]

    def sim(V: float, rudder: float) -> Dict[str, float]:
        return turning_circle(hull, float(V), rudder_deg=float(rudder), t_end=720.0,
                              dt=0.75, U_R_factor=0.9, n_sections=11)

    def ref(X: np.ndarray) -> np.ndarray:
        out = np.empty((X.shape[0], 4))
        for i, (V, rud) in enumerate(X):
            r = sim(float(V), float(rud))
            out[i] = (r["turning_diameter_L"], r["advance_L"],
                      r["speed_loss_pct"], r["steady_yaw_rate_deg_s"])
        return out

    def scalar(v: Dict[str, float]) -> Dict[str, float]:
        r = sim(v["V"], v["rudder_deg"])
        return {"turning_diameter_L": r["turning_diameter_L"], "advance_L": r["advance_L"],
                "speed_loss_pct": r["speed_loss_pct"],
                "steady_yaw_rate_deg_s": r["steady_yaw_rate_deg_s"]}

    return PCESurrogate(
        name="manoeuvring",
        description="MMG turning-circle metrics (diameter, advance, speed loss, yaw rate)",
        reference_model="mmg_3dof_crossflow_lift",
        hull=hull, input_fields=fields, output_names=names,
        reference_fn=ref, reference_scalar=scalar, degree=2, n_samples=n_samples,
        output_units={"turning_diameter_L": "L", "advance_L": "L",
                      "speed_loss_pct": "%", "steady_yaw_rate_deg_s": "deg/s"},
    )


def _make_stability(hull: HullForm, kg: float, n_samples: int = 48) -> PCESurrogate:
    fields = [
        Field("heel_deg", "deg", 0.0, 30.0, "heel angle"),
        Field("KG", "m", 0.45 * hull.T, 1.05 * hull.T, "vertical centre of gravity"),
    ]
    names = ["GZ_m", "KN_m", "GM_m"]

    def ref(X: np.ndarray) -> np.ndarray:
        out = np.empty((X.shape[0], 3))
        for i, (heel, KG) in enumerate(X):
            kc = kn_curve(hull, [float(heel)], KG=float(KG))
            out[i] = (kc["GZ_m"][0], kc["KN_m"][0], kc["GM_hydrostatic_m"])
        return out

    def scalar(v: Dict[str, float]) -> Dict[str, float]:
        kc = kn_curve(hull, [v["heel_deg"]], KG=v["KG"])
        return {"GZ_m": kc["GZ_m"][0], "KN_m": kc["KN_m"][0],
                "GM_m": kc["GM_hydrostatic_m"]}

    return PCESurrogate(
        name="stability",
        description="Righting arm GZ, KN and metacentric height GM",
        reference_model="numerical_kn_gz_hydrostatics",
        hull=hull, input_fields=fields, output_names=names,
        reference_fn=ref, reference_scalar=scalar, degree=2, n_samples=n_samples,
        output_units={"GZ_m": "m", "KN_m": "m", "GM_m": "m"},
    )


def _make_fatigue(hull: HullForm, kg: float, n_samples: int = 160) -> PCESurrogate:
    fields = [
        Field("Hs", "m", 0.2, 8.0, "significant wave height"),
        Field("Tp", "s", 3.0, 14.0, "peak period"),
        Field("ice_concentration", "-", 0.0, 1.0, "ice concentration"),
        Field("ice_thickness", "m", 0.0, 1.2, "level ice thickness"),
    ]
    names = ["damage_per_day", "sigma_rms_MPa"]

    def _life(dmg: float) -> float:
        return float(1.0 / (dmg * 365.25)) if dmg > 0 else 1e12

    def ref(X: np.ndarray) -> np.ndarray:
        out = np.empty((X.shape[0], 2))
        for i, (Hs, Tp, conc, h) in enumerate(X):
            r = spectral_fatigue(hull, float(Hs), float(Tp), U_knots=12.0,
                                 ice_concentration=float(conc), ice_thickness=float(h))
            out[i] = (r["damage_per_day"], r["sigma_rms_MPa"])
        return out

    def scalar(v: Dict[str, float]) -> Dict[str, float]:
        r = spectral_fatigue(hull, v["Hs"], v["Tp"], U_knots=12.0,
                             ice_concentration=v["ice_concentration"],
                             ice_thickness=v["ice_thickness"])
        return _post({"damage_per_day": r["damage_per_day"],
                      "sigma_rms_MPa": r["sigma_rms_MPa"]})

    def _post(out: Dict[str, float]) -> Dict[str, float]:
        dmg = max(out.get("damage_per_day", 0.0), 0.0)
        return {"damage_per_day": dmg, "life_years": _life(dmg),
                "sigma_rms_MPa": out.get("sigma_rms_MPa", 0.0)}

    return PCESurrogate(
        name="fatigue",
        description="Structural fatigue damage rate and remaining life",
        reference_model="spectral_miner_dnv_c203_curveF",
        hull=hull, input_fields=fields, output_names=names,
        reference_fn=ref, reference_scalar=scalar, degree=3, n_samples=n_samples,
        output_units={"damage_per_day": "1/day", "life_years": "year", "sigma_rms_MPa": "MPa"},
        log10_outputs=("damage_per_day",),
        postprocess=_post,
    )


BUILDERS = {
    "resistance": _make_resistance,
    "seakeeping": _make_seakeeping,
    "manoeuvring": _make_manoeuvring,
    "stability": _make_stability,
    "fatigue": _make_fatigue,
}

# training-sample caps: the MMG reference simulation is ~200x more expensive per
# sample than the closed-form models, so its design space is sampled coarser
SAMPLE_CAP = {"resistance": 256, "seakeeping": 192, "manoeuvring": 48,
              "stability": 64, "fatigue": 160}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

class SurrogateRegistry:
    """Name -> surrogate mapping with versioning, health and bulk prediction."""

    def __init__(self, registry_version: str = API_VERSION):
        self.registry_version = registry_version
        self._surrogates: Dict[str, Surrogate] = {}
        self.vessel: Dict[str, Any] = {}
        self.created_at = time.time()

    # -- registration -------------------------------------------------------
    def register(self, surrogate: Surrogate) -> Surrogate:
        if surrogate.name in self._surrogates:
            raise ValueError(f"surrogate '{surrogate.name}' already registered")
        self._surrogates[surrogate.name] = surrogate
        return surrogate

    def register_vessel(self, hull: HullForm, KG: float, name: str = "generic",
                        names: Optional[Sequence[str]] = None,
                        n_samples: Optional[int] = None) -> None:
        """Build and register all requested surrogates for one vessel."""
        self.vessel = {
            "name": name,
            "hull": hull.to_dict(),
            "KG_m": float(KG),
            "geometry_check": hull.verify(),
        }
        for sname in (names or list(BUILDERS)):
            if sname in self._surrogates:
                continue
            builder = BUILDERS[sname]
            if n_samples is None:
                self.register(builder(hull, KG))
            else:
                self.register(builder(hull, KG, n_samples=min(int(n_samples), SAMPLE_CAP[sname])))

    # -- access -------------------------------------------------------------
    def get(self, name: str) -> Surrogate:
        if name not in self._surrogates:
            raise SurrogateNotFound(f"surrogate '{name}' not registered; "
                                    f"available: {sorted(self._surrogates)}")
        return self._surrogates[name]

    def names(self) -> List[str]:
        return list(self._surrogates)

    def __len__(self) -> int:
        return len(self._surrogates)

    def list(self) -> List[Dict[str, Any]]:
        return [s.describe() for s in self._surrogates.values()]

    # -- prediction ---------------------------------------------------------
    def predict(self, name: str, inputs: Mapping[str, float]) -> Dict[str, float]:
        return self.get(name).predict(inputs)

    def safe_predict(self, name: str, inputs: Mapping[str, float]) -> Dict[str, Any]:
        out = self.get(name).safe_predict(inputs)
        out["surrogate"] = name
        return out

    def predict_all(self, inputs_by_name: Mapping[str, Mapping[str, float]]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, payload in inputs_by_name.items():
            results[name] = self.safe_predict(name, payload)
        return {
            "results": results,
            "degraded": any(r["degraded"] for r in results.values()),
            "registry_version": self.registry_version,
        }

    # -- health -------------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        details = {name: s.health() for name, s in self._surrogates.items()}
        p95 = [d["latency"]["p95_ms"] for d in details.values()
               if d["latency"]["p95_ms"] is not None]
        worst = max(p95) if p95 else None
        ok = all(d["latency_ok"] for d in details.values()) and len(details) == 5
        return {
            "status": "ok" if ok else "degraded",
            "registry_version": self.registry_version,
            "dt_version": DT_VERSION,
            "n_surrogates": len(details),
            "worst_p95_ms": worst,
            "latency_budget_ms": 100.0,
            "vessel": self.vessel,
            "surrogates": details,
        }

    def contract_fingerprint(self) -> str:
        """Stable hash of the (name, version, input/output) contract."""
        import hashlib

        payload = json.dumps(
            [{"name": s.name, "version": s.version,
              "inputs": s.inputs.names, "outputs": s.outputs.names}
             for s in sorted(self._surrogates.values(), key=lambda x: x.name)],
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# default (cached) vessel registry
# ---------------------------------------------------------------------------

DEFAULT_VESSEL = {
    "name": "ice_class_1A_bulker_180m",
    "L": 180.0, "B": 32.0, "T": 11.5, "Cb": 0.80, "KG": 10.0,
}

_CACHE: Dict[str, SurrogateRegistry] = {}


def get_registry(
    L: float = 180.0, B: float = 32.0, T: float = 11.5, Cb: float = 0.80,
    KG: float = 10.0, n_samples: Optional[int] = None, cache: bool = True,
) -> SurrogateRegistry:
    """Return a registry for the given hull (cached by hull key)."""
    key = f"{L:.3f}_{B:.3f}_{T:.3f}_{Cb:.4f}_{KG:.3f}_{n_samples}"
    if cache and key in _CACHE:
        return _CACHE[key]
    hull = HullForm(L, B, T, Cb)
    reg = SurrogateRegistry()
    reg.register_vessel(hull, KG, name=f"L{L:.0f}_B{B:.0f}_T{T:.1f}_Cb{Cb:.2f}",
                        n_samples=n_samples)
    if cache:
        _CACHE[key] = reg
    return reg