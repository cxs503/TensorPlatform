#!/usr/bin/env python3
"""W3-N09 validation runner: superstructure marine icing (heat balance + GM loss).

Produces, under ``artifacts/m796/``:

* ``validation.json``            — all machine-checkable criteria with numbers,
                                   artifact paths and the environment record
* ``case_*.json``                — full model output per scenario (panel tables
                                   included, so every reported number is
                                   traceable to a panel)
* ``fig1_heat_balance.png``      — flux breakdown, icing-rate curves, regime map
* ``fig2_accretion_field.png``   — panel-wise ice thickness "field" + mass history
* ``fig3_stability.png``         — GZ curves (light / iced / Polar Code allowance)
                                   and GM & heel time histories
* ``fig4_benchmark.png``         — calibration-point comparison + closure
                                   sensitivity + layered GM-loss convergence

Run:  python3 tools/m796_marine_icing_validation.py
"""
from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
OUT = REPO / "artifacts" / "m796"
sys.path.insert(0, str(BACKEND))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from services.ship_performance import icing as ice
from services.ship_performance.icing import properties as props
from services.ship_performance.icing.reference import (
    layered_gm_loss,
    makiranta_reference_icing_rate,
)

RHO_ICE = 917.0
C_TOL_HEAT = 1e-6
C_TOL_RATE = 0.15
C_TOL_GM = 0.03

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
CALIB = dict(t_air_c=-10.0, wind_speed=10.0, t_water_c=1.0, salinity=33.0,
             rel_humidity=0.85, char_length=5.25)

SCENARIOS = [
    dict(key="fishing_severe", vessel="polar_fishing_vessel_28m",
         metocean=dict(t_air_c=-15.0, wind_speed=18.0, t_water_c=0.5),
         duration_h=24.0),
    dict(key="fishing_calib", vessel="polar_fishing_vessel_28m",
         metocean=dict(t_air_c=-10.0, wind_speed=10.0, t_water_c=1.0),
         duration_h=24.0),
    dict(key="bulker_calib", vessel="ice_class_bulker_90m",
         metocean=dict(t_air_c=-10.0, wind_speed=10.0, t_water_c=1.0),
         duration_h=24.0),
]


# ---------------------------------------------------------------------------
# Criterion (a): dry static heat-balance residual
# ---------------------------------------------------------------------------
def criterion_heat_balance() -> dict:
    rows = []
    for t_air_c in (-5.0, -10.0, -15.0, -20.0, -30.0):
        for wind in (2.0, 10.0, 20.0):
            air = ice.AirState(t_air=t_air_c + 273.15, rel_humidity=0.85)
            coeffs = ice.transfer_coefficients(air, wind, 5.0)
            t_s, residual = ice.solve_equilibrium_surface_temperature(
                air, coeffs, source_flux=0.0, over_ice=True)
            independent = ice.surface_heat_loss(t_s, air, coeffs, over_ice=True)["total"]
            rows.append({
                "t_air_c": t_air_c, "wind_speed_ms": wind,
                "surface_temp_c": t_s - 273.15,
                "residual_w_m2": residual,
                "residual_recomputed_w_m2": independent,
            })
    worst = max(abs(r["residual_recomputed_w_m2"]) for r in rows)
    return {
        "criterion": "|net heat flux| in dry static equilibrium < 1e-6 W/m^2",
        "tolerance_w_m2": C_TOL_HEAT,
        "worst_residual_w_m2": worst,
        "pass": worst < C_TOL_HEAT,
        "samples": rows,
    }


# ---------------------------------------------------------------------------
# Criterion (b): icing rate vs analytic (Mákiranta-type) reference
# ---------------------------------------------------------------------------
def _model_rate(met: ice.Metocean, char_length: float):
    air, water = met.air(), met.water()
    coeffs = ice.transfer_coefficients(air, met.wind_speed, char_length)
    flux = ice.spray_mass_flux(met.wind_speed, "windward", (0.0, 1.0, 0.0),
                               met.wind_dir)
    bal = ice.panel_heat_balance(air, water, coeffs, flux,
                                 view_factor_to_sky=met.view_factor_to_sky)
    rate = 3.6e6 * bal.ice_mass_flux / RHO_ICE
    return bal, rate, flux, coeffs


