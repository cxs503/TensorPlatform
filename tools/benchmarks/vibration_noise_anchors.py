#!/usr/bin/env python3
"""Anchor benchmark for the vibration & noise domain (mission 1707).

Emits machine-readable evidence to ``artifacts/m1707/vibration_noise_anchors.json``
and four figures to ``artifacts/m1707/``.  Every number in the JSON is computed
here (nothing is transcribed by hand into the report).

Run::

    cd /srv/tensor/agent/work/m1707 && python3 tools/benchmarks/vibration_noise_anchors.py
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "backend"))

from services.ship_performance.vibration_noise import acoustics as AC  # noqa: E402
from services.ship_performance.vibration_noise import criteria as CR  # noqa: E402
from services.ship_performance.vibration_noise import excitation as EX  # noqa: E402
from services.ship_performance.vibration_noise import structural as ST  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(REPO, "artifacts", "m1707")
os.makedirs(OUT, exist_ok=True)


def observed_order(e_coarse: float, e_fine: float, refinement: float) -> float:
    return math.log(e_coarse / e_fine) / math.log(refinement)


def main() -> dict:
    evidence: dict = {
        "mission": 1707,
        "domain": "vibration_noise",
        "domain_evidence": {"excitation": "REAL", "structural": "REAL", "acoustics": "REAL", "criteria": "WITHHELD"},
    }

    # ------------------------------------------------------------------ SDOF
    sdof_rows = []
    for zeta in (0.02, 0.05, 0.10):
        r_t = ST.sdof_transmissibility_peak_ratio(zeta)
        sdof_rows.append({
            "zeta": zeta,
            "h_peak": ST.sdof_magnification_peak(zeta),
            "h_peak_ratio": ST.sdof_magnification_peak_ratio(zeta),
            "t_peak": ST.sdof_transmissibility_peak(zeta),
            "t_peak_ratio": r_t,
            "t_peak_vs_h_peak_pct": (ST.sdof_transmissibility_peak(zeta) / ST.sdof_magnification_peak(zeta) - 1.0) * 100.0,
            "half_power_bandwidth": ST.half_power_bandwidth_ratio(zeta),
            "two_zeta": 2.0 * zeta,
            "bandwidth_error_pct": (ST.half_power_bandwidth_ratio(zeta) / (2.0 * zeta) - 1.0) * 100.0,
        })
    evidence["sdof"] = sdof_rows
    evidence["roll_off_db_per_octave"] = {
        "lossless_single": ST.transmissibility_slope_db_per_octave(10.0, 20.0, 1, 1e-4, False),
        "lossless_two_stage": ST.transmissibility_slope_db_per_octave(10.0, 20.0, 1, 1e-4, True),
        "damped_single_zeta0.1": ST.transmissibility_slope_db_per_octave(10.0, 100.0, 3, 0.1, False),
        "damped_two_stage_zeta0.1": ST.transmissibility_slope_db_per_octave(10.0, 100.0, 3, 0.1, True),
    }

    # ------------------------------------------------------- cantilever modes
    exact = ST.cantilever_frequencies(1.0, 1.0, 1.0, 3)
    rr_table = []
    for n_terms in (2, 3, 4, 6, 8, 10, 12):
        rr = ST.cantilever_rayleigh_ritz(1.0, 1.0, 1.0, n_terms)
        k = min(n_terms, 3)
        rr_table.append({
            "n_terms": n_terms,
            "f": [float(v) for v in rr[:k]],
            "rel_err_pct": [float((rr[i] - exact[i]) / exact[i] * 100.0) for i in range(k)],
        })
    evidence["cantilever_exact_hz"] = [float(v) for v in exact]
    evidence["cantilever_rayleigh_ritz"] = rr_table

    # --------------------------------------------------- FD beam convergence
    beam_rows = []
    beam_exact = ST.ssss_beam_exact_frequencies(1.0, 1.0, 1.0, 3)
    for n_cells in (16, 32, 64, 128, 256):
        fd = ST.ssss_beam_fd_frequencies(1.0, 1.0, 1.0, n_cells, 3)
        beam_rows.append({
            "n_cells": n_cells,
            "f": [float(v) for v in fd],
            "rel_err": [float(abs(fd[i] - beam_exact[i]) / beam_exact[i]) for i in range(3)],
        })
    orders = [observed_order(beam_rows[i]["rel_err"][0], beam_rows[i + 1]["rel_err"][0], 2.0)
              for i in range(len(beam_rows) - 2)]
    evidence["beam_fd"] = {"exact_hz": [float(v) for v in beam_exact], "grids": beam_rows,
                           "observed_orders_mode1": orders}

    # -------------------------------------------------- FD plate convergence
    plate_exact = ST.ssss_plate_frequencies(1.0, 1.0, 1.0, 1.0, [(1, 1), (1, 2), (2, 1)])
    plate_rows = []
    for n_cells in (10, 20, 40, 80, 160):
        res = ST.ssss_plate_fd_modes(1.0, 1.0, 1.0, 1.0, 1, 1, n_cells)
        plate_rows.append({
            "n_cells": n_cells,
            "f11": res["frequency_hz"],
            "rel_err": abs(res["frequency_hz"] - plate_exact[0]) / plate_exact[0],
        })
    plate_orders = [observed_order(plate_rows[i]["rel_err"], plate_rows[i + 1]["rel_err"], 2.0)
                    for i in range(len(plate_rows) - 2)]
    evidence["plate_fd"] = {
        "exact_hz_11_12_21": [float(v) for v in plate_exact],
        "degeneracy_rel": abs(plate_exact[1] - plate_exact[2]) / plate_exact[1],
        "grids": plate_rows,
        "observed_orders": plate_orders,
        "inadmissible_cubic_quotient_ratio": ST.plate_rayleigh_quotient_inadmissible_trial(1.0, 1.0, 1.0, 1.0) / plate_exact[0],
        "admissible_sine_quotient_rel_err": abs(ST.plate_rayleigh_quotient_sine(1.3, 0.9, 5.0, 40.0, 1, 2)
                                                - ST.ssss_plate_frequencies(1.3, 0.9, 5.0, 40.0, [(1, 2)])[0])
                                               / ST.ssss_plate_frequencies(1.3, 0.9, 5.0, 40.0, [(1, 2)])[0],
    }

    # ------------------------------------------------------------- acoustics
    f0, q0 = 1000.0, 1.0e-4
    k0 = AC.wavenumber(f0)
    dip = []
    for kd in (0.01, 0.02, 0.05, 0.1, 0.2):
        d = kd / k0
        p_num = AC.two_monopole_power_numeric(f0, q0, d, r_obs=2000.0, n_theta=2000)
        p_ana = AC.dipole_radiated_power(f0, q0, d)
        dip.append({"kd": kd, "P_numeric": p_num, "P_analytic": p_ana, "ratio": p_num / p_ana})
    freqs = (200.0, 400.0, 800.0, 1600.0)
    d_fix = 0.05
    p_f = [AC.two_monopole_power_numeric(f, q0, d_fix, r_obs=8000.0, n_theta=2000) for f in freqs]
    k_law_slope = (math.log(p_f[-1]) - math.log(p_f[0])) / (math.log(freqs[-1]) - math.log(freqs[0]))

    mono_rows = []
    for f in (200.0, 1000.0, 5000.0):
        p_tot = AC.monopole_radiated_power(f, q0)
        r_r = AC.monopole_radiation_resistance(f)
        p_at_1m = AC.monopole_pressure_amplitude(f, q0, 1.0)
        mono_rows.append({
            "f": f,
            "P": p_tot,
            "half_RrQ2": 0.5 * r_r * q0 * q0,
            "identity_rel_err": abs(p_tot - 0.5 * r_r * q0 ** 2) / p_tot,
            "p_rms_1m": p_at_1m / math.sqrt(2.0),
            "SL_1m_dB_re_1uPa": AC.source_level_at_1m_db(p_at_1m / math.sqrt(2.0), 1.0),
        })
    a = 0.5
    ka_rows = []
    for f in (100.0, 500.0, 2000.0, 10000.0):
        ka = AC.wavenumber(f) * a
        u = 0.1
        q_s = 4.0 * math.pi * a * a * u
        p_rad = AC.monopole_radiated_power(f, q_s)
        sigma = p_rad / (0.5 * AC.RHO_SEAWATER * AC.C_SEAWATER * u * u * 4.0 * math.pi * a * a)
        ka_rows.append({"f": f, "ka": ka, "sigma_numeric": sigma, "sigma_ka2": ka * ka,
                        "rel_err": abs(sigma - ka * ka) / (ka * ka)})
    evidence["acoustics"] = {
        "monopole": mono_rows,
        "dipole_vs_numeric": dip,
        "dipole_frequency_law_slope": k_law_slope,
        "pulsating_sphere": ka_rows,
        "levels": {
            "SL_of_1Pa": AC.sound_pressure_level_db(1.0),
            "incoherent_two_equal": AC.incoherent_sum_db([100.0, 100.0]),
            "coherent_two_equal": AC.coherent_sum_db_equal(2),
            "mass_law_slope_5000kgm2": AC.limp_mass_transmission_loss_db(4000.0, 5000.0)
                                       - AC.limp_mass_transmission_loss_db(2000.0, 5000.0),
        },
    }

    # ------------------------------------------------------------ excitation
    evidence["excitation"] = {
        "shaft_rpm": 120.0,
        "n_blades": 5,
        "f_blade_rate_hz": EX.blade_rate_hz(120.0, 5),
        "harmonics": EX.blade_rate_harmonics(120.0, 5, 4),
        "tip_speed_ms": EX.propeller_tip_speed(120.0, 2.0),
        "cavitation_number": EX.cavitation_number(101325.0 + 1025.0 * 9.81 * 5.0, 2300.0, 1025.0, 12.0),
    }

    # -------------------------------------------------------------- criteria
    evidence["criteria"] = {
        "withheld": CR.withheld_limit_profiles(),
        "band_energy_sum_3_4": CR.band_energy_sum([3.0, 4.0]),
    }

    with open(os.path.join(OUT, "vibration_noise_anchors.json"), "w") as fh:
        json.dump(evidence, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------- figures
    _fig_sdof()
    _fig_beam(beam_rows, beam_exact)
    _fig_plate(plate_exact)
    _fig_acoustics(dip, freqs, p_f, ka_rows)
    return evidence


def _fig_sdof() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    r = np.linspace(0.01, 12.0, 2000)
    for zeta, style in ((0.02, "-"), (0.05, "--"), (0.10, ":")):
        t = np.array([ST.sdof_transmissibility(float(x), zeta) for x in r])
        h = np.array([ST.sdof_compliance(float(x), zeta) for x in r])
        axes[0].loglog(r, t, style, lw=1.6, label=f"T (transmissibility), zeta={zeta}")
        axes[0].loglog(r, h, style, lw=1.0, alpha=0.55, label=f"|H| (magnification), zeta={zeta}")
        axes[0].plot([ST.sdof_transmissibility_peak_ratio(zeta)], [ST.sdof_transmissibility_peak(zeta)],
                     "o", ms=5, mfc="none", mec="C0")
        axes[0].plot([ST.sdof_magnification_peak_ratio(zeta)], [ST.sdof_magnification_peak(zeta)],
                     "s", ms=4, mfc="none", mec="C1")
    axes[0].set_xlabel("frequency ratio r = f / f_n")
    axes[0].set_ylabel("amplitude ratio")
    axes[0].set_title("SDOF: transmissibility vs magnification (peaks marked)")
    axes[0].grid(True, which="both", alpha=0.25)
    axes[0].legend(fontsize=6, loc="lower left")

    # isolation curve with the damped/lossless roll-off annotation
    fn = 10.0
    f = np.logspace(0.5, 3.0, 400)
    for zeta in (1e-4, 0.1):
        il = [ST.isolation_insertion_loss_db(float(x) / fn, zeta) for x in f]
        axes[1].semilogx(f, il, lw=1.6, label=f"single stage, zeta={zeta}")
    for zeta in (1e-4, 0.1):
        il = [-20.0 * math.log10(ST.two_stage_transmissibility(float(x) / fn, zeta)) for x in f]
        axes[1].semilogx(f, il, lw=1.2, ls="--", label=f"two stages, zeta={zeta}")
    axes[1].axhline(0.0, color="k", lw=0.6, alpha=0.5)
    axes[1].set_xlabel("frequency [Hz]  (f_n = 10 Hz)")
    axes[1].set_ylabel("insertion loss [dB]")
    axes[1].set_title("Isolation: 12/24 dB per octave (lossless) degrades to 6/12 (damped)")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend(fontsize=6, loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig1_sdof_isolation.png"), dpi=140)
    plt.close(fig)


def _fig_beam(beam_rows, beam_exact) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    h = np.array([1.0 / row["n_cells"] for row in beam_rows])
    for i, name in enumerate(("mode 1", "mode 2", "mode 3")):
        err = np.array([row["rel_err"][i] for row in beam_rows])
        axes[0].loglog(h, err, "o-", label=f"FD {name}")
    axes[0].loglog(h, 2.0e-2 * (h / h[0]) ** 2, "k:", lw=1.0, label="slope 2 reference")
    axes[0].set_xlabel("h / L")
    axes[0].set_ylabel("relative error")
    axes[0].set_title("Beam FD eigen-solver: 2nd-order convergence to Euler-Bernoulli")
    axes[0].grid(True, which="both", alpha=0.25)
    axes[0].legend(fontsize=7)

    terms = [2, 3, 4, 6, 8, 10, 12]
    e1 = [abs(ST.cantilever_rayleigh_ritz(1.0, 1.0, 1.0, n)[0] - beam_exact_cantilever[0]) / beam_exact_cantilever[0]
          for n in terms]
    axes[1].semilogy(terms, np.maximum(e1, 1e-17), "s-", label="Rayleigh-Ritz, mode 1")
    axes[1].set_xlabel("number of trial functions")
    axes[1].set_ylabel("relative error")
    axes[1].set_title("Cantilever RR: monotone downward convergence (upper bound)")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig2_beam_convergence.png"), dpi=140)
    plt.close(fig)


def _fig_plate(plate_exact) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    res = ST.ssss_plate_fd_modes(1.0, 1.0, 1.0, 1.0, 1, 2, 120)
    n = 2
    y = np.linspace(0.0, 1.0, 200)
    X, Y = np.meshgrid(res["x"], y, indexing="ij")
    W = np.outer(res["psi"], np.sin(n * math.pi * y))
    cf = axes[0].contourf(X, Y, W, levels=24, cmap="RdBu_r")
    axes[0].contour(X, Y, W, levels=[0.0], colors="k", linewidths=0.8)
    fig.colorbar(cf, ax=axes[0], shrink=0.9)
    axes[0].set_title(f"FD mode (1,2) shape, f = {res['frequency_hz']:.4f} Hz (exact {plate_exact[1]:.4f})")
    axes[0].set_xlabel("x / a")
    axes[0].set_ylabel("y / b")

    grids = np.array([10, 20, 40, 80, 160], dtype=float)
    errs = [abs(ST.ssss_plate_fd_modes(1.0, 1.0, 1.0, 1.0, 1, 1, int(nc))["frequency_hz"] - plate_exact[0]) / plate_exact[0]
            for nc in grids]
    axes[1].loglog(1.0 / grids, errs, "o-", label="plate FD (1,1)")
    axes[1].loglog(1.0 / grids, errs[0] * (grids[0] / grids) ** 2, "k:", label="slope 2 reference")
    axes[1].set_xlabel("h / a")
    axes[1].set_ylabel("relative error vs Kirchhoff closed form")
    axes[1].set_title("SSSS plate: FD convergence to closed form")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig3_plate_mode_convergence.png"), dpi=140)
    plt.close(fig)


def _fig_acoustics(dip, freqs, p_f, ka_rows) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    kd = np.array([row["kd"] for row in dip])
    ratio = np.array([row["ratio"] for row in dip])
    axes[0].semilogx(kd, ratio, "o-")
    axes[0].axhline(1.0, color="k", lw=0.6)
    axes[0].set_xlabel("kd = 2 pi d / lambda")
    axes[0].set_ylabel("P_numeric / P_analytic")
    axes[0].set_title("Dipole power: far-field integration vs closed form")
    axes[0].grid(True, which="both", alpha=0.25)

    axes[1].loglog(freqs, p_f, "o-", label="numeric integration")
    axes[1].loglog(freqs, p_f[0] * (np.array(freqs) / freqs[0]) ** 4, "k:", label="slope 4 reference (k^4 law)")
    axes[1].set_xlabel("frequency [Hz] (fixed separation d = 0.05 m)")
    axes[1].set_ylabel("radiated power [W]")
    axes[1].set_title("Compact dipole: k^4 (f^4) radiation law")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig4_acoustics_dipole.png"), dpi=140)
    plt.close(fig)


beam_exact_cantilever = ST.cantilever_frequencies(1.0, 1.0, 1.0, 3)


if __name__ == "__main__":
    ev = main()
    print(json.dumps({k: ev[k] for k in ("roll_off_db_per_octave", "domain_evidence")}, indent=2))
    print("artifacts written to", OUT)