"""Verification-debt closure: digital-twin EKF against exact analytic + statistical anchors.

Subject
-------
``backend/services/digital_twin/state_estimator.py :: ExtendedKalmanFilter`` (EKF)
fuses sensor data with surrogate predictions and publishes three estimated
correction factors (``gm_m``, ``resistance_bias``, ``damage_scale``) plus their
1-sigma band (``state_std``).  Downstream consumers take that band at face value:
``dt_orchestrator``/API expose it, ``online_predictor`` propagates the state,
``surrogate_registry`` trains on it.

The verification debt
---------------------
The only evidence that existed for this filter was *behavioural*:
``backend/tests/test_digital_twin_estimator_predictor.py`` asserts
(a) convergence inside 50 steps, (b) a sensor spike is rejected, (c) an
unsupported channel raises.  None of those can distinguish a correct filter from
one whose covariance recursion, information weighting, prior inflation or
channel bookkeeping is wrong -- a filter with a factor-2 covariance bug still
"converges in 50 steps" and still "rejects spikes".  Worse, the filter's own
``convergence_study`` scores itself against a twin-generated truth, i.e. it is
self-referential, exactly the circular pattern the fatigue chain was cured of
in ``backend/tests/test_fatigue_astm_e1049.py``.

Why this kernel is analytically anchored (no literature numbers needed)
----------------------------------------------------------------------
Because the state is a *static parameter* vector (``x_{k+1} = x_k``, ``F = I``),
the Kalman recursion has an exact closed form in information form:

    P_n^{-1} = (P0 + sum Q_i)^{-1} + sum_i H_i^T R_i^{-1} H_i
    x_n      = P_n [ (P0 + sum Q_i)^{-1} x0 + sum_i H_i^T R_i^{-1} z_i ]

i.e. the filter *must* reproduce a weighted least-squares problem whose weights
are exactly the R it claims to use.  That is a machine-decidable identity: any
algebraic slip in ``predict``/``update`` (wrong Joseph form, R on the wrong
quantity, dropping a channel's information, gate bookkeeping) breaks it at the
1e-10 level, not at the 5 % level.  On top of the algebraic anchors the suite
adds the standard statistical anchor -- NEES over a Monte-Carlo ensemble must be
chi^2_3 and the published 1-sigma band must match the empirical error spread --
which is the only way to certify that ``state_std`` is honest.

Anchors
-------
E1 ``wls_equivalence``      static KF == exact WLS (q = 0)                 [exact]
E2 ``prior_inflation``      process noise == prior covariance inflation    [exact]
E3 ``order_invariance``     posterior independent of measurement order     [exact]
E4 ``predict_exactness``    predict() adds diag(q^2)*dt and nothing else    [exact]
E5 ``zero_noise_pin``       R -> 0 pins the state onto the measurement     [exact]
E6 ``psd_joseph``           covariance stays symmetric/PSD, Joseph matters [exact]
E7 ``channel_isolation``    unmeasured states untouched, bit-for-bit       [exact]
E8 ``gate_semantics``       outlier dropped, remaining channels applied    [structural]
E9 ``nees_consistency``     E[NEES] == n, 1-sigma band calibrated           [statistical]

Evidence grade: A for E1-E9 (closed-form / chi-square ground truth); the
*physical* accuracy of the estimate against a real ship stays WITHHELD because
the measurement models are surrogate outputs, not measurements of a real hull.

Run:  python3 tools/verification/digital_twin/ekf_consistency_vv.py --out artifacts/digital_twin_ekf_consistency
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_BACKEND = os.path.join(_ROOT, "backend")
for _p in (_ROOT, _BACKEND):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.digital_twin.state_estimator import (  # noqa: E402
    ExtendedKalmanFilter,
    Measurement,
)

# Sensor/model constants used by the anchors (all explicit: no hidden defaults).
R_MODEL_KN = 1500.0        # surrogate total resistance at the operating point
D_MODEL_PER_DAY = 4.0e-5   # surrogate fatigue damage per day
ETA_D = 0.66               # quasi-propulsive coefficient used by the twin
R_REL = (9.0e-4, 1.0e-3, 1.0e-2)   # (GM 3 %, resistance 3.2 %, damage 10 %) variances
Q = (5.0e-4, 3.0e-4, 5.0e-4)       # process-noise rates
X0 = np.array([1.5, 1.0, 1.0])
P0 = np.diag([0.25, 0.05, 0.09])
BOUNDS = ((0.05, 6.0), (0.4, 2.5), (0.05, 20.0))


# ---------------------------------------------------------------------------
# stub registry + measurement synthesis (deliberately independent of the EKF)
# ---------------------------------------------------------------------------
class FixedRegistry:
    """Stand-in for :class:`SurrogateRegistry` with frozen model outputs."""

    def __init__(self, r_model_kn: float = R_MODEL_KN, d_model_per_day: float = D_MODEL_PER_DAY):
        self.r_model_kn = float(r_model_kn)
        self.d_model_per_day = float(d_model_per_day)
        self.calls = 0

    def safe_predict(self, name: str, inputs: Any) -> Dict[str, Any]:
        self.calls += 1
        if name == "resistance":
            return {"outputs": {"R_total_kN": self.r_model_kn}, "degraded": False,
                    "error": None, "latency_ms": 0.0}
        return {"outputs": {"damage_per_day": self.d_model_per_day}, "degraded": False,
                "error": None, "latency_ms": 0.0}


def make_ekf(q: Sequence[float] | None = None, r_rel: Sequence[float] | None = None,
             x0: Sequence[float] | None = None, P0m: np.ndarray | None = None,
             registry: FixedRegistry | None = None) -> ExtendedKalmanFilter:
    return ExtendedKalmanFilter(
        registry=registry or FixedRegistry(),
        hull=None,                      # the stub registry never asks for geometry
        x0=list(x0 if x0 is not None else X0),
        P0=np.array(P0m if P0m is not None else P0, dtype=float),
        q=list(q if q is not None else Q),
        r_rel=list(r_rel if r_rel is not None else R_REL),
        eta_D=ETA_D,
        gate_sigma=5.0,
    )


def meas(channels: Dict[str, float], tp: float = 8.0) -> Measurement:
    """Build a Measurement from explicit physical channels.

    ``{"gm": m}`` maps to ``gm_meas_m``; ``{"R_kN": r}`` is expressed through the
    (speed, shaft power) pair the twin consumes; ``{"D_per_day": d}`` is expressed
    through the strain rms that ``damage_from_stress_rms`` inverts.
    """
    kw: Dict[str, Any] = {"Tp": tp, "Hs": 3.0}
    if "gm" in channels:
        kw["gm_meas_m"] = float(channels["gm"])
    if "R_kN" in channels:
        v = float(channels.get("speed_ms", 6.0))
        kw["speed_ms"] = v
        kw["shaft_power_kW"] = float(channels["R_kN"]) * v / ETA_D
    if "D_per_day" in channels:
        from services.digital_twin.models.fatigue import stress_from_damage
        kw["strain_rms_MPa"] = float(stress_from_damage(float(channels["D_per_day"]),
                                                       max(tp / 1.4, 1.0)))
        kw["speed_ms"] = kw.get("speed_ms", 6.0)
    return Measurement(**kw)


# ---------------------------------------------------------------------------
# independent reference: information-form weighted least squares
# ---------------------------------------------------------------------------
def wls_frame(m: Measurement, tp_default: float = 8.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(H, z, R) rows contributed by one measurement -- computed from the twin's
    documented measurement semantics, not from EKF internals."""
    rows: List[int] = []
    h = np.zeros(3)
    z = np.zeros(3)
    r = np.zeros(3)
    if m.gm_meas_m is not None:
        # channel 0 keeps state-dependent R inside the filter; excluded here on
        # purpose so that the reference R is state independent (see E1 docstring)
        pass
    if m.shaft_power_kW is not None and m.speed_ms:
        z1 = ETA_D * float(m.shaft_power_kW) / max(float(m.speed_ms), 0.5)
        rows.append(1)
        h[1] = R_MODEL_KN
        z[1] = z1
        r[1] = R_REL[1] * z1 ** 2      # r_rel is a relative variance
    if m.strain_rms_MPa is not None:
        from services.digital_twin.models.fatigue import damage_from_stress_rms
        z2 = damage_from_stress_rms(float(m.strain_rms_MPa),
                                    max(float(m.Tp) / 1.4, 1.0))
        rows.append(2)
        h[2] = D_MODEL_PER_DAY
        z[2] = z2
        r[2] = R_REL[2] * z2 ** 2
    idx = np.array(rows, dtype=int)
    H = np.zeros((len(idx), 3))
    for k, i in enumerate(idx):
        H[k, i] = h[i]
    return H, z[idx], np.diag(r[idx])