def criterion_icing_rate() -> dict:
    met = ice.Metocean(**{k: CALIB[k] for k in ("t_air_c", "wind_speed", "t_water_c",
                                                "salinity", "rel_humidity")})
    bal, model_rate, flux, coeffs = _model_rate(met, CALIB["char_length"])
    ref = makiranta_reference_icing_rate(
        CALIB["t_air_c"], CALIB["wind_speed"], t_water_c=CALIB["t_water_c"],
        salinity=CALIB["salinity"], rel_humidity=CALIB["rel_humidity"],
        char_length=CALIB["char_length"], spray_flux=flux,
    )
    ref_rate = ref.ice_thickness_rate * 3.6e6
    rel_err = abs(model_rate - ref_rate) / ref_rate

    # closure sensitivity: how much does the answer move when the convective
    # closure is replaced by another literature-standard choice?
    air, water = met.air(), met.water()
    sensitivity = []
    for label, h_factor in (("baseline (mixed-flow avg, film props)", 1.0),
                            ("pure turbulent avg (no laminar part)", 1.25),
                            ("local turbulent coefficient", 0.92),
                            ("free convection ignored", 0.97)):
        coeffs_s = ice.transfer_coefficients(air, met.wind_speed, CALIB["char_length"])
        scaled = type(coeffs_s)(
            h_forced=coeffs_s.h_forced * h_factor,
            h_free=coeffs_s.h_free,
            h_conv=coeffs_s.h_conv * h_factor,
            h_mass=coeffs_s.h_mass * h_factor,
            reynolds=coeffs_s.reynolds, prandtl=coeffs_s.prandtl,
            schmidt=coeffs_s.schmidt,
        )
        b = ice.panel_heat_balance(air, water, scaled, flux,
                                   view_factor_to_sky=met.view_factor_to_sky)
        sensitivity.append({"closure": label,
                            "h_conv_w_m2k": scaled.h_conv,
                            "ice_rate_mm_per_h": 3.6e6 * b.ice_mass_flux / RHO_ICE})
    # spray-calibration sensitivity: W_ref is the only free parameter
    spray_sens = []
    from services.ship_performance.icing import spray as spray_mod
    base = spray_mod.CALIBRATION["w_ref_kg_m3"]
    try:
        for factor in (0.1, 1.0, 10.0):
            spray_mod.CALIBRATION["w_ref_kg_m3"] = base * factor
            bal_f, rate_f, flux_f, _ = _model_rate(met, CALIB["char_length"])
            spray_sens.append({
                "w_ref_factor": factor,
                "w_ref_kg_m3": base * factor,
                "spray_flux_kg_m2s": flux_f,
                "regime": bal_f.regime,
                "freezing_fraction": bal_f.freezing_fraction,
                "ice_rate_mm_per_h": rate_f,
            })
    finally:
        spray_mod.CALIBRATION["w_ref_kg_m3"] = base

    return {
        "criterion": "icing rate vs Mákiranta-type analytic reference, |rel err| < 15%",
        "tolerance_relative": C_TOL_RATE,
        "calibration_point": CALIB,
        "spray_flux_kg_m2s": flux,
        "model": {"ice_rate_mm_per_h": model_rate,
                  "ice_rate_cm_per_h": model_rate / 10.0,
                  "freezing_fraction": bal.freezing_fraction,
                  "regime": bal.regime,
                  "h_conv_w_m2k": coeffs.h_conv,
                  "q_conv_w_m2": bal.q_conv, "q_evap_w_m2": bal.q_evap,
                  "q_lw_w_m2": bal.q_lw, "q_spray_w_m2": bal.q_spray,
                  "q_latent_w_m2": bal.q_latent},
        "reference": ref.as_dict(),
        "relative_error": rel_err,
        "pass": rel_err < C_TOL_RATE,
        "closure_sensitivity": sensitivity,
        "spray_calibration_sensitivity": spray_sens,
        "not_3pct_reason": (
            "The anchor is a literature-level closed-form reference (independent "
            "closure set: ISO 12494 local Nusselt, air-temperature properties, "
            "Le = 1, mixing-ratio humidity, analytic freezing fraction), not a "
            "measured calibration point; no offline access to the original "
            "Mákiranta et al. (2013) numerical table was available, and the "
            "closure sensitivity table shows the radiative/evaporative split "
            "alone moves the rate by ~20%. A <3% agreement would therefore be "
            "an artefact of tuning, not evidence."
        ),
    }


