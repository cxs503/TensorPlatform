"""Online performance prediction: current state + environment forecast + voyage plan.

The predictor marches the surrogate chain forward in time and returns the
performance *envelope* (power, fuel, fatigue damage, stability margin, speed
loss) at 1 h / 6 h / 24 h horizons.

Time integration
----------------
At every step the resistance budget is re-assembled from the surrogates::

    R_tot(t) = R_calm + R_ice(conc, h) + R_aw(Hs, Tp, heading)

    P_D = R_tot V / eta_D   (limited by the installed power -> speed drop)

    fuel = P_D * SFOC * dt
    D(t) += d_scale * damage_rate(Hs, Tp, ice) * dt/86400
    GM(t) = GM_0 - dGM_icing(t, ice concentration) - 0.02 * t[day]  (fuel burn-off)

Two predictors share the same loop body:

* ``use_surrogate=True``  - production path, surrogate models, ``dt = 300 s``;
* ``use_surrogate=False`` - reference path, full physics models, ``dt = 60 s``.

The reference path is the "truth simulation" used to score the forecast
(criterion: 1 h MARE < 10 %).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import numpy as np

from .models.fatigue import spectral_fatigue
from .models.geometry import HullForm
from .models.resistance import total_resistance
from .models.seakeeping import irregular_seastate
from .surrogate_registry import SurrogateRegistry

G = 9.80665

DEFAULT_OPERATION = {
    "eta_D": 0.66,
    "sfoc_g_per_kWh": 175.0,
    "power_limit_kW": 11000.0,
    "deck_area_m2": 0.6 * 180.0 * 32.0,
    "icing_rate_kg_m2_h": 6.0,       # ice accretion at 100 % concentration
    "icing_KG_m": 14.0,              # centre of gravity of the accreted ice
}


@dataclass
class VoyagePlan:
    """Ordered speed / heading schedule for the forecast horizon."""

    speed_ms: float = 7.0
    heading_deg: float = 180.0
    horizon_h: float = 1.0

    def sample(self, t_s: float) -> Dict[str, float]:
        return {"speed_ms": self.speed_ms, "heading_deg": self.heading_deg}


@dataclass
class EnvironmentForecast:
    """Piecewise-linear environment time series (scalars are broadcast)."""

    ice_concentration: Union[float, Sequence[float]] = 0.0
    ice_thickness: Union[float, Sequence[float]] = 0.0
    Hs: Union[float, Sequence[float]] = 2.0
    Tp: Union[float, Sequence[float]] = 8.0
    wind_ms: Union[float, Sequence[float]] = 10.0

    def _interp(self, value, t_s: float, horizon_s: float) -> float:
        if np.isscalar(value):
            return float(value)
        arr = np.asarray(value, dtype=float)
        if arr.size == 0:
            return 0.0
        if arr.size == 1:
            return float(arr[0])
        xs = np.linspace(0.0, max(horizon_s, 1.0), arr.size)
        return float(np.interp(t_s, xs, arr))

    def sample(self, t_s: float, horizon_s: float) -> Dict[str, float]:
        return {
            "ice_concentration": float(np.clip(self._interp(self.ice_concentration, t_s, horizon_s), 0.0, 1.0)),
            "ice_thickness": float(max(self._interp(self.ice_thickness, t_s, horizon_s), 0.0)),
            "Hs": float(max(self._interp(self.Hs, t_s, horizon_s), 0.0)),
            "Tp": float(max(self._interp(self.Tp, t_s, horizon_s), 2.0)),
            "wind_ms": float(max(self._interp(self.wind_ms, t_s, horizon_s), 0.0)),
        }

    def perturbed(self, ice_rel: float = 0.0, hs_rel: float = 0.0) -> "EnvironmentForecast":
        """Return a copy with a relative forecast error (validation scenario)."""

        def scale(v, f):
            if np.isscalar(v):
                return float(v) * f
            return (np.asarray(v, dtype=float) * f).tolist()

        return EnvironmentForecast(
            ice_concentration=np.clip(np.asarray(scale(self.ice_concentration, 1 + ice_rel), dtype=float), 0, 1).tolist()
            if not np.isscalar(self.ice_concentration) else min(max(float(self.ice_concentration) * (1 + ice_rel), 0), 1),
            ice_thickness=scale(self.ice_thickness, 1 + ice_rel),
            Hs=scale(self.Hs, 1 + hs_rel),
            Tp=self.Tp,
            wind_ms=self.wind_ms,
        )


class OnlinePredictor:
    """Surrogate-driven forward simulator producing performance envelopes."""

    def __init__(
        self,
        registry: SurrogateRegistry,
        hull: HullForm,
        KG: float,
        operation: Optional[Mapping[str, float]] = None,
    ):
        self.registry = registry
        self.hull = hull
        self.KG = float(KG)
        self.op = {**DEFAULT_OPERATION, **dict(operation or {})}
        self.forecast_calls = 0
        self.last_forecast: Optional[Dict[str, Any]] = None

    # -- physics per step ---------------------------------------------------
    def _step_models(self, V: float, heading: float, env: Dict[str, float],
                     use_surrogate: bool) -> Dict[str, float]:
        if use_surrogate:
            res = self.registry.safe_predict("resistance", {
                "V": float(np.clip(V, 1.0, 9.4)),
                "ice_concentration": float(np.clip(env["ice_concentration"], 0.0, 1.0)),
                "ice_thickness": float(np.clip(env["ice_thickness"], 0.0, 1.2)),
            })["outputs"]
            sea = self.registry.safe_predict("seakeeping", {
                "Hs": float(np.clip(env["Hs"], 0.2, 8.0)),
                "Tp": float(np.clip(env["Tp"], 3.0, 14.0)),
                "heading_deg": float(np.clip(heading, 90.0, 180.0)),
                "V": float(np.clip(V, 1.0, 9.4)),
            })["outputs"]
            fat = self.registry.safe_predict("fatigue", {
                "Hs": float(np.clip(env["Hs"], 0.2, 8.0)),
                "Tp": float(np.clip(env["Tp"], 3.0, 14.0)),
                "ice_concentration": float(np.clip(env["ice_concentration"], 0.0, 1.0)),
                "ice_thickness": float(np.clip(env["ice_thickness"], 0.0, 1.2)),
            })["outputs"]
            R_calm = res["R_calm_kN"]
            R_ice = res["R_ice_kN"]
            R_aw = sea["R_aw_kN"]
            dmg = fat["damage_per_day"]
            heave_rms = sea["heave_rms_m"]
        else:
            r = total_resistance(V, self.hull.L, self.hull.B, self.hull.T, self.hull.Cb,
                                 env["ice_concentration"], env["ice_thickness"], 0.0)
            sea = irregular_seastate(env["Hs"], env["Tp"], heading, self.hull, self.KG, V,
                                     n_freq=20, n_sections=41)
            fat = spectral_fatigue(self.hull, env["Hs"], env["Tp"], U_knots=V / 0.5144,
                                   ice_concentration=env["ice_concentration"],
                                   ice_thickness=env["ice_thickness"])
            R_calm, R_ice = r["R_calm_N"] / 1e3, r["R_ice_N"] / 1e3
            R_aw = sea["R_aw_kN"]
            dmg = fat["damage_per_day"]
            heave_rms = sea["heave_rms_m"]
        return {"R_calm_kN": R_calm, "R_ice_kN": R_ice, "R_aw_kN": R_aw,
                "R_total_kN": R_calm + R_ice + R_aw, "damage_per_day": dmg,
                "heave_rms_m": heave_rms}

    def _solve_speed(self, V_desired: float, heading: float, env: Dict[str, float],
                     use_surrogate: bool) -> Dict[str, float]:
        """Deliver the highest feasible speed under the installed-power limit."""
        m = self._step_models(V_desired, heading, env, use_surrogate)
        P = m["R_total_kN"] * V_desired / self.op["eta_D"]      # kW (kN*m/s = kW)
        if P <= self.op["power_limit_kW"]:
            return {**m, "V_ms": V_desired, "P_D_kW": P, "limited": 0.0}
        lo, hi = 1.0, V_desired
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            mm = self._step_models(mid, heading, env, use_surrogate)
            pm = mm["R_total_kN"] * mid / self.op["eta_D"]
            if pm > self.op["power_limit_kW"]:
                hi = mid
            else:
                lo = mid
        V = 0.5 * (lo + hi)
        mm = self._step_models(V, heading, env, use_surrogate)
        return {**mm, "V_ms": V, "P_D_kW": mm["R_total_kN"] * V / self.op["eta_D"],
                "limited": 1.0}

    # -- forecast -----------------------------------------------------------
    def forecast(
        self,
        state: Mapping[str, float],
        env: EnvironmentForecast,
        plan: VoyagePlan,
        horizon_h: float = 1.0,
        dt_s: float = 300.0,
        use_surrogate: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        gm0 = float(state.get("gm_m", 1.5))
        r_bias = float(state.get("resistance_bias", 1.0))
        d_scale = float(state.get("damage_scale", 1.0))
        horizon_s = float(horizon_h) * 3600.0
        n = max(int(round(horizon_s / dt_s)), 1)
        dt = horizon_s / n
        disp_t = self.hull.volume * self.hull.rho / 1000.0

        times, power, speed, fuel, damage, gm, gm_margin, heave = ([] for _ in range(8))
        fuel_kg = 0.0
        dmg_total = 0.0
        icing_kg = 0.0
        limited_steps = 0
        for k in range(n + 1):
            t_s = k * dt
            e = env.sample(t_s, horizon_s)
            plan_s = plan.sample(t_s)
            step = self._solve_speed(plan_s["speed_ms"], plan_s["heading_deg"], e, use_surrogate)
            R_tot = step["R_total_kN"] * r_bias
            P = R_tot * step["V_ms"] / self.op["eta_D"]
            if k > 0:
                fuel_kg += P * self.op["sfoc_g_per_kWh"] / 1000.0 * (dt / 3600.0)
                dmg_total += d_scale * step["damage_per_day"] * dt / 86400.0
                icing_kg += (self.op["icing_rate_kg_m2_h"] * e["ice_concentration"]
                             * self.op["deck_area_m2"] * dt / 3600.0)
            # dGM = -m_ice * (z_ice - KG) / displacement  [m]
            gm_value = gm0 - icing_kg * (self.op["icing_KG_m"] - self.KG) / (disp_t * 1000.0) \
                - 0.02 * (t_s / 86400.0)
            times.append(t_s)
            power.append(P)
            speed.append(step["V_ms"])
            fuel.append(fuel_kg)
            damage.append(dmg_total)
            gm.append(gm_value)
            gm_margin.append(gm_value - 0.15)
            heave.append(step["heave_rms_m"])
            limited_steps += int(step["limited"])

        power_a = np.asarray(power)
        speed_a = np.asarray(speed)
        gm_a = np.asarray(gm)
        summary = {
            "horizon_h": float(horizon_h),
            "n_steps": n,
            "dt_s": dt,
            "mean_power_kW": float(power_a.mean()),
            "peak_power_kW": float(power_a.max()),
            "fuel_kg": float(fuel_kg),
            "fuel_t": float(fuel_kg / 1000.0),
            "damage_increment": float(dmg_total),
            "damage_life_years": float(1.0 / (dmg_total * 365.25 / max(horizon_h / 24.0, 1e-6)))
            if dmg_total > 0 else float("inf"),
            "min_GM_m": float(gm_a.min()),
            "min_GM_margin_m": float(gm_a.min() - 0.15),
            "mean_speed_ms": float(speed_a.mean()),
            "speed_loss_pct": float(100.0 * (1.0 - speed_a.mean() / max(plan.speed_ms, 1e-9))),
            "power_limited_fraction": float(limited_steps / (n + 1)),
            "icing_accreted_kg": float(icing_kg),
            "mean_heave_rms_m": float(np.mean(heave)),
            "stability_hazard": bool(gm_a.min() < 0.15),
            "fatigue_hazard": bool(dmg_total * 365.25 / max(horizon_h / 24.0, 1e-6) > 0.1),
            "model": "surrogate" if use_surrogate else "reference",
            "elapsed_ms": round((time.perf_counter() - t0) * 1e3, 3),
        }
        result = {
            "summary": summary,
            "timeline": {
                "time_s": times, "power_kW": power, "speed_ms": speed,
                "fuel_cum_kg": fuel, "damage_cum": damage, "GM_m": gm,
                "GM_margin_m": gm_margin, "heave_rms_m": heave,
            },
            "state_used": {"gm_m": gm0, "resistance_bias": r_bias, "damage_scale": d_scale},
            "environment": {
                "ice_concentration": env.ice_concentration if np.isscalar(env.ice_concentration) else list(np.asarray(env.ice_concentration, dtype=float)),
                "ice_thickness": env.ice_thickness if np.isscalar(env.ice_thickness) else list(np.asarray(env.ice_thickness, dtype=float)),
                "Hs": env.Hs if np.isscalar(env.Hs) else list(np.asarray(env.Hs, dtype=float)),
                "Tp": env.Tp if np.isscalar(env.Tp) else list(np.asarray(env.Tp, dtype=float)),
            },
        }
        self.forecast_calls += 1
        self.last_forecast = result
        return result

    # -- validation against the reference ("truth") simulation --------------
    def validate(
        self,
        state: Mapping[str, float],
        env_truth: EnvironmentForecast,
        plan: VoyagePlan,
        horizon_h: float = 1.0,
        ice_forecast_error: float = 0.03,
        hs_forecast_error: float = 0.05,
        dt_truth_s: float = 60.0,
        dt_forecast_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Score the surrogate forecast against the fine reference simulation."""
        truth = self.forecast(state, env_truth, plan, horizon_h, dt_truth_s, use_surrogate=False)
        env_fc = env_truth.perturbed(ice_rel=ice_forecast_error, hs_rel=hs_forecast_error)
        fc = self.forecast(state, env_fc, plan, horizon_h, dt_forecast_s, use_surrogate=True)
        # primary performance channels (criterion d)
        keys = ["mean_power_kW", "fuel_kg", "mean_speed_ms", "min_GM_margin_m"]
        rel, rel_secondary = {}, {}
        for k in keys:
            tv, fv = truth["summary"][k], fc["summary"][k]
            rel[k] = abs(fv - tv) / abs(tv) if abs(tv) > 1e-12 else float("nan")
        for k in ["damage_increment", "peak_power_kW"]:
            tv, fv = truth["summary"][k], fc["summary"][k]
            rel_secondary[k] = abs(fv - tv) / abs(tv) if abs(tv) > 1e-12 else float("nan")
        mare = float(np.nanmean([abs(v) for v in rel.values()]))
        return {
            "horizon_h": horizon_h,
            "truth": truth["summary"],
            "forecast": fc["summary"],
            "relative_errors": rel,
            "secondary_relative_errors": rel_secondary,
            "mare": mare,
            "mare_definition": "mean |forecast-truth|/|truth| over mean power, fuel, "
                               "mean speed and GM margin at the given horizon",
            "ice_forecast_error": ice_forecast_error,
            "hs_forecast_error": hs_forecast_error,
            "criterion_met": bool(mare < 0.10),
            "truth_dt_s": dt_truth_s,
            "forecast_dt_s": dt_forecast_s,
        }