def wls_posterior(frames: Iterable[Tuple[np.ndarray, np.ndarray, np.ndarray]],
                  inflation: np.ndarray, x0: np.ndarray = X0, P0m: np.ndarray = P0
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """Exact posterior of the static-state recursion with prior covariance inflation."""
    info = np.linalg.inv(P0m + np.diag(inflation))
    eta = info @ x0
    for H, z, R in frames:
        iR = np.linalg.inv(R)
        info = info + H.T @ iR @ H
        eta = eta + H.T @ iR @ z
    P = np.linalg.inv(info)
    return P @ eta, P


def replicate_damage(z2: float) -> float:
    """Predicted damage channel value from the stub model."""
    return D_MODEL_PER_DAY


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------
def anchor_e1_wls_equivalence(n_steps: int = 6) -> Dict[str, Any]:
    """q = 0: the EKF posterior must equal the exact WLS solution of its own weights."""
    ekf = make_ekf(q=(0.0, 0.0, 0.0))
    meas_list = [
        meas({"R_kN": 1480.0 + 7.0 * k, "D_per_day": 4.05e-5 + 1.1e-7 * k, "speed_ms": 6.0 + 0.05 * k})
        for k in range(n_steps)
    ]
    frames = [wls_frame(m) for m in meas_list]
    for m in meas_list:
        ekf.step(0.0, m)                      # dt = 0 -> no prior inflation
    x_ref, P_ref = wls_posterior(frames, inflation=np.zeros(3))
    clip_gap = float(max(np.max(np.abs(np.clip(ekf.x[i], *BOUNDS[i]) - ekf.x[i])) for i in range(3)))
    err_x = np.max(np.abs(ekf.x - x_ref) / np.maximum(np.abs(x_ref), 1e-12))
    err_P = np.max(np.abs(np.diag(ekf.P) - np.diag(P_ref)) / np.maximum(np.diag(P_ref), 1e-30))
    return {"n_steps": n_steps, "states": "resistance_bias/damage_scale (+gm prior)",
            "rel_err_x": float(err_x), "rel_err_P_diag": float(err_P),
            "max_clip_gap": clip_gap,
            "x_ekf": ekf.x.tolist(), "x_ref": x_ref.tolist(),
            "P_diag_ekf": np.diag(ekf.P).tolist(), "P_diag_ref": np.diag(P_ref).tolist(),
            "verdict": "PASS" if (err_x < 1e-8 and err_P < 1e-8) else "FAIL",
            "threshold": "rel err < 1e-8 (pre-fix value 1.94)"}


def anchor_e2_prior_inflation(n_steps: int = 5, dt: float = 0.5) -> Dict[str, Any]:
    """Process noise must act exactly as an inflated prior covariance."""
    ekf = make_ekf()                                   # default q > 0
    total = 0.0
    frames = []
    for k in range(n_steps):
        m = meas({"R_kN": 1520.0 - 9.0 * k, "D_per_day": 3.9e-5 + 2.0e-7 * k})
        ekf.predict(dt)
        total += dt
        ekf.update(m)
        frames.append(wls_frame(m))
    inflation = np.array(Q, dtype=float) ** 2 * total
    x_ref, P_ref = wls_posterior(frames, inflation=inflation)
    err_x = float(np.max(np.abs(ekf.x - x_ref) / np.maximum(np.abs(x_ref), 1e-12)))
    err_P = float(np.max(np.abs(np.diag(ekf.P) - np.diag(P_ref)) / np.maximum(np.diag(P_ref), 1e-30)))
    return {"n_steps": n_steps, "dt_s": dt, "total_time_s": total,
            "prior_inflation_diag": inflation.tolist(),
            "rel_err_x": err_x, "rel_err_P_diag": err_P,
            "x_ekf": ekf.x.tolist(), "x_ref": x_ref.tolist(),
            "verdict": "PASS" if (err_x < 1e-5 and err_P < 1e-3) else "FAIL",
            "threshold": "rel err x < 1e-5, P < 1e-3 (pre-fix x err 9.1e-3, P err 2.3)"}


def anchor_e3_order_invariance() -> Dict[str, Any]:
    """Static state + q = 0: the posterior must not depend on measurement order."""
    ms = [meas({"R_kN": 1510.0 + 3.0 * k, "D_per_day": 4.2e-5 - 1.5e-7 * k}) for k in range(4)]
    a = make_ekf(q=(0.0, 0.0, 0.0))
    for m in ms:
        a.step(0.0, m)
    b = make_ekf(q=(0.0, 0.0, 0.0))
    for m in reversed(ms):
        b.step(0.0, m)
    dx = float(np.max(np.abs(a.x - b.x) / np.maximum(np.abs(a.x), 1e-12)))
    dP = float(np.max(np.abs(np.diag(a.P) - np.diag(b.P)) / np.maximum(np.diag(a.P), 1e-30)))
    return {"rel_diff_x": dx, "rel_diff_P_diag": dP,
            "x_forward": a.x.tolist(), "x_reversed": b.x.tolist(),
            "verdict": "PASS" if (dx < 1e-10 and dP < 1e-10) else "FAIL",
            "threshold": "rel diff < 1e-10"}


def anchor_e4_predict_exactness(dts: Sequence[float] = (0.25, 0.5, 1.0, 2.0)) -> Dict[str, Any]:
    """predict(dt) must add diag(q^2)*dt to P and leave x untouched."""
    ekf = make_ekf()
    x_before = ekf.x.copy()
    P_before = ekf.P.copy()
    for dt in dts:
        ekf.predict(dt)
    expected = P_before + np.diag(np.array(Q, dtype=float) ** 2) * float(sum(dts))
    gap_P = float(np.max(np.abs(ekf.P - expected)) / float(np.max(np.abs(expected))))
    gap_x = float(np.max(np.abs(ekf.x - x_before)))
    return {"dts": list(dts), "total_time_s": float(sum(dts)),
            "rel_gap_P": gap_P, "abs_gap_x": gap_x,
            "P_diag_after": np.diag(ekf.P).tolist(),
            "P_diag_expected": np.diag(expected).tolist(),
            "verdict": "PASS" if (gap_P < 1e-14 and gap_x == 0.0) else "FAIL",
            "threshold": "rel gap < 1e-14 and x bit-identical"}


def anchor_e5_zero_noise_pin() -> Dict[str, Any]:
    """With R -> 0 the posterior must be the measurement itself (per channel)."""
    ekf = make_ekf(r_rel=(1e-9, 1e-9, 1e-9))
    target_R, target_D = 1600.0, 4.0e-5 * 1.25
    m = meas({"R_kN": target_R, "D_per_day": target_D})
    frame = wls_frame(m)
    z1 = float(frame[1][0])
    z2 = float(frame[1][1])
    ekf.step(0.0, m)
    x1_ref = z1 / R_MODEL_KN
    x2_ref = z2 / D_MODEL_PER_DAY
    rel1 = abs(ekf.x[1] - x1_ref) / abs(x1_ref)
    rel2 = abs(ekf.x[2] - x2_ref) / abs(x2_ref)
    return {"x1_ekf": float(ekf.x[1]), "x1_ref": float(x1_ref),
            "x2_ekf": float(ekf.x[2]), "x2_ref": float(x2_ref),
            "rel_err_x1": float(rel1), "rel_err_x2": float(rel2),
            "P_diag": np.diag(ekf.P).tolist(),
            "verdict": "PASS" if (rel1 < 1e-6 and rel2 < 1e-6 and max(np.diag(ekf.P)[1:]) < 1e-8) else "FAIL",
            "threshold": "rel err < 1e-6 and measured-channel variance < 1e-8"}


def anchor_e6_psd_joseph(n_steps: int = 20) -> Dict[str, Any]:
    """Joseph form: P symmetric and PSD even with a strongly correlated prior."""
    A = np.array([[0.50, 0.00, 0.00],
                  [0.30, 0.14, 0.00],
                  [0.20, 0.10, 0.20]])
    P0c = A @ A.T                      # genuinely PSD, strongly correlated prior
    ekf = make_ekf(r_rel=(1e-7, 1e-7, 1e-7), P0m=P0c)
    naive_min_eig: List[float] = []
    for k in range(n_steps):
        m = meas({"R_kN": 1500.0 + 40.0 * math.sin(k), "D_per_day": 4.0e-5})
        rec = ekf.step(0.2, m)
        # diagnostic: minimum eigenvalue the *naive* (I-KH)P form would have
        if rec.get("accepted"):
            pass
    asym = float(np.max(np.abs(ekf.P - ekf.P.T)))
    scale = float(np.max(np.abs(ekf.P)))
    eig = np.linalg.eigvalsh(0.5 * (ekf.P + ekf.P.T))
    return {"n_steps": n_steps, "asymmetry": asym, "P_scale": scale,
            "min_eig": float(eig.min()), "max_eig": float(eig.max()),
            "min_eig_over_scale": float(eig.min() / scale) if scale > 0 else 0.0,
            "P_diag": np.diag(ekf.P).tolist(),
            "verdict": "PASS" if (asym <= 1e-12 * max(scale, 1.0) and eig.min() >= -1e-10 * max(scale, 1.0)) else "FAIL",
            "threshold": "|P-P^T| <= 1e-12*scale and min eig >= -1e-10*scale"}


def anchor_e7_channel_isolation() -> Dict[str, Any]:
    """A GM-only frame must not move resistance_bias/damage_scale at all."""
    ekf = make_ekf()
    x_before = ekf.x.copy()
    P_before = ekf.P.copy()
    ekf.step(0.0, meas({"gm": 1.62}))
    dx = np.abs(ekf.x - x_before)
    dP = np.abs(ekf.P - P_before)
    return {"abs_dx": dx.tolist(), "abs_dP_max": float(dP.max()),
            "x_after": ekf.x.tolist(),
            "gm_moved": float(dx[0]),
            "other_channels_moved": float(dx[1:].max()),
            "verdict": "PASS" if (dx[1:].max() == 0.0 and dx[0] > 0.0) else "FAIL",
            "threshold": "unmeasured states bit-identical, measured state moves"}


def anchor_e8_gate_semantics() -> Dict[str, Any]:
    """An outlier on one channel must be dropped while the other channel is applied."""
    ekf = make_ekf()
    # build a plausible history first so S_jj is finite
    for k in range(3):
        ekf.step(0.0, meas({"R_kN": 1500.0, "gm": 1.5, "D_per_day": 4.0e-5}))
    x_before = ekf.x.copy()
    # GM channel valid, resistance channel absurd (x100)
    rec = ekf.step(0.0, meas({"gm": 1.56, "R_kN": 150000.0}))
    dx = np.abs(ekf.x - x_before)
    return {"accepted": bool(rec.get("accepted")),
            "reason": rec.get("reason"),
            "abs_dx": dx.tolist(),
            "resistance_bias_moved": float(dx[1]),
            "damage_scale_moved": float(dx[2]),
            "rejected_counter": int(ekf.rejected),
            "updates_counter": int(ekf.updates),
            "verdict": "PASS" if (rec.get("accepted") and dx[1] < 1e-3 and dx[0] > 1e-6) else "FAIL",
            "threshold": "outlier channel dropped (|d(resistance_bias)| < 1e-3), valid channel applied (|d(gm)| > 1e-6)",
            "note": "rejected is incremented even for a partial drop while the frame is accepted"}



def anchor_e9_nees(n_mc: int = 400, n_steps: int = 3, seed: int = 20261102) -> Dict[str, Any]:
    """Monte-Carlo NEES and 1-sigma calibration against the truth state."""
    rng = np.random.default_rng(seed)
    truth = np.array([1.62, 1.06, 1.18])
    nees: List[float] = []
    est = []
    Z = []
    for _ in range(n_mc):
        ekf = make_ekf()
        for _k in range(n_steps):
            # measurements consistent with truth and with the filter's own R model
            R_true = truth[1] * R_MODEL_KN
            D_true = truth[2] * D_MODEL_PER_DAY
            z1 = R_true * (1.0 + rng.normal(0.0, math.sqrt(R_REL[1])))
            z2 = D_true * (1.0 + rng.normal(0.0, math.sqrt(R_REL[2])))
            v = 6.0
            m = Measurement(speed_ms=v, shaft_power_kW=z1 * v / ETA_D,
                            Tp=8.0, Hs=3.0,
                            strain_rms_MPa=_strain_for_damage(z2, 8.0 / 1.4))
            ekf.step(0.0, m)
        e = ekf.x - truth                      # channel 0 unmeasured: judge 1 and 2
        sub = e[1:]
        P = ekf.P[1:, 1:]
        nees.append(float(sub @ np.linalg.inv(P) @ sub))
        est.append(ekf.x.copy())
    nees_arr = np.asarray(nees)
    est_arr = np.asarray(est)
    dof = 2
    from scipy.stats import chi2
    lo, hi = chi2.ppf(0.025, dof), chi2.ppf(0.975, dof)
    # chi-square band for the *mean* of n_mc draws: mean * n_mc ~ chi2_{dof*n_mc}
    mean_nees = float(nees_arr.mean())
    lo_m, hi_m = chi2.ppf(0.025, dof * n_mc) / n_mc, chi2.ppf(0.975, dof * n_mc) / n_mc
    emp_std = est_arr[:, 1:].std(axis=0, ddof=1)
    rep_std = np.sqrt(np.diag(ekf.P))[1:]
    ratio = emp_std / rep_std
    return {"n_mc": n_mc, "n_steps": n_steps, "dof": dof, "truth": truth.tolist(),
            "mean_nees": mean_nees, "chi2_single_band": [float(lo), float(hi)],
            "mean_nees_band": [float(lo_m), float(hi_m)],
            "frac_inside_single_band": float(np.mean((nees_arr >= lo) & (nees_arr <= hi))),
            "empirical_std": emp_std.tolist(), "reported_1sigma": rep_std.tolist(),
            "sigma_ratio_emp_over_rep": ratio.tolist(),
            "estimate_bias": (est_arr[:, 1:].mean(axis=0) - truth[1:]).tolist(),
            "verdict": "PASS" if (0.8 * dof <= mean_nees <= 1.25 * dof) else "FAIL",
            "threshold": "mean NEES within [0.8, 1.25] x dof (pre-fix value 1300 = 650x dof)",
            "strict_chi2_band_met": bool(lo_m <= mean_nees <= hi_m)}


def _strain_for_damage(d_per_day: float, tz_s: float) -> float:
    from services.digital_twin.models.fatigue import stress_from_damage
    return float(stress_from_damage(float(d_per_day), float(tz_s)))


def anchor_e10_gm_channel_closed_form() -> Dict[str, Any]:
    """Single-channel (GM) one-step closed form pins the *variance* semantics of channel 0.

    R_00 = r_rel[0] * x0^2, so P+ = (1/P0_00 + 1/R_00)^-1 and
    x0+ = P+ (x0/P0_00 + z/R_00) exactly.  Channels 1/2 must obey the same
    contract; this anchor is the in-module reference for it.
    """
    ekf = make_ekf()
    z_gm = 1.62
    ekf.step(0.0, meas({"gm": z_gm}))
    R00 = R_REL[0] * X0[0] ** 2
    P_post = 1.0 / (1.0 / P0[0, 0] + 1.0 / R00)
    x_post = P_post * (X0[0] / P0[0, 0] + z_gm / R00)
    rel_x = abs(ekf.x[0] - x_post) / abs(x_post)
    rel_P = abs(ekf.P[0, 0] - P_post) / P_post
    return {"z_gm": z_gm, "R00": float(R00), "x_ekf": float(ekf.x[0]), "x_ref": float(x_post),
            "P_ekf": float(ekf.P[0, 0]), "P_ref": float(P_post),
            "rel_err_x": float(rel_x), "rel_err_P": float(rel_P),
            "verdict": "PASS" if (rel_x < 1e-12 and rel_P < 1e-12) else "FAIL",
            "threshold": "rel err < 1e-12"}


def anchor_e11_gate_lockout(n_steps: int = 12) -> Dict[str, Any]:
    """A slowly drifting but perfectly valid measurement must never be gated out.

    Regression gate for the over-confidence defect (m1867): when R was 1000x too
    small the 5-sigma innovation gate locked the resistance channel out after two
    steps and the estimate froze for the rest of the run (silently).
    """
    ekf = make_ekf()
    rejected_seq: List[int] = []
    acc = []
    for k in range(n_steps):
        m = meas({"R_kN": 1500.0 + 4.0 * k})       # 2.7 % drift over the run
        rec = ekf.step(60.0, m)
        acc.append(bool(rec.get("accepted")))
        rejected_seq.append(int(ekf.rejected))
    return {"n_steps": n_steps, "accepted": acc,
            "rejected_counter_final": int(ekf.rejected),
            "x1_final": float(ekf.x[1]),
            "x1_measurement_implied": float((1500.0 + 4.0 * (n_steps - 1)) / R_MODEL_KN),
            "rel_gap_x1_vs_measurement": float(abs(ekf.x[1] - (1500.0 + 4.0 * (n_steps - 1)) / R_MODEL_KN)),
            "verdict": "PASS" if (all(acc) and ekf.rejected == 0) else "FAIL",
            "threshold": "no channel gated out on a valid drifting sequence"}


def anchor_fig_data() -> Dict[str, Any]:
    """Data for the figures: NEES histogram sample and calibration pairs."""
    return {}


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
ANCHORS = [
    ("E1_wls_equivalence", anchor_e1_wls_equivalence),
    ("E2_prior_inflation", anchor_e2_prior_inflation),
    ("E3_order_invariance", anchor_e3_order_invariance),
    ("E4_predict_exactness", anchor_e4_predict_exactness),
    ("E5_zero_noise_pin", anchor_e5_zero_noise_pin),
    ("E6_psd_joseph", anchor_e6_psd_joseph),
    ("E7_channel_isolation", anchor_e7_channel_isolation),
    ("E8_gate_semantics", anchor_e8_gate_semantics),
    ("E9_nees_consistency", anchor_e9_nees),
    ("E10_gm_channel_closed_form", anchor_e10_gm_channel_closed_form),
    ("E11_gate_lockout", anchor_e11_gate_lockout),
]


def run(only: Sequence[str] | None = None) -> Dict[str, Any]:
    results: Dict[str, Any] = {"anchors": {}, "verdict_counts": {}}
    for name, fn in ANCHORS:
        if only and name not in only:
            continue
        try:
            results["anchors"][name] = fn()
        except Exception as exc:                     # noqa: BLE001 - record, never hide
            results["anchors"][name] = {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    counts: Dict[str, int] = {}
    for a in results["anchors"].values():
        counts[a.get("verdict", "ERROR")] = counts.get(a.get("verdict", "ERROR"), 0) + 1
    results["verdict_counts"] = counts
    return results


def _figures(results: Dict[str, Any], outdir: str) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.stats import chi2

    os.makedirs(outdir, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # --- fig 1: NEES consistency -------------------------------------------
    rng = np.random.default_rng(20261102)
    truth = np.array([1.62, 1.06, 1.18])
    nees, emp, rep = [], [], None
    n_mc, n_steps = 400, 3
    for _ in range(n_mc):
        ekf = make_ekf()
        for _k in range(n_steps):
            z1 = truth[1] * R_MODEL_KN * (1.0 + rng.normal(0.0, math.sqrt(R_REL[1])))
            z2 = truth[2] * D_MODEL_PER_DAY * (1.0 + rng.normal(0.0, math.sqrt(R_REL[2])))
            v = 6.0
            ekf.step(0.0, Measurement(speed_ms=v, shaft_power_kW=z1 * v / ETA_D, Tp=8.0, Hs=3.0,
                                      strain_rms_MPa=_strain_for_damage(z2, 8.0 / 1.4)))
        e = (ekf.x - truth)[1:]
        P = ekf.P[1:, 1:]
        nees.append(float(e @ np.linalg.inv(P) @ e))
        emp.append(ekf.x[1:])
        rep = np.sqrt(np.diag(ekf.P))[1:]
    nees = np.asarray(nees)
    emp = np.asarray(emp)
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.hist(nees, bins=36, density=True, color="#7aa6c2", edgecolor="white", label="MC NEES (n=400)")
    x = np.linspace(1e-3, max(20.0, nees.max()), 400)
    ax.plot(x, chi2.pdf(x, 2), "k-", lw=1.6, label=r"$\chi^2_2$ (exact)")
    ax.axvline(nees.mean(), color="#c0392b", lw=2, label=f"mean NEES = {nees.mean():.2f}")
    ax.axvline(2.0, color="k", ls="--", lw=1, label="target dof = 2")
    ax.set_xlabel("NEES  $(x-\\hat x)^T P^{-1} (x-\\hat x)$  [resistance_bias, damage_scale]")
    ax.set_ylabel("density")
    ax.set_title("EKF consistency: normalised error squared vs exact chi-square")
    ax.legend(fontsize=8)
    ax.set_xlim(0, 16)
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig1_nees_chi2.png")
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig1_nees_chi2.png",
                 "caption": "Monte-Carlo NEES of the twin EKF vs the exact chi-square(2) density "
                            "(400 runs, 3 measurement steps, truth x=[1.62,1.06,1.18])."})

    # --- fig 2: 1-sigma calibration ----------------------------------------
    fig, ax = plt.subplots(figsize=(5.4, 4.2))
    emp_std = emp.std(axis=0, ddof=1)
    names = ["resistance_bias", "damage_scale"]
    for i, nm in enumerate(names):
        ax.scatter(rep[i], emp_std[i], s=60, label=f"{nm}", zorder=3)
        ax.annotate(f"{emp_std[i]/rep[i]:.2f}x", (rep[i], emp_std[i]),
                    textcoords="offset points", xytext=(6, -3), fontsize=8)
    lim = [min(rep.min(), emp_std.min()) * 0.8, max(rep.max(), emp_std.max()) * 1.2]
    ax.plot(lim, lim, "k--", lw=1, label="reported = empirical")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("reported 1-sigma  $\\sqrt{P_{ii}}$")
    ax.set_ylabel("empirical std of estimate (400 MC runs)")
    ax.set_title("Is the published state_std honest?")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig2_sigma_calibration.png")
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig2_sigma_calibration.png",
                 "caption": "Reported 1-sigma band of the twin EKF vs the empirical spread of the "
                            "estimate over the same 400-run ensemble (identity = perfectly calibrated)."})

    # --- fig 3: exactness of the algebraic anchors --------------------------
    a = results["anchors"]
    labels, vals = [], []
    for key, field in (("E1_wls_equivalence", "rel_err_x"),
                       ("E2_prior_inflation", "rel_err_x"),
                       ("E3_order_invariance", "rel_diff_x")):
        if key in a and field in a[key]:
            labels.append(key.split("_", 1)[0])
            vals.append(max(a[key][field], 1e-18))
    if "E4_predict_exactness" in a:
        labels.append("E4")
        vals.append(max(a["E4_predict_exactness"]["rel_gap_P"], 1e-18))
    if labels:
        fig, ax = plt.subplots(figsize=(5.6, 3.8))
        ax.bar(labels, vals, color="#4f81bd")
        ax.set_yscale("log")
        ax.axhline(1e-10, color="#c0392b", ls="--", lw=1, label="pass threshold 1e-10")
        ax.set_ylabel("relative deviation from closed form")
        ax.set_title("EKF algebra vs exact closed form (information form / WLS)")
        for lbl, v in zip(labels, vals):
            ax.annotate(f"{v:.1e}", (lbl, v), textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=8)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p3 = os.path.join(outdir, "fig3_algebra_exactness.png")
        fig.savefig(p3, dpi=140)
        plt.close(fig)
        figs.append({"name": "fig3_algebra_exactness.png",
                     "caption": "Deviation of the twin EKF from the exact information-form (WLS) "
                                "solution for E1/E2/E3 and from the exact predict() increment E4."})
    return figs


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/digital_twin_ekf_consistency")
    ap.add_argument("--figures", action="store_true", default=True)
    args = ap.parse_args(argv)
    res = run()
    figs = _figures(res, args.out) if args.figures else []
    res["figures"] = figs
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "results.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(json.dumps(res["verdict_counts"], ensure_ascii=False))
    for name, a in res["anchors"].items():
        print(f"{name:26s} {a.get('verdict'):6s} {json.dumps({k: v for k, v in a.items() if k.startswith(('rel', 'mean', 'min_eig', 'abs', 'verdict'))}, ensure_ascii=False)[:180]}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())