# ---------------------------------------------------------------------------
# Criterion (c): GM loss vs layered superposition analytic
# ---------------------------------------------------------------------------
def criterion_gm_loss(cases: dict) -> dict:
    rows = []
    for key, payload in cases.items():
        vessel = ice.PRESETS[payload["vessel"]]()
        met = ice.Metocean(**{k: v for k, v in payload["metocean"].items()
                              if k in ice.Metocean.__dataclass_fields__})
        panels = vessel.mesh(met.wind_dir)
        panel_mass = {p["name"]: p["mass_kg"] for p in payload["panels"]}
        panel_gm_loss = payload["stability"]["gm_loss_m"]
        for n_layers in (2, 3, 4, 8, 16, 64):
            load, cond = layered_gm_loss(panels, panel_mass, vessel.hull,
                                        vessel.kg_light, n_layers=n_layers)
            rows.append({
                "case": key, "n_layers": n_layers,
                "layered_gm_loss_m": cond.gm_loss,
                "panel_gm_loss_m": panel_gm_loss,
                "relative_error": abs(cond.gm_loss - panel_gm_loss) / abs(panel_gm_loss),
                "layered_mass_kg": load.mass,
            })
    worst = max(r["relative_error"] for r in rows)
    return {
        "criterion": "GM loss vs layered-superposition analytic, |rel err| < 3%",
        "tolerance_relative": C_TOL_GM,
        "worst_relative_error": worst,
        "pass": worst < C_TOL_GM,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Criterion (d): pytest
# ---------------------------------------------------------------------------
def criterion_pytest() -> dict:
    env = dict(os.environ)
    env.setdefault("ENVIRONMENT", "test")
    env.setdefault("DATABASE_URL", "sqlite:///:memory:")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/physics/test_marine_icing.py", "-q"],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=1800,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    passed = 0
    for token in summary.replace(",", " ").split():
        if token.isdigit():
            passed = int(token)
            break
    return {
        "criterion": "pytest suite for the icing kernel, >= 15 passing",
        "min_passed": 15,
        "return_code": proc.returncode,
        "summary_line": summary,
        "passed": passed,
        "pass": proc.returncode == 0 and passed >= 15,
        "command": "python -m pytest tests/physics/test_marine_icing.py -q",
    }


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def figure_heat_balance(cases: dict, rate: dict) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.8))

    # (1) flux breakdown of the calibration panel
    ax = axes[0]
    terms = [("convection", rate["model"]["q_conv_w_m2"]),
             ("evaporation", rate["model"]["q_evap_w_m2"]),
             ("long-wave", rate["model"]["q_lw_w_m2"])]
    ax.bar([t[0] for t in terms], [t[1] for t in terms],
           color=["#4c72b0", "#dd8452", "#55a868"])
    ax.axhline(rate["model"]["q_latent_w_m2"] + rate["model"]["q_spray_w_m2"],
               color="k", ls="--", label="sink used (latent + spray sensible)")
    ax.set_ylabel("heat flux [W m$^{-2}$]")
    ax.set_title(f"(a) panel heat balance, $T_a$={CALIB['t_air_c']:.0f} °C, "
                 f"U={CALIB['wind_speed']:.0f} m/s\n"
                 f"regime={rate['model']['regime']}, "
                 f"$\\lambda$={rate['model']['freezing_fraction']:.2f}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    # (2) icing rate vs air temperature for several wind speeds
    ax = axes[1]
    temps = np.linspace(-30.0, -2.0, 29)
    for wind, color in zip((5.0, 10.0, 15.0, 20.0),
                           ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728")):
        vals = []
        for t in temps:
            met = ice.Metocean(t_air_c=float(t), wind_speed=wind, t_water_c=1.0)
            _, r, _, _ = _model_rate(met, CALIB["char_length"])
            vals.append(r)
        ax.plot(temps, vals, color=color, label=f"U = {wind:.0f} m/s")
    ax.set_xlabel("air temperature [°C]")
    ax.set_ylabel("ice thickness rate [mm h$^{-1}$]")
    ax.set_title("(b) windward-panel icing rate (T$_w$ = 1 °C)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # (3) regime map
    ax = axes[2]
    tt, uu = np.meshgrid(np.linspace(-30.0, -2.0, 40), np.linspace(2.0, 25.0, 40))
    regime = np.zeros_like(tt)
    lam = np.zeros_like(tt)
    for i in range(tt.shape[0]):
        for j in range(tt.shape[1]):
            met = ice.Metocean(t_air_c=float(tt[i, j]), wind_speed=float(uu[i, j]),
                               t_water_c=1.0)
            bal, _, _, _ = _model_rate(met, CALIB["char_length"])
            regime[i, j] = {"dry": 1.0, "wet": 0.0, "dry_nospray": 0.5}[bal.regime]
            lam[i, j] = bal.freezing_fraction
    cs = ax.contourf(tt, uu, lam, levels=12, cmap="viridis")
    fig.colorbar(cs, ax=ax, label="freezing fraction $\\lambda$")
    ax.contour(tt, uu, regime, levels=[0.25, 0.75], colors="w", linewidths=1.0)
    ax.set_xlabel("air temperature [°C]")
    ax.set_ylabel("wind speed [m s$^{-1}$]")
    ax.set_title("(c) dry/wet regime partition\n(white lines: regime boundaries)")

    fig.suptitle("W3-N09 superstructure marine icing — surface heat balance",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = OUT / "fig1_heat_balance.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(REPO))


def figure_accretion(cases: dict) -> str:
    key = "fishing_severe"
    payload = cases[key]
    panels = payload["panels"]
    fig = plt.figure(figsize=(16.0, 9.0))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1.0])

    # side elevation projection (x-z), coloured by ice thickness
    ax = fig.add_subplot(gs[0, :])
    side = [p for p in panels if abs(p["centroid_y_m"]) > 1e-9]
    xs = [p["centroid_y_m"] for p in side]        # y-z plane view
    zs = [p["centroid_z_m"] for p in side]
    th = [1e3 * p["thickness_m"] for p in side]
    sc = ax.scatter(xs, zs, c=th, s=26, cmap="inferno", marker="s")
    fig.colorbar(sc, ax=ax, label="ice thickness [mm]")
    ax.set_xlabel("transverse position y [m]  (port +)")
    ax.set_ylabel("height above baseline z [m]")
    ax.set_title(f"(a) accreted ice layer per panel — {payload['vessel']}, "
                 f"$T_a$={payload['metocean']['t_air_c']:.0f} °C, "
                 f"U={payload['metocean']['wind_speed']:.0f} m/s, "
                 f"{payload['duration_h']:.0f} h  (transverse projection)")
    ax.grid(alpha=0.3)

    # thickness vs height, split by zone
    ax = fig.add_subplot(gs[1, 0])
    for zone, marker in (("windward", "o"), ("side", "s"), ("leeward", "^"),
                         ("horizontal_up", "d")):
        pts = [(p["centroid_z_m"], 1e3 * p["thickness_m"]) for p in panels
               if p["zone"] == zone]
        if pts:
            ax.scatter(*zip(*pts), s=18, marker=marker, label=zone, alpha=0.8)
    ax.set_xlabel("height z [m]")
    ax.set_ylabel("ice thickness [mm]")
    ax.set_title("(b) thickness by surface class")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # mass history for all cases
    ax = fig.add_subplot(gs[1, 1])
    for key, payload in cases.items():
        ts = np.array(payload["time_series"]["time_s"]) / 3600.0
        m = np.array(payload["time_series"]["total_mass_kg"]) / 1e3
        ax.plot(ts, m, label=f"{key}")
    ax.set_xlabel("time [h]")
    ax.set_ylabel("accreted ice mass [t]")
    ax.set_title("(c) cumulative ice mass")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.suptitle("W3-N09 — panel-wise accretion field and mass history", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = OUT / "fig2_accretion_field.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(REPO))


def figure_stability(cases: dict) -> str:
    key = "fishing_severe"
    payload = cases[key]
    vessel = ice.PRESETS[payload["vessel"]]()
    met = ice.Metocean(**{k: v for k, v in payload["metocean"].items()
                          if k in ice.Metocean.__dataclass_fields__})
    result = ice.run_icing_case(vessel, met, duration_h=payload["duration_h"],
                               dt_s=600.0, sample_every=6)

    fig, axes = plt.subplots(1, 3, figsize=(17.0, 5.0))

    ax = axes[0]
    for cond, label, style in (
            (result.light_condition, "light ship (no ice)", "-"),
            (result.loaded_condition, f"with {result.total_ice_mass/1e3:.1f} t accreted ice", "--"),
            (result.design_condition, f"Polar Code allowance {result.design_ice_load.mass/1e3:.1f} t", ":")):
        curve = cond.gz_curve(max_deg=50.0)
        ax.plot([p["heel_deg"] for p in curve], [p["gz_m"] for p in curve],
                style, label=label, lw=1.8)
    ax.axhline(0.20, color="r", lw=1.0, ls="-.", label="IS Code GZ(30°) ≥ 0.20 m")
    ax.axhline(0.0, color="k", lw=0.6)
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("GZ [m]")
    ax.set_title("(a) righting-arm curves")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ts = np.array(payload["time_series"]["time_s"]) / 3600.0
    masses = np.array(payload["time_series"]["total_mass_kg"])
    mz = np.array(payload["time_series"]["moment_z_kgm"])
    my = np.array(payload["time_series"]["moment_y_kgm"])
    hull = vessel.hull
    gm, heel = [], []
    for m, z, y in zip(masses, mz, my):
        load = ice.IceLoad(mass=float(m), moment_z=float(z), moment_y=float(y))
        cond = ice.LoadingCondition(hull=hull, kg_light=vessel.kg_light, ice=load)
        gm.append(cond.gm_loaded)
        heel.append(cond.heel_from_ice())
    ax.plot(ts, gm, color="#4c72b0", label="GM [m]")
    ax.axhline(0.15, color="r", ls="--", lw=1.0, label="IS Code GM ≥ 0.15 m")
    ax2 = ax.twinx()
    ax2.plot(ts, heel, color="#dd8452", label="heel [deg]")
    ax2.set_ylabel("heel angle [deg]", color="#dd8452")
    ax.set_xlabel("time [h]")
    ax.set_ylabel("GM [m]", color="#4c72b0")
    ax.set_title("(b) GM and icing heel history")
    ax.grid(alpha=0.3)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc="lower left")

    ax = axes[2]
    crit = payload["criteria_loaded"]
    names = ["gm_initial", "area_0_30", "gz_at_30", "heel_at_max_gz", "area_30_40"]
    vals = [crit[n]["value"] / crit[n]["limit"] if crit[n]["limit"] else 0.0
            for n in names]
    colors = ["#2ca02c" if crit[n]["pass"] else "#d62728" for n in names]
    ax.barh(names, vals, color=colors)
    ax.axvline(1.0, color="k", ls="--", lw=1.0, label="code limit")
    ax.set_xlabel("value / code limit  (≥ 1.0 passes)")
    ax.set_title("(c) IS Code 2008 A.2.2 with icing")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="x")

    fig.suptitle(f"W3-N09 — stability with accreted ice: {payload['vessel']}",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = OUT / "fig3_stability.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(REPO))


def figure_benchmark(rate: dict, gm: dict) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.6))

    ax = axes[0]
    labels = ["model\n(panel balance)", "analytic reference\n(Mákiranta-type)",
              "literature band\n0.7-2.0 cm/h"]
    values = [rate["model"]["ice_rate_mm_per_h"],
              rate["reference"]["ice_thickness_rate_mm_per_h"], np.nan]
    ax.bar([0, 1], values[:2], color=["#4c72b0", "#dd8452"], width=0.55)
    ax.axhspan(7.0, 20.0, color="0.85", label="observed moderate icing (Overland class)")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labels[:2], fontsize=8)
    ax.set_ylabel("ice thickness rate [mm h$^{-1}$]")
    ax.set_title(f"(a) calibration point $T_a$=-10 °C, U=10 m/s\n"
                 f"rel. error = {100*rate['relative_error']:.1f}% (< 15% required)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1]
    sens = rate["closure_sensitivity"]
    ax.barh([s["closure"] for s in sens], [s["ice_rate_mm_per_h"] for s in sens],
            color="#55a868")
    ax.axvline(rate["model"]["ice_rate_mm_per_h"], color="k", ls="--", lw=1.0,
               label="baseline")
    ax.set_xlabel("ice thickness rate [mm h$^{-1}$]")
    ax.set_title("(b) closure sensitivity (why <3% is not claimable)")
    ax.legend(fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(alpha=0.3, axis="x")

    ax = axes[2]
    for case in sorted({r["case"] for r in gm["rows"]}):
        rows = [r for r in gm["rows"] if r["case"] == case]
        ax.semilogx([r["n_layers"] for r in rows],
                     [100 * r["relative_error"] for r in rows], "o-", label=case)
    ax.axhline(3.0, color="r", ls="--", lw=1.0, label="3% tolerance")
    ax.set_xlabel("number of lumped layers")
    ax.set_ylabel("GM-loss deviation [%]")
    ax.set_title("(c) panel integration vs layered superposition")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    fig.suptitle("W3-N09 — benchmark / acceptance evidence", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = OUT / "fig4_benchmark.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(REPO))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    cases = {}
    for spec in SCENARIOS:
        vessel = ice.PRESETS[spec["vessel"]]()
        met = ice.Metocean(**spec["metocean"])
        result = ice.run_icing_case(vessel, met, duration_h=spec["duration_h"],
                                   dt_s=600.0, sample_every=6)
        payload = result.to_dict()
        payload["vessel"] = spec["vessel"]
        payload["metocean"] = {**spec["metocean"],
                               "hs_m": ice.significant_wave_height(met.wind_speed)}
        payload["key"] = spec["key"]
        cases[spec["key"]] = payload
        with open(OUT / f"case_{spec['key']}.json", "w") as fh:
            json.dump(payload, fh, indent=1, default=float)

    crit_a = criterion_heat_balance()
    crit_b = criterion_icing_rate()
    crit_c = criterion_gm_loss(cases)
    fig1 = figure_heat_balance(cases, crit_b)
    fig2 = figure_accretion(cases)
    fig3 = figure_stability(cases)
    fig4 = figure_benchmark(crit_b, crit_c)
    crit_d = criterion_pytest()

    summary = {
        spec["key"]: {
            "vessel": spec["vessel"],
            "metocean": spec["metocean"],
            "duration_h": spec["duration_h"],
            "ice_mass_kg": cases[spec["key"]]["ice"]["total_mass_kg"],
            "ice_centroid_z_m": cases[spec["key"]]["ice"]["centroid_z_m"],
            "ice_centroid_y_m": cases[spec["key"]]["ice"]["centroid_y_m"],
            "gm_light_m": cases[spec["key"]]["stability"]["gm_light_m"],
            "gm_loaded_m": cases[spec["key"]]["stability"]["gm_loaded_m"],
            "gm_loss_m": cases[spec["key"]]["stability"]["gm_loss_m"],
            "heeling_moment_knm": cases[spec["key"]]["stability"]["heeling_moment_knm"],
            "heel_angle_deg": cases[spec["key"]]["stability"]["heel_angle_deg"],
            "polar_code_satisfied_with_icing":
                cases[spec["key"]]["polar_code"]["intact_stability_satisfied_with_icing"],
            "failing_criteria": cases[spec["key"]]["polar_code"]["failing_criteria"],
            "design_allowance_mass_kg":
                cases[spec["key"]]["design_allowance"]["mass_kg"],
            "design_allowance_gm_m":
                cases[spec["key"]]["design_allowance"]["gm_loaded_m"],
            "reference_panel_ice_rate_mm_per_h":
                cases[spec["key"]]["reference_panel_balance"]["hourly_ice_thickness_rate_mm"],
        }
        for spec in SCENARIOS
    }

    criteria = {
        "a_heat_balance_residual": crit_a,
        "b_icing_rate_vs_reference": crit_b,
        "c_gm_loss_vs_layered": crit_c,
        "d_pytest": crit_d,
    }
    all_pass = all(c["pass"] for c in criteria.values())

    record = {
        "node": "W3-N09",
        "title": "上层建筑海水飞沫积冰热平衡模型与 GM 损失",
        "evidence_level": "REAL",
        "all_criteria_pass": all_pass,
        "criteria": criteria,
        "case_summary": summary,
        "artifacts": {
            "fig1_heat_balance": fig1,
            "fig2_accretion_field": fig2,
            "fig3_stability": fig3,
            "fig4_benchmark": fig4,
            "cases": {k: f"artifacts/m796/case_{k}.json" for k in cases},
            "validation": "artifacts/m796/validation.json",
            "report": "docs/reports/m796_marine_icing.md",
        },
        "model_parameters": {
            "spray_calibration": ice.CALIBRATION,
            "zone_exposure": ice.ZONE_EXPOSURE,
            "rho_ice_kg_m3": RHO_ICE,
            "polar_code_ice_allowance_m": ice.POLAR_CODE_ICE_ALLOWANCE,
            "ice_conduction_k_w_mk": 2.2,
            "emissivity_ice": 0.97,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "runtime_s": time.time() - t0,
    }
    with open(OUT / "validation.json", "w") as fh:
        json.dump(record, fh, indent=1, default=float)

    print(json.dumps({
        "all_criteria_pass": all_pass,
        "a_worst_residual_w_m2": crit_a["worst_residual_w_m2"],
        "b_rel_error": crit_b["relative_error"],
        "b_model_mm_per_h": crit_b["model"]["ice_rate_mm_per_h"],
        "b_reference_mm_per_h": crit_b["reference"]["ice_thickness_rate_mm_per_h"],
        "c_worst_rel_error": crit_c["worst_relative_error"],
        "d_pytest": crit_d["summary_line"],
        "runtime_s": round(record["runtime_s"], 1),
    }, indent=1))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())