"""Extended Kalman filter fusing sensor data with surrogate predictions.

State vector (3 states)::

    x = [ GM_eff (m),  resistance_bias (-),  damage_rate_scale (-) ]

The resistance and damage "biases" absorb hull/propeller fouling, ice damage,
loading condition and sensor calibration offsets; they are the physically
interpretable quantities a digital twin needs in order to make the surrogate
predictions track the real ship.

Process model: random walk (``F = I``, ``Q = diag(q^2) * dt``).
Measurement model (non-linear, evaluated with the surrogate registry)::

    z1 = GM_eff                                     (roll-period / inclining sensor)
    z2 = r_bias * R_model(V, ice)                   (shaft power / speed)
    z3 = d_scale * D_model(Hs, Tp, ice)              (strain-gauge damage rate)

The Jacobian is analytic (the measurement model is linear in each state), so the
filter is a genuine EKF with a scalar non-linearity per channel.  Innovation
gating (Mahalanobis distance) rejects sensor spikes, and the covariance is
Joseph-stabilised.

Acceptance: with 2-3 % sensor noise the GM estimate converges inside 50 steps to
an RMSE below 5 % of the true value (tested and reported).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

from .models.fatigue import damage_from_stress_rms, stress_from_damage
from .models.geometry import HullForm
from .models.resistance import total_resistance
from .surrogate_registry import SurrogateRegistry, get_registry
from .types import InputValidationError

STATE_NAMES = ["gm_m", "resistance_bias", "damage_scale"]
MEASUREMENT_NAMES = ["gm_meas_m", "resistance_meas_kN", "damage_meas_per_day"]


@dataclass
class Measurement:
    """One synchronised sensor snapshot (SI / engineering units)."""

    gm_meas_m: Optional[float] = None
    speed_ms: Optional[float] = None
    shaft_power_kW: Optional[float] = None
    strain_rms_MPa: Optional[float] = None
    peak_period_s: float = 8.0
    ice_concentration: float = 0.0
    ice_thickness: float = 0.0
    Hs: float = 2.0
    Tp: float = 8.0
    timestamp_s: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class ExtendedKalmanFilter:
    """3-state EKF with surrogate-based non-linear measurement model."""

    def __init__(
        self,
        registry: SurrogateRegistry,
        hull: HullForm,
        x0: Optional[np.ndarray] = None,
        P0: Optional[np.ndarray] = None,
        q: Optional[np.ndarray] = None,
        r_rel: Optional[np.ndarray] = None,
        eta_D: float = 0.66,
        gate_sigma: float = 5.0,
    ):
        self.registry = registry
        self.hull = hull
        self.x = np.array(x0 if x0 is not None else [1.5, 1.0, 1.0], dtype=float)
        self.P = np.array(P0 if P0 is not None else np.diag([0.25, 0.05, 0.09]), dtype=float)
        self.q = np.array(q if q is not None else [5e-4, 3e-4, 5e-4], dtype=float)
        # relative variances: GM 3 %, resistance 3.2 %, damage 10 % (stress noise 3 %
        # is amplified by ~m=3.5 through the S-N curve)
        self.r_rel = np.array(r_rel if r_rel is not None else [9e-4, 1e-3, 1e-2], dtype=float)
        self.eta_D = float(eta_D)
        self.gate_sigma = float(gate_sigma)
        self.history: List[Dict[str, Any]] = []
        self.rejected = 0
        self.updates = 0
        self.last_innovation: Optional[np.ndarray] = None

    # -- process ------------------------------------------------------------
    def predict(self, dt: float) -> np.ndarray:
        dt = float(max(dt, 0.0))
        self.P = self.P + np.diag(self.q ** 2) * dt
        return self.x.copy()

    # -- measurement model --------------------------------------------------
    def _models(self, m: Measurement) -> Dict[str, float]:
        V = float(m.speed_ms if m.speed_ms is not None else 6.0)
        res = self.registry.safe_predict("resistance", {
            "V": max(V, 1.0),
            "ice_concentration": float(np.clip(m.ice_concentration, 0.0, 1.0)),
            "ice_thickness": float(np.clip(m.ice_thickness, 0.0, 1.2)),
        })["outputs"]
        R_model = float(res["R_total_kN"]) + 1e-6
        dmg = self.registry.safe_predict("fatigue", {
            "Hs": float(np.clip(m.Hs, 0.2, 8.0)),
            "Tp": float(np.clip(m.Tp, 3.0, 14.0)),
            "ice_concentration": float(np.clip(m.ice_concentration, 0.0, 1.0)),
            "ice_thickness": float(np.clip(m.ice_thickness, 0.0, 1.2)),
        })["outputs"]
        return {"R_model_kN": R_model, "D_model_per_day": max(float(dmg["damage_per_day"]), 1e-12)}

    def _innovation(self, m: Measurement) -> Dict[str, Any]:
        models = self._models(m)
        h = np.array([
            self.x[0],
            self.x[1] * models["R_model_kN"],
            self.x[2] * models["D_model_per_day"],
        ])
        z = np.full(3, np.nan)
        present = np.zeros(3, dtype=bool)
        if m.gm_meas_m is not None:
            z[0], present[0] = float(m.gm_meas_m), True
        if m.shaft_power_kW is not None and m.speed_ms:
            # P_D = R V / eta -> R = eta P / V ; kN
            z[1] = self.eta_D * float(m.shaft_power_kW) / max(float(m.speed_ms), 0.5)
            present[1] = True
        if m.strain_rms_MPa is not None:
            z[2] = damage_from_stress_rms(float(m.strain_rms_MPa),
                                          max(float(m.Tp) / 1.4, 1.0))
            present[2] = True
        return {"h": h, "z": z, "present": present, "models": models}

    # -- update -------------------------------------------------------------
    def update(self, m: Measurement) -> Dict[str, Any]:
        innov = self._innovation(m)
        h, z, present = innov["h"], innov["z"], innov["present"]
        if not present.any():
            raise InputValidationError("measurement contains none of the supported channels")

        H = np.zeros((3, 3))
        R = np.zeros((3, 3))
        idx = []
        for i in range(3):
            if not present[i]:
                continue
            idx.append(i)
            if i == 0:
                H[i, 0] = 1.0
                R[i, i] = self.r_rel[0] * max(self.x[0], 0.2) ** 2
            elif i == 1:
                H[i, 1] = innov["models"]["R_model_kN"]
                R[i, i] = (self.r_rel[1] * abs(z[1])) ** 2
            else:
                H[i, 2] = innov["models"]["D_model_per_day"]
                R[i, i] = (self.r_rel[2] * abs(z[2])) ** 2

        Hr, Rr, zr, hr = H[idx], R[np.ix_(idx, idx)], z[idx], h[idx]
        y = zr - hr
        S_full = Hr @ self.P @ Hr.T + Rr
        # per-channel innovation gate: drop outlier channels, keep the rest
        keep = []
        for j in range(len(idx)):
            s_jj = float(S_full[j, j])
            if s_jj <= 0 or abs(y[j]) <= self.gate_sigma * math.sqrt(s_jj):
                keep.append(j)
        if not keep:
            self.rejected += 1
            return {"accepted": False, "reason": "innovation gate",
                    "innovation": y.tolist(), **innov}
        if len(keep) < len(idx):
            self.rejected += 1
            Hr = Hr[keep]
            Rr = Rr[np.ix_(keep, keep)]
            zr = zr[keep]
            hr = hr[keep]
            y = y[keep]
        S = Hr @ self.P @ Hr.T + Rr
        try:
            Sinv = np.linalg.inv(S)
        except np.linalg.LinAlgError:
            return {"accepted": False, "reason": "singular innovation covariance", **innov}
        d2 = float(y @ Sinv @ y)
        K = self.P @ Hr.T @ Sinv
        self.x = self.x + K @ y
        I_KH = np.eye(3) - K @ Hr
        self.P = I_KH @ self.P @ I_KH.T + K @ Rr @ K.T      # Joseph form
        self.P = 0.5 * (self.P + self.P.T)
        # physical bounds
        self.x[0] = float(np.clip(self.x[0], 0.05, 6.0))
        self.x[1] = float(np.clip(self.x[1], 0.4, 2.5))
        self.x[2] = float(np.clip(self.x[2], 0.05, 20.0))
        self.updates += 1
        self.last_innovation = y
        rec = {"accepted": True, "innovation": y.tolist(),
               "state": self.x.tolist(), "P_diag": np.diag(self.P).tolist(), **innov}
        self.history.append(rec)
        return rec

    def step(self, dt: float, m: Measurement) -> Dict[str, Any]:
        self.predict(dt)
        return self.update(m)

    @property
    def state(self) -> Dict[str, float]:
        return {n: float(v) for n, v in zip(STATE_NAMES, self.x)}

    def state_std(self) -> Dict[str, float]:
        return {n: float(math.sqrt(max(p, 0.0))) for n, p in zip(STATE_NAMES, np.diag(self.P))}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "state_std": self.state_std(),
            "updates": self.updates,
            "rejected": self.rejected,
            "last_innovation": None if self.last_innovation is None else self.last_innovation.tolist(),
        }


# ---------------------------------------------------------------------------
# convergence study (acceptance criterion c)
# ---------------------------------------------------------------------------

def convergence_study(
    registry: SurrogateRegistry,
    hull: HullForm,
    n_steps: int = 120,
    dt: float = 60.0,
    seed: int = 20261031,
    sensor_noise: float = 0.03,
    truth0: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Synthetic closed-loop: truth random walk + noisy sensors -> EKF.

    Returns the normalised RMSE of every state over the *last* ``n_steps-50``
    steps (steady-state accuracy) and the step index at which the GM error first
    stays below 5 % of the truth.
    """
    rng = np.random.default_rng(seed)
    truth = dict(truth0 or {"gm_m": 1.45, "resistance_bias": 1.08, "damage_scale": 1.10})
    reg = registry
    ekf = ExtendedKalmanFilter(
        reg, hull,
        x0=np.array([truth["gm_m"] * 1.6, 1.0, 1.0]),
        P0=np.diag([1.0, 0.04, 0.25]),
    )
    times, gm_true, gm_est, gm_std, rb_true, rb_est, ds_true, ds_est = ([] for _ in range(8))
    rejected = 0
    for k in range(n_steps):
        # truth random walk
        truth["gm_m"] += rng.normal(0.0, 0.004)
        truth["resistance_bias"] += rng.normal(0.0, 0.0025)
        truth["damage_scale"] += rng.normal(0.0, 0.004)
        truth["gm_m"] = float(np.clip(truth["gm_m"], 0.4, 3.5))
        truth["resistance_bias"] = float(np.clip(truth["resistance_bias"], 0.7, 1.6))
        truth["damage_scale"] = float(np.clip(truth["damage_scale"], 0.6, 2.0))

        ice_c = 0.4 + 0.3 * math.sin(k / 18.0)
        Hs = 3.0 + 0.8 * math.sin(k / 9.0)
        V = 6.0 + 0.3 * math.sin(k / 5.0)
        m = Measurement(
            gm_meas_m=truth["gm_m"] * (1.0 + rng.normal(0.0, sensor_noise)),
            speed_ms=V * (1.0 + rng.normal(0.0, 0.004)),
            ice_concentration=ice_c,
            ice_thickness=0.5,
            Hs=Hs, Tp=8.0, peak_period_s=6.5,
            timestamp_s=k * dt,
        )
        models = ekf._models(m)
        R_model = models["R_model_kN"]
        m.shaft_power_kW = truth["resistance_bias"] * R_model * m.speed_ms / ekf.eta_D \
            * (1.0 + rng.normal(0.0, sensor_noise))
        dmg_true = truth["damage_scale"] * models["D_model_per_day"]
        # strain sensor consistent with the true damage rate
        sigma_equiv = stress_from_damage(dmg_true, max(m.Tp / 1.4, 1.0))
        m.strain_rms_MPa = sigma_equiv * (1.0 + rng.normal(0.0, sensor_noise))

        rec = ekf.step(dt, m)
        if not rec["accepted"]:
            rejected += 1
        times.append(k * dt)
        gm_true.append(truth["gm_m"]); gm_est.append(ekf.x[0]); gm_std.append(math.sqrt(ekf.P[0, 0]))
        rb_true.append(truth["resistance_bias"]); rb_est.append(ekf.x[1])
        ds_true.append(truth["damage_scale"]); ds_est.append(ekf.x[2])

    gm_true, gm_est = np.array(gm_true), np.array(gm_est)
    rb_true, rb_est = np.array(rb_true), np.array(rb_est)
    ds_true, ds_est = np.array(ds_true), np.array(ds_est)
    tail = slice(max(50, n_steps // 4), None)
    rmse_gm = float(np.sqrt(np.mean((gm_est[tail] - gm_true[tail]) ** 2)))
    rmse_rb = float(np.sqrt(np.mean((rb_est[tail] - rb_true[tail]) ** 2)))
    rmse_ds = float(np.sqrt(np.mean((ds_est[tail] - ds_true[tail]) ** 2)))
    rel_gm = rmse_gm / float(np.mean(np.abs(gm_true[tail])) or 1.0)
    rel_rb = rmse_rb / float(np.mean(np.abs(rb_true[tail])) or 1.0)
    rel_ds = rmse_ds / float(np.mean(np.abs(ds_true[tail])) or 1.0)

    err = np.abs(gm_est - gm_true) / np.abs(gm_true)
    steps_to_5pct = None
    for k in range(len(err)):
        if np.all(err[k:] < 0.05):
            steps_to_5pct = k
            break
    return {
        "n_steps": n_steps,
        "dt_s": dt,
        "sensor_noise": sensor_noise,
        "rmse_gm_m": rmse_gm,
        "rmse_resistance_bias": rmse_rb,
        "rmse_damage_scale": rmse_ds,
        "rel_rmse_gm": rel_gm,
        "rel_rmse_resistance_bias": rel_rb,
        "rel_rmse_damage_scale": rel_ds,
        "steps_to_5pct_gm": steps_to_5pct,
        "converged_within_50_steps": bool(steps_to_5pct is not None and steps_to_5pct <= 50),
        "rejected_updates": rejected,
        "final_state": ekf.state,
        "final_state_std": ekf.state_std(),
        "truth_end": truth,
        "series": {
            "time_s": times,
            "gm_true": gm_true.tolist(), "gm_est": gm_est.tolist(),
            "gm_std": gm_std,
            "resistance_bias_true": rb_true.tolist(), "resistance_bias_est": rb_est.tolist(),
            "damage_scale_true": ds_true.tolist(), "damage_scale_est": ds_est.tolist(),
        },
    }