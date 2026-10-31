#!/usr/bin/env python3
"""Digital-twin acceptance benchmark (W3-N10).

Produces machine-checkable evidence for the five acceptance criteria:

  a) 5 surrogates registered behind one ``predict`` contract
  b) single-predict wall-clock P95 < 100 ms (200 calls per surrogate)
  c) EKF converges inside 50 steps, steady-state RMSE < 5 % of truth
  d) 1 h forecast vs fine reference simulation: MARE < 10 %
  e) pytest >= 15 passed  (executed separately, result recorded by the caller)

Outputs
-------
``artifacts/digital_twin/acceptance.json``  all criterion numbers + paths
``artifacts/digital_twin/physics_validation.json`` model-vs-baseline errors
``artifacts/digital_twin/figures/*.png``     report figures

Run:  python3 scripts/run_digital_twin_benchmark.py
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402

from services.digital_twin.models.fatigue import spectral_vs_rainflow   # noqa: E402
from services.digital_twin.models.geometry import HullForm              # noqa: E402
from services.digital_twin.models.manoeuvring import turning_circle     # noqa: E402
from services.digital_twin.models.resistance import (holtrop_resistance,  # noqa: E402
                                                     lindqvist_ice_resistance)
from services.digital_twin.models.seakeeping import irregular_seastate  # noqa: E402
from services.digital_twin.models.stability import kn_curve, wall_sided_gz  # noqa: E402
from services.digital_twin.online_predictor import (EnvironmentForecast,  # noqa: E402
                                                     OnlinePredictor, VoyagePlan)
from services.digital_twin.state_estimator import convergence_study      # noqa: E402
from services.digital_twin.surrogate_registry import get_registry         # noqa: E402

OUT = ROOT / "artifacts" / "digital_twin"
FIG = OUT / "figures"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

VESSEL = dict(L=180.0, B=32.0, T=11.5, Cb=0.80, KG=10.0)
SURROGATE_INPUTS = {
    "resistance": {"V": 6.0, "ice_concentration": 0.5, "ice_thickness": 0.5},
    "seakeeping": {"Hs": 4.0, "Tp": 8.0, "heading_deg": 180.0, "V": 6.0},
    "manoeuvring": {"V": 6.0, "rudder_deg": 30.0},
    "stability": {"heel_deg": 15.0, "KG": 10.0},
    "fatigue": {"Hs": 4.0, "Tp": 8.0, "ice_concentration": 0.5, "ice_thickness": 0.5},
}


def pct(v, n=4):
    return None if v is None else round(float(v), n)


def main() -> dict:
    hull = HullForm(VESSEL["L"], VESSEL["B"], VESSEL["T"], VESSEL["Cb"])
    t_build = time.perf_counter()
    reg = get_registry(VESSEL["L"], VESSEL["B"], VESSEL["T"], VESSEL["Cb"], VESSEL["KG"])
    build_s = time.perf_counter() - t_build

    # ---------------------------------------------------------------- (a)
    contracts = []
    for name in reg.names():
        s = reg.get(name)
        contracts.append({
            "name": name, "version": s.version, "reference_model": s.reference_model,
            "inputs": s.inputs.names, "outputs": s.outputs.names,
            "fit_r2": {k: pct(v, 6) for k, v in s.fit_report["r2"].items()},
            "n_basis": s.fit_report["n_basis"],
            "training_samples": s.fit_report["training_samples"],
        })

    # ---------------------------------------------------------------- (b)
    latency = {}
    sample_trace = {name: [] for name in reg.names()}
    for name, payload in SURROGATE_INPUTS.items():
        s = reg.get(name)
        s.latency.reset()
        for _ in range(200):
            t0 = time.perf_counter()
            reg.predict(name, payload)
            sample_trace[name].append((time.perf_counter() - t0) * 1e3)
        latency[name] = s.latency.stats()
    p95_all = [v["p95_ms"] for v in latency.values()]

    # ---------------------------------------------------------------- (c)
    conv = convergence_study(reg, hull, n_steps=100, sensor_noise=0.03)
    conv_5 = convergence_study(reg, hull, n_steps=100, sensor_noise=0.05)

    # ---------------------------------------------------------------- (d)
    predictor = OnlinePredictor(reg, hull, VESSEL["KG"])
    state = {"gm_m": 1.45, "resistance_bias": 1.05, "damage_scale": 1.05}
    env_moderate = EnvironmentForecast(ice_concentration=[0.28, 0.30, 0.33, 0.30],
                                       ice_thickness=0.30, Hs=[3.0, 3.3, 3.5, 3.2], Tp=8.0)
    env_heavy = EnvironmentForecast(ice_concentration=[0.60, 0.65, 0.62, 0.60],
                                    ice_thickness=0.60, Hs=[4.2, 4.6, 4.8, 4.4], Tp=9.0)
    plan = VoyagePlan(speed_ms=7.0, heading_deg=180.0)
    validations = {}
    for tag, env, horizon in (("moderate_ice_1h", env_moderate, 1.0),
                              ("moderate_ice_6h", env_moderate, 6.0),
                              ("heavy_ice_1h", env_heavy, 1.0)):
        validations[tag] = predictor.validate(state, env, plan, horizon_h=horizon)
    horizons = predictor.forecast_horizons.__self__.forecast_horizons if False else None
    fc_h = {}
    for h, dt in ((1.0, 300.0), (6.0, 600.0), (24.0, 1200.0)):
        fc_h[f"{h:g}h"] = predictor.forecast(state, env_moderate, plan, horizon_h=h, dt_s=dt)

    # ---------------------------------------------------------------- physics
    res7 = holtrop_resistance(7.0, hull.L, hull.B, hull.T, hull.Cb)
    res15 = holtrop_resistance(7.97, hull.L, hull.B, hull.T, hull.Cb)
    kcs = holtrop_resistance(12.35, 230.0, 32.2, 11.0, 0.651)
    ice6 = lindqvist_ice_resistance(2.57, hull.L, hull.B, hull.T, hull.Cb, 0.9, 0.6)
    sea = irregular_seastate(4.0, 8.0, 180.0, hull, VESSEL["KG"], 7.0)
    from services.digital_twin.models.seakeeping import long_wave_check
    lw = long_wave_check(hull, VESSEL["KG"], 7.0)
    kc = kn_curve(hull, [0.0, 10.0, 20.0, 30.0], KG=VESSEL["KG"])
    box = HullForm(100.0, 20.0, 6.0, 1.0)
    box_gm = 9.0
    box_wall = [(h, wall_sided_gz(box, h, box_gm)) for h in (2.0, 4.0, 6.0, 8.0)]
    box_kc = kn_curve(box, [h for h, _ in box_wall], KG=box_gm)
    box_err = [abs(g - w) / abs(w) for g, (_, w) in zip(box_kc["GZ_m"], box_wall)]
    fat_rf = spectral_vs_rainflow(n=150_000, dt=0.2, sigma_rms_MPa=10.0)
    turn35 = turning_circle(hull, 7.0, rudder_deg=35.0)
    turn20 = turning_circle(hull, 7.0, rudder_deg=20.0)
    geo = hull.verify()

    physics = {
        "geometry": {k: (pct(v, 10) if isinstance(v, float) else v) for k, v in geo.items()},
        "holtrop_design_point_7ms": {"RT_kN": pct(res7["RT_N"] / 1e3, 2),
                                     "RF_kN": pct(res7["RF_N"] / 1e3, 2),
                                     "RW_kN": pct(res7["RW_N"] / 1e3, 2),
                                     "Fn": pct(res7["Fn"]), "form_factor": pct(res7["form_factor"])},
        "holtrop_15_5kn": {"RT_kN": pct(res15["RT_N"] / 1e3, 2),
                           "P_D_kW": pct(res15["RT_N"] * 7.97 / 0.66 / 1e3, 1)},
        "lindqvist_0p6m_90pct_5kn": {k: (pct(v / 1e3, 2) if k.endswith("_N") else pct(v, 4))
                                     for k, v in ice6.items()},
        "seakeeping_Hs4_Tp8": {k: (pct(v, 4) if isinstance(v, float) else v)
                               for k, v in sea.items()},
        "seakeeping_long_wave": {k: pct(v, 8) for k, v in lw.items()},
        "stability_ship": {k: (pct(v, 5) if isinstance(v, float) else v)
                           for k, v in kc.items()},
        "stability_wall_sided_barge": {"heels": [h for h, _ in box_wall],
                                       "gz_numeric": [pct(g, 5) for g in box_kc["GZ_m"]],
                                       "gz_analytic": [pct(w, 5) for _, w in box_wall],
                                       "rel_errors": [pct(e, 5) for e in box_err]},
        "fatigue_spectral_vs_rainflow": {k: (pct(v, 6) if isinstance(v, float) else v)
                                         for k, v in fat_rf.items()},
        "manoeuvring_35deg": {k: pct(v, 3) for k, v in turn35.items()
                              if isinstance(v, float) and not k.startswith("traj")},
        "manoeuvring_20deg": {k: pct(v, 3) for k, v in turn20.items()
                              if isinstance(v, float) and not k.startswith("traj")},
        "kcs_check": {"RT_MN": pct(kcs["RT_N"] / 1e6, 3), "Fn": pct(kcs["Fn"]),
                      "note": "published EFD/CFD full-scale KCS at 24 kn ~ 1.4-1.6 MN; "
                              "Holtrop over-predicts a fine hull at Fn=0.26 (declared limitation)"},
    }

    # ---------------------------------------------------------------- figures
    figs = []

    # (1) Kalman convergence
    ser = conv["series"]
    t_min = np.asarray(ser["time_s"]) / 60.0
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].plot(t_min, ser["gm_true"], "k-", lw=2, label="truth")
    axes[0].plot(t_min, ser["gm_est"], "C0--", lw=1.5, label="EKF estimate")
    axes[0].fill_between(t_min, np.asarray(ser["gm_est"]) - 2 * np.asarray(ser["gm_std"]),
                         np.asarray(ser["gm_est"]) + 2 * np.asarray(ser["gm_std"]),
                         color="C0", alpha=0.2, label="±2σ")
    axes[0].axvline(50 * conv["dt_s"] / 60.0, color="grey", ls=":", lw=1)
    axes[0].set_xlabel("time [min]")
    axes[0].set_ylabel("GM [m]")
    axes[0].set_title(f"EKF GM tracking (RMSE {conv['rel_rmse_gm']*100:.2f} % of truth)")
    axes[0].legend(fontsize=8)
    axes[1].plot(t_min, ser["resistance_bias_true"], "k-", lw=2, label="truth")
    axes[1].plot(t_min, ser["resistance_bias_est"], "C1--", lw=1.5, label="estimate")
    axes[1].set_xlabel("time [min]")
    axes[1].set_ylabel("resistance bias [-]")
    axes[1].set_title(f"resistance bias (RMSE {conv['rel_rmse_resistance_bias']*100:.2f} %)")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "kalman_convergence.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    figs.append({"name": "kalman_convergence.png",
                 "caption": "EKF state estimation: GM and resistance-bias tracking over 100 steps "
                            f"(3 % sensor noise). RMSE {conv['rel_rmse_gm']*100:.2f} % / "
                            f"{conv['rel_rmse_resistance_bias']*100:.2f} % of truth."})

    # (2) forecast vs truth
    v1 = validations["moderate_ice_1h"]
    tr, fc = v1["truth"], v1["forecast"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    labels = ["mean power\n[kW]", "fuel\n[kg]", "GM margin\n[m]"]
    tv = [tr["mean_power_kW"], tr["fuel_kg"], tr["min_GM_margin_m"]]
    fv = [fc["mean_power_kW"], fc["fuel_kg"], fc["min_GM_margin_m"]]
    x = np.arange(len(labels))
    axes[0].bar(x - 0.18, tv, 0.36, label="reference simulation (dt=60 s)", color="k")
    axes[0].bar(x + 0.18, fv, 0.36, label="surrogate forecast (dt=300 s)", color="C2")
    axes[0].set_xticks(x, labels)
    axes[0].set_title(f"1 h horizon, MARE = {v1['mare']*100:.2f} %")
    axes[0].legend(fontsize=8)
    tl = fc_h["6h"]["timeline"]
    tt = np.asarray(tl["time_s"]) / 3600.0
    axes[1].plot(tt, tl["power_kW"], "C0-", label="delivered power [kW]")
    axes[1].set_xlabel("horizon [h]")
    axes[1].set_ylabel("P_D [kW]")
    ax2 = axes[1].twinx()
    ax2.plot(tt, tl["GM_margin_m"], "C3--", label="GM margin [m]")
    ax2.set_ylabel("GM margin [m]")
    axes[1].set_title("6 h envelope (moderate ice 0.30 / Hs 3-3.5 m)")
    h1, l1 = axes[1].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    axes[1].legend(h1 + h2, l1 + l2, fontsize=8, loc="lower right")
    fig.tight_layout()
    p = FIG / "forecast_vs_truth.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    figs.append({"name": "forecast_vs_truth.png",
                 "caption": "1 h forecast vs fine reference simulation (power, fuel, GM margin) and "
                            "the 6 h power / stability-margin envelope."})

    # (3) latency distribution
    fig, ax = plt.subplots(figsize=(7, 4))
    data = [sample_trace[n] for n in reg.names()]
    ax.boxplot(data, labels=reg.names(), showfliers=False)
    ax.axhline(100.0, color="r", ls="--", lw=1.2, label="100 ms budget")
    ax.set_yscale("log")
    ax.set_ylabel("predict wall-clock [ms]")
    ax.set_title(f"Surrogate latency, worst P95 = {max(p95_all):.3f} ms")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "surrogate_latency.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    figs.append({"name": "surrogate_latency.png",
                 "caption": "Per-surrogate predict latency (200 calls each, log scale) against the "
                            "100 ms real-time budget."})

    # (4) GZ curve + wall-sided check
    fig, ax = plt.subplots(figsize=(7, 4))
    heels = np.linspace(0, 30, 31)
    kc_fine = kn_curve(hull, list(heels), KG=VESSEL["KG"])
    ax.plot(kc_fine["heels_deg"], kc_fine["GZ_m"], "C0-o", ms=3, label="numerical hydrostatics")
    ax.plot([h for h, _ in box_wall], [w for _, w in box_wall], "ks", label="barge analytic (Cb=1)")
    ax.plot([h for h, _ in box_wall], box_kc["GZ_m"], "r^", mfc="none",
            label="barge numerical")
    ax.axhline(0.0, color="grey", lw=0.8)
    ax.set_xlabel("heel [deg]")
    ax.set_ylabel("GZ [m]")
    ax.set_title(f"GZ curve, ship (GM = {kc['GM0_m']:.2f} m, KG = {VESSEL['KG']} m) "
                 f"vs analytic barge (max err {max(box_err)*100:.2f} %)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "gz_curve.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    figs.append({"name": "gz_curve.png",
                 "caption": "Numerical GZ curve of the 180 m 1A bulker and the wall-sided analytic "
                            "benchmark for a rectangular barge (max error "
                            f"{max(box_err)*100:.2f} % up to 8 deg)."})

    # (5) surrogate parity
    fig, axes = plt.subplots(1, 5, figsize=(16, 3.4))
    for ax, name in zip(axes, reg.names()):
        s = reg.get(name)
        key = s.outputs.names[0]
        fld = s.inputs.fields[0]
        xs = np.linspace(fld.lower, fld.upper, 12)
        base = dict(SURROGATE_INPUTS[name])
        ref, got = [], []
        for xv in xs:
            payload = dict(base); payload[fld.name] = float(xv)
            ref.append(s._predict_reference(payload)[key])
            got.append(reg.predict(name, payload)[key])
        ax.plot(ref, got, "o", ms=3)
        lim = [min(ref + got), max(ref + got)]
        ax.plot(lim, lim, "k--", lw=1)
        ax.set_title(f"{name}\n{key}", fontsize=8)
        ax.set_xlabel("reference", fontsize=8)
        ax.set_ylabel("surrogate", fontsize=8)
        ax.tick_params(labelsize=7)
    fig.suptitle("Surrogate parity vs reference physics (1-D sweeps of the primary input)")
    fig.tight_layout()
    p = FIG / "surrogate_parity.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    figs.append({"name": "surrogate_parity.png",
                 "caption": "Parity plots: surrogate prediction vs its reference physics model along "
                            "the primary input axis for all five models."})

    # ---------------------------------------------------------------- acceptance
    acceptance = {
        "node": "W3-N10",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "numpy": np.__version__},
        "vessel": {"hull": hull.to_dict(), "KG_m": VESSEL["KG"],
                   "registry_build_s": pct(build_s, 2)},
        "criterion_a_surrogates": {
            "passed": len(reg) == 5,
            "n_registered": len(reg),
            "registry_version": reg.registry_version,
            "contract_fingerprint": reg.contract_fingerprint(),
            "contracts": contracts,
        },
        "criterion_b_latency": {
            "passed": bool(max(p95_all) < 100.0),
            "budget_ms": 100.0,
            "worst_p95_ms": pct(max(p95_all), 4),
            "per_surrogate": latency,
        },
        "criterion_c_kalman": {
            "passed": bool(conv["rel_rmse_gm"] < 0.05 and conv["converged_within_50_steps"]),
            "rmse_gm_pct": pct(conv["rel_rmse_gm"] * 100, 3),
            "rmse_resistance_bias_pct": pct(conv["rel_rmse_resistance_bias"] * 100, 3),
            "rmse_damage_scale_pct": pct(conv["rel_rmse_damage_scale"] * 100, 3),
            "steps_to_5pct": conv["steps_to_5pct_gm"],
            "rejected_updates": conv["rejected_updates"],
            "noise_5pct_case": {"rmse_gm_pct": pct(conv_5["rel_rmse_gm"] * 100, 3),
                                "steps_to_5pct": conv_5["steps_to_5pct_gm"],
                                "converged": conv_5["converged_within_50_steps"]},
            "figure": "figures/kalman_convergence.png",
        },
        "criterion_d_forecast": {
            "passed": bool(all(v["mare"] < 0.10 for v in validations.values())),
            "mare_definition": "mean |forecast-truth|/|truth| over mean power, fuel, "
                               "mean speed and GM margin",
            "cases": {k: {"mare_pct": pct(v["mare"] * 100, 3),
                          "relative_errors": {kk: pct(vv, 5) for kk, vv in v["relative_errors"].items()},
                          "secondary": {kk: pct(vv, 4) for kk, vv in v["secondary_relative_errors"].items()},
                          "truth": {kk: (pct(vv, 4) if isinstance(vv, float) else vv)
                                    for kk, vv in v["truth"].items()},
                          "forecast": {kk: (pct(vv, 4) if isinstance(vv, float) else vv)
                                       for kk, vv in v["forecast"].items()},
                          "criterion_met": v["criterion_met"]}
                      for k, v in validations.items()},
            "horizon_envelopes": {k: {kk: (pct(vv, 4) if isinstance(vv, float) else vv)
                                      for kk, vv in v["summary"].items()}
                                  for k, v in fc_h.items()},
            "figure": "figures/forecast_vs_truth.png",
        },
        "physics_validation": physics,
        "figures": figs,
        "artifacts": {
            "acceptance": "artifacts/digital_twin/acceptance.json",
            "physics": "artifacts/digital_twin/physics_validation.json",
            "figures_dir": "artifacts/digital_twin/figures",
        },
    }
    with open(OUT / "acceptance.json", "w") as fh:
        json.dump(acceptance, fh, indent=2, default=float)
    with open(OUT / "physics_validation.json", "w") as fh:
        json.dump(physics, fh, indent=2, default=float)
    return acceptance


if __name__ == "__main__":
    acc = main()
    print(json.dumps({k: acc[k] for k in ("criterion_a_surrogates", "criterion_b_latency",
                                          "criterion_c_kalman")}, indent=2)[:2000])
    print("criterion_d passed:", acc["criterion_d_forecast"]["passed"],
          {k: v["mare_pct"] for k, v in acc["criterion_d_forecast"]["cases"].items()})
    print("written:", acc["artifacts"])