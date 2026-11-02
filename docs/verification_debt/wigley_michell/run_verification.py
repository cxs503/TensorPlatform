"""Verification runner for the Michell thin-ship reference of the Wigley hull.

Produces (all machine-generated, no hand-copied numbers):

    artifacts/verification_verdicts.json   every check + verdict
    artifacts/verification_tables.md       markdown tables for the report
    figs/*.png                             report figures

Checks
------
G  geometry gates            Cb = 4/9, volume = 4LBT/9 exact; wetted surface
                             convergence; platform Wigley geometry endpoint
I  inner integrals           closed form vs mpmath adaptive quadrature
R  route consistency         A (closed form + Gauss) vs B (numerical surface
                             quadrature, dy/dx by finite differences) vs
                             C (30-digit mpmath adaptive)
D  discretisation            theta-quadrature refinement + Richardson (GCI-ish)
P  physics invariants        Rw ~ B^2 , Rw ~ rho , Cw(lambda) = lambda^2 Cw(1)
                             at fixed Fn, low-Fn decay of Rw
S  platform comparison       /resistance/predict wave resistance vs Michell

Usage:  python3 run_verification.py [--no-platform]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from michell_wigley import (  # noqa: E402
    KNOT,
    Wigley,
    amplitude_closed,
    inner_x_closed,
    inner_z_closed,
    michell_prefactor,
    rw_double_quad,
    rw_highprec,
    rw_theta_integral,
    wetted_surface,
)

ART = HERE / "artifacts"
FIG = HERE / "figs"
ART.mkdir(exist_ok=True)
FIG.mkdir(exist_ok=True)

RHO = 1025.0
G = 9.81
NU = 1.19e-6
PLATFORM = "http://127.0.0.1:8001/api/v1"
L, B, T = 100.0, 10.0, 6.25
HULL = Wigley(L=L, B=B, T=T, rho=RHO)
FN_SWEEP = [0.10, 0.15, 0.20, 0.25, 0.30, 0.32, 0.35]
FN_REF = 0.30


def relerr(x: float, ref: float) -> float:
    return (x - ref) / ref if ref else float("nan")


def speed(Fn: float, hull: Wigley = HULL) -> float:
    return Fn * math.sqrt(hull.g * hull.L)


# ---------------------------------------------------------------------------
# G : geometry gates
# ---------------------------------------------------------------------------
def check_geometry(res: dict) -> None:
    rec = {"inputs": {"L": L, "B": B, "T": T, "rho": RHO}}
    rec["Cb_exact"] = 4.0 / 9.0
    rec["Cb_module"] = HULL.cb
    rec["Cb_relerr"] = relerr(HULL.cb, 4.0 / 9.0)
    rec["volume_exact_m3"] = 4.0 * L * B * T / 9.0
    rec["volume_module_m3"] = HULL.volume
    rec["volume_relerr"] = relerr(HULL.volume, 4.0 * L * B * T / 9.0)

    s_ref = wetted_surface(HULL, n_x=400, n_z=160)
    conv = []
    for n in (50, 100, 200, 400):
        val = wetted_surface(HULL, n_x=n, n_z=n)
        conv.append({"n": n, "S_m2": val, "relerr_vs_400": relerr(val, s_ref)})
    rec["wetted_surface_convergence"] = conv
    rec["wetted_surface_m2"] = s_ref
    rec["wetted_surface_over_L2"] = s_ref / (L * L)

    # platform Wigley geometry endpoint (black box)
    try:
        import requests

        r = requests.get(f"{PLATFORM}/geometry/verify/wigley", timeout=20)
        rec["platform_geometry_status"] = r.status_code
        rec["platform_geometry"] = r.json() if r.status_code == 200 else str(r.text)[:300]
    except Exception as exc:  # pragma: no cover
        rec["platform_geometry_status"] = -1
        rec["platform_geometry"] = repr(exc)
    res["G_geometry"] = rec


# ---------------------------------------------------------------------------
# I : inner integrals
# ---------------------------------------------------------------------------
def check_inner_integrals(res: dict) -> None:
    import mpmath as mp

    rows = []
    with mp.workdps(30):
        for Fn in (0.15, 0.30, 0.35):
            U = speed(Fn)
            k0 = G / U**2
            for theta in (0.0, 0.25, 0.6, 0.9):
                sec = 1.0 / math.cos(theta)
                b, a = k0 * sec, k0 * sec**2
                ix_num = complex(mp.quad(lambda x: x * mp.e ** (1j * b * x), [-L / 2, L / 2]))
                iz_num = mp.quad(lambda z: (1 - (z / T) ** 2) * mp.e ** (-a * z), [0, T])
                rows.append(
                    {
                        "Fn": Fn,
                        "theta": theta,
                        "inner_x_relerr": abs(inner_x_closed(HULL, b) - ix_num) / abs(ix_num)
                        if abs(ix_num) > 0
                        else 0.0,
                        "inner_z_relerr": abs(inner_z_closed(HULL, a) - float(iz_num))
                        / float(iz_num),
                    }
                )
    worst_x = max(r["inner_x_relerr"] for r in rows)
    worst_z = max(r["inner_z_relerr"] for r in rows)
    res["I_inner_integrals"] = {
        "rows": rows,
        "worst_inner_x_relerr": worst_x,
        "worst_inner_z_relerr": worst_z,
        "tolerance": 1e-10,
        "verdict": "PASS" if max(worst_x, worst_z) < 1e-10 else "FAIL",
    }


# ---------------------------------------------------------------------------
# R : route consistency
# ---------------------------------------------------------------------------
def check_routes(res: dict) -> None:
    rows = []
    for Fn in (0.15, 0.20, 0.25, 0.30, 0.35):
        U = speed(Fn)
        a = rw_theta_integral(HULL, U, n_theta=400)
        b = rw_double_quad(HULL, U)
        c = rw_highprec(HULL, U, dps=25)
        rows.append(
            {
                "Fn": Fn,
                "route_A_N": a,
                "route_B_N": b,
                "route_C_N": c,
                "A_vs_B_relerr": relerr(a, b),
                "A_vs_C_relerr": relerr(a, c),
            }
        )
    worst_ab = max(abs(r["A_vs_B_relerr"]) for r in rows)
    worst_ac = max(abs(r["A_vs_C_relerr"]) for r in rows)
    res["R_routes"] = {
        "rows": rows,
        "worst_A_vs_B_relerr": worst_ab,
        "worst_A_vs_C_relerr": worst_ac,
        "tolerance_A_vs_B": 5e-4,
        "tolerance_A_vs_C": 1e-10,
        "verdict": "PASS" if worst_ab < 5e-4 and worst_ac < 1e-10 else "FAIL",
    }


# ---------------------------------------------------------------------------
# D : discretisation / convergence
# ---------------------------------------------------------------------------
def check_discretisation(res: dict) -> None:
    U = speed(FN_REF)
    fine = rw_theta_integral(HULL, U, n_theta=3200)
    levels = []
    for n in (50, 100, 200, 400, 800):
        val = rw_theta_integral(HULL, U, n_theta=n)
        levels.append({"n_theta": n, "Rw_N": val, "relerr_vs_3200": relerr(val, fine)})

    # observed order from three consecutive levels (n, 2n, 4n)
    p_obs = []
    for i in range(len(levels) - 2):
        e0, e1, e2 = (
            levels[i]["relerr_vs_3200"],
            levels[i + 1]["relerr_vs_3200"],
            levels[i + 2]["relerr_vs_3200"],
        )
        if e0 and e1 and e2 and e0 * e2 > 0:
            ratio = (e1 / e0) if abs(e0) > 0 else float("nan")
            if 0 < ratio < 1:
                p_obs.append(math.log(ratio) / math.log(0.5))

    # route B panel convergence
    b_levels = []
    b_fine = rw_double_quad(HULL, U, n_panel_x=64, n_panel_z=16, n_theta=192)
    for nx, nz, nt in ((16, 6, 48), (24, 8, 96), (32, 10, 96), (48, 12, 192)):
        val = rw_double_quad(HULL, U, n_panel_x=nx, n_panel_z=nz, n_theta=nt)
        b_levels.append(
            {
                "n_panel_x": nx,
                "n_panel_z": nz,
                "n_theta": nt,
                "Rw_N": val,
                "relerr_vs_finest": relerr(val, b_fine),
            }
        )

    res["D_discretisation"] = {
        "Fn": FN_REF,
        "U_ms": U,
        "route_A_levels": levels,
        "route_A_observed_order": p_obs,
        "route_B_levels": b_levels,
        "route_B_finest_N": b_fine,
        "route_A_finest_N": fine,
        "A_finest_vs_B_finest_relerr": relerr(fine, b_fine),
        "verdict": "PASS"
        if abs(levels[-1]["relerr_vs_3200"]) < 1e-6
        and abs(relerr(fine, b_fine)) < 5e-4
        else "FAIL",
    }


# ---------------------------------------------------------------------------
# P : analytic invariants of the Michell integral
# ---------------------------------------------------------------------------
def check_invariants(res: dict) -> None:
    U = speed(0.25)
    base = rw_theta_integral(HULL, U, n_theta=400)

    # Rw ~ B^2 at fixed L, T, U (exact: I is linear in B)
    b_rows = []
    for scale in (0.5, 0.75, 1.0, 1.25, 1.5):
        h = Wigley(L=L, B=B * scale, T=T, rho=RHO)
        val = rw_theta_integral(h, U, n_theta=400)
        b_rows.append(
            {
                "B_scale": scale,
                "Rw_N": val,
                "Rw_over_scale2": val / scale**2,
                "relerr_vs_B2": relerr(val / scale**2, base),
            }
        )

    # Rw ~ rho (exact: linear prefactor)
    h2 = Wigley(L=L, B=B, T=T, rho=2.0 * RHO)
    rho_err = relerr(rw_theta_integral(h2, U, n_theta=400), 2.0 * base)

    # Froude similarity of Cw: Cw(lambda) = lambda^2 Cw(1) at fixed Fn
    fr_rows = []
    fn = 0.28
    s1 = wetted_surface(HULL, n_x=300, n_z=120)
    cw1 = rw_theta_integral(HULL, speed(fn), n_theta=400) / (
        0.5 * RHO * speed(fn) ** 2 * s1
    )
    for lam in (0.25, 0.5, 1.0, 2.0):
        h = Wigley(L=L * lam, B=B * lam, T=T * lam, rho=RHO)
        s = wetted_surface(h, n_x=300, n_z=120)
        Ulam = fn * math.sqrt(G * L * lam)
        cw = rw_theta_integral(h, Ulam, n_theta=400) / (0.5 * RHO * Ulam**2 * s)
        fr_rows.append(
            {
                "lambda": lam,
                "Fn": fn,
                "Cw": cw,
                "Cw_over_lambda2": cw / lam**2,
                "relerr_vs_scaling": relerr(cw / lam**2, cw1),
            }
        )

    # low-Fn decay: Rw must fall steeply; check the log-slope against 1/Fn^2
    low = []
    for fn in (0.08, 0.10, 0.12, 0.15, 0.20):
        val = rw_theta_integral(HULL, speed(fn), n_theta=400)
        low.append({"Fn": fn, "Rw_N": val, "log_Rw": math.log(val) if val > 0 else None})
    slopes = []
    for i in range(1, len(low)):
        d_inv = 1.0 / low[i]["Fn"] ** 2 - 1.0 / low[i - 1]["Fn"] ** 2
        slopes.append((low[i]["log_Rw"] - low[i - 1]["log_Rw"]) / d_inv)

    res["P_invariants"] = {
        "B2_scaling": {
            "U_ms": U,
            "Fn": 0.25,
            "rows": b_rows,
            "worst_relerr": max(abs(r["relerr_vs_B2"]) for r in b_rows),
            "tolerance": 1e-9,
        },
        "rho_scaling": {"relerr": rho_err, "tolerance": 1e-12},
        "froude_similarity": {
            "rows": fr_rows,
            "worst_relerr": max(abs(r["relerr_vs_scaling"]) for r in fr_rows),
            "tolerance": 1e-8,
        },
        "low_fn_decay": {"rows": low, "dlogRw_dinvFn2": slopes},
        "verdict": "PASS"
        if max(abs(r["relerr_vs_B2"]) for r in b_rows) < 1e-9
        and abs(rho_err) < 1e-12
        and max(abs(r["relerr_vs_scaling"]) for r in fr_rows) < 1e-8
        else "FAIL",
    }


# ---------------------------------------------------------------------------
# S : platform comparison
# ---------------------------------------------------------------------------
def check_platform(res: dict, enabled: bool = True) -> None:
    rec = {"enabled": enabled, "endpoint": f"{PLATFORM}/resistance/predict"}
    if not enabled:
        res["S_platform"] = rec
        return
    disp_t = HULL.volume * RHO / 1000.0  # tonnes
    payload_common = {
        "Lwl": L,
        "Lpp": L,
        "B": B,
        "T": T,
        "displacement": disp_t,
        "Cb": 4.0 / 9.0,
        "Cm": 2.0 / 3.0,
        "Cwp": 2.0 / 3.0,
    }
    speeds_kn = [round(speed(fn) / KNOT, 3) for fn in FN_SWEEP]
    try:
        import requests

        r = requests.post(
            f"{PLATFORM}/resistance/predict",
            json={**payload_common, "speeds": speeds_kn},
            timeout=40,
        )
        rec["status"] = r.status_code
        if r.status_code != 200:
            rec["error"] = str(r.text)[:400]
            res["S_platform"] = rec
            return
        data = r.json()
    except Exception as exc:
        rec["status"] = -1
        rec["error"] = repr(exc)
        res["S_platform"] = rec
        return

    s_ref = wetted_surface(HULL, n_x=300, n_z=120)
    rows = []
    for row in data:
        U = row["speed"] * KNOT
        Fn = U / math.sqrt(G * L)
        rw_api = row["wave_resistance"] * 1e3  # kN -> N
        rw_m = rw_theta_integral(HULL, U, n_theta=400)
        cw_api = rw_api / (0.5 * RHO * U**2 * s_ref)
        cw_m = rw_m / (0.5 * RHO * U**2 * s_ref)
        rows.append(
            {
                "speed_kn": row["speed"],
                "U_ms": U,
                "Fn": Fn,
                "Rw_api_N": rw_api,
                "Rw_michell_N": rw_m,
                "Rw_ratio_api_over_michell": rw_api / rw_m if rw_m else None,
                "Cw_api": cw_api,
                "Cw_michell": cw_m,
                "Ct_api": row["total_resistance"] * 1e3 / (0.5 * RHO * U**2 * s_ref),
                "Cw_relerr_api_vs_michell": relerr(cw_api, cw_m),
                "Re_api": row["reynolds_number"],
                "Re_exact": U * L / NU,
            }
        )
    rec["payload"] = payload_common
    rec["speeds_kn"] = speeds_kn
    rec["rows"] = rows

    # shape agreement of the Cw(Fn) curve, independent of a constant prefactor
    try:
        import numpy as np

        x = np.array([r["Cw_michell"] for r in rows])
        y = np.array([r["Cw_api"] for r in rows])
        k = float((x @ y) / (x @ x))  # least-squares scale through the origin
        resid = y - k * x
        ss_res = float(resid @ resid)
        ss_tot = float(((y - y.mean()) ** 2).sum())
        rec["shape_fit"] = {
            "least_squares_scale_api_over_michell": k,
            "R2_unity_shape_after_scale": 1.0 - ss_res / ss_tot if ss_tot else None,
            "max_abs_relerr_after_scale": float(
                np.max(np.abs(resid / (k * x)))
            ),
        }
    except Exception as exc:  # pragma: no cover
        rec["shape_fit"] = {"error": repr(exc)}

    worst = max(abs(r["Cw_relerr_api_vs_michell"]) for r in rows)
    rec["worst_abs_Cw_relerr"] = worst
    rec["gate_15pct"] = "PASS" if worst < 0.15 else "FAIL"
    rec["gate_3pct"] = "PASS" if worst < 0.03 else "FAIL"
    rec["S_unreachable_note"] = (
        "model-to-model comparison: the platform's wave resistance is an empirical "
        "regression, Michell theory is a thin-ship approximation; agreement is a "
        "consistency statement, not experimental validation"
    )
    res["S_platform"] = rec


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(res: dict) -> list[str]:
    made = []

    # fig 1 : Cw(Fn) with the three routes and the platform
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    fns = [r["Fn"] for r in res["R_routes"]["rows"]]
    s_ref = res["G_geometry"]["wetted_surface_m2"]
    for key, style, label in (
        ("route_A_N", "o-", "route A: closed-form inner + Gauss theta"),
        ("route_B_N", "s--", "route B: numerical surface quadrature"),
        ("route_C_N", "^:", "route C: mpmath adaptive, 25 digits"),
    ):
        cw = [
            r[key] / (0.5 * RHO * speed(r["Fn"]) ** 2 * s_ref)
            for r in res["R_routes"]["rows"]
        ]
        ax.plot(fns, [1e3 * c for c in cw], style, label=label, ms=4, lw=1.2)
    plat = res.get("S_platform", {}).get("rows") or []
    if plat:
        ax.plot(
            [r["Fn"] for r in plat],
            [1e3 * r["Cw_api"] for r in plat],
            "d-",
            color="crimson",
            label="platform /resistance/predict",
            ms=5,
            lw=1.4,
        )
    ax.axhline(0, color="k", lw=0.4)
    ax.set_xlabel("Froude number  Fn = U/sqrt(gL)")
    ax.set_ylabel(r"$10^3\,C_w$   ($C_w = R_w/(\frac{1}{2}\rho S U^2)$)")
    ax.set_title("Wigley hull (L=100 m, B=10 m, T=6.25 m, Cb=4/9): thin-ship reference")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5)
    fig.tight_layout()
    p = FIG / "fig1_cw_vs_fn.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(HERE)))

    # fig 2 : convergence of route A and route B
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.0))
    d = res["D_discretisation"]
    nA = [x["n_theta"] for x in d["route_A_levels"]]
    eA = [abs(x["relerr_vs_3200"]) for x in d["route_A_levels"]]
    axes[0].loglog(nA, eA, "o-", label="route A vs n_theta=3200")
    axes[0].set_xlabel(r"theta Gauss nodes $n_\theta$")
    axes[0].set_ylabel("relative error of $R_w$")
    axes[0].set_title("route A theta convergence")
    axes[0].grid(alpha=0.3, which="both")
    axes[0].legend(fontsize=8)
    nB = [x["n_panel_x"] * x["n_theta"] for x in d["route_B_levels"]]
    eB = [abs(x["relerr_vs_finest"]) for x in d["route_B_levels"]]
    axes[1].loglog(nB, eB, "s-", color="darkorange", label="route B vs finest grid")
    axes[1].set_xlabel(r"$n_{panel,x}\times n_\theta$")
    axes[1].set_title("route B surface-quadrature convergence")
    axes[1].grid(alpha=0.3, which="both")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "fig2_convergence.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(HERE)))

    # fig 3 : analytic invariants
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 3.6))
    b = res["P_invariants"]["B2_scaling"]["rows"]
    axes[0].plot([r["B_scale"] for r in b], [r["Rw_over_scale2"] for r in b], "o-")
    axes[0].set_xlabel("B / B0 at fixed L, T, U")
    axes[0].set_ylabel(r"$R_w/(B/B_0)^2$  [N]")
    axes[0].set_title(r"exact invariant  $R_w \propto B^2$")
    axes[0].grid(alpha=0.3)
    fr = res["P_invariants"]["froude_similarity"]["rows"]
    axes[1].plot([r["lambda"] for r in fr], [r["Cw_over_lambda2"] for r in fr], "s-")
    axes[1].set_xscale("log")
    axes[1].set_xlabel(r"geometric scale $\lambda$ at fixed Fn")
    axes[1].set_ylabel(r"$C_w/\lambda^2$")
    axes[1].set_title(r"exact invariant  $C_w(\lambda)=\lambda^2 C_w(1)$")
    axes[1].grid(alpha=0.3, which="both")
    low = res["P_invariants"]["low_fn_decay"]["rows"]
    axes[2].plot(
        [1.0 / r["Fn"] ** 2 for r in low], [r["log_Rw"] for r in low], "^-"
    )
    axes[2].set_xlabel(r"$1/Fn^2$")
    axes[2].set_ylabel(r"$\ln R_w$ [N]")
    axes[2].set_title("low-Fn exponential decay of $R_w$")
    axes[2].grid(alpha=0.3)
    fig.tight_layout()
    p = FIG / "fig3_invariants.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(HERE)))

    # fig 4 : prefactor sensitivity band vs platform
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    fns = [r["Fn"] for r in res["R_routes"]["rows"]]
    cw1 = [
        r["route_A_N"] / (0.5 * RHO * speed(r["Fn"]) ** 2 * s_ref)
        for r in res["R_routes"]["rows"]
    ]
    ax.fill_between(
        fns,
        [1e3 * c for c in cw1],
        [2e3 * c for c in cw1],
        color="steelblue",
        alpha=0.25,
        label="thin-ship band: prefactor convention (side_factor 1..2)",
    )
    ax.plot(fns, [1e3 * c for c in cw1], "b-", lw=1.2, label="adopted convention (side_factor=1)")
    if plat:
        ax.plot(
            [r["Fn"] for r in plat],
            [1e3 * r["Cw_api"] for r in plat],
            "d-",
            color="crimson",
            label="platform wave resistance",
        )
    ax.set_xlabel("Fn")
    ax.set_ylabel(r"$10^3 C_w$")
    ax.set_title("prefactor sensitivity vs platform wave resistance")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "fig4_prefactor_band.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(HERE)))

    # fig 5 : Wigley hull geometry (body plan + lines) for the report
    fig, ax = plt.subplots(1, 2, figsize=(10.4, 3.8))
    xx = [(-0.5 + i / 400.0) * L for i in range(401)]
    ax[0].plot(xx, [HULL.half_beam(x, 0.0) for x in xx], "b-", label="waterline  z=0")
    ax[0].plot(xx, [HULL.half_beam(x, 0.5 * T) for x in xx], "g--", label="z=T/2")
    ax[0].plot(xx, [HULL.half_beam(x, 0.95 * T) for x in xx], "r:", label="z=0.95T")
    ax[0].set_xlabel("x [m]")
    ax[0].set_ylabel("half-breadth y [m]")
    ax[0].set_title("Wigley hull waterlines")
    ax[0].grid(alpha=0.3)
    ax[0].legend(fontsize=8)
    zz = [T * i / 200.0 for i in range(201)]
    for xs, lab in ((0.0, "midship x=0"), (0.25 * L, "x=L/4"), (0.45 * L, "x=0.45L")):
        ax[1].plot([HULL.half_beam(xs, z) for z in zz], zz, label=lab)
    ax[1].invert_yaxis()
    ax[1].set_xlabel("half-breadth [m]")
    ax[1].set_ylabel("z below WL [m]")
    ax[1].set_title("body plan (Cb=4/9, Cm=Cwp=2/3 exact)")
    ax[1].grid(alpha=0.3)
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "fig5_wigley_geometry.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(HERE)))

    return made


# ---------------------------------------------------------------------------
# markdown tables
# ---------------------------------------------------------------------------
def write_tables(res: dict) -> None:
    lines = ["<!-- generated by run_verification.py -- do not edit by hand -->", ""]
    lines.append("### Route consistency (Michell wave resistance, Wigley hull)")
    lines.append("")
    lines.append("| Fn | route A [N] | route B [N] | route C [N] | A vs B | A vs C |")
    lines.append("|---|---|---|---|---|---|")
    for r in res["R_routes"]["rows"]:
        lines.append(
            "| {:.2f} | {:.6e} | {:.6e} | {:.6e} | {:+.2e} | {:+.2e} |".format(
                r["Fn"], r["route_A_N"], r["route_B_N"], r["route_C_N"],
                r["A_vs_B_relerr"], r["A_vs_C_relerr"],
            )
        )
    lines.append("")
    lines.append("### Route A theta convergence (Fn=0.30)")
    lines.append("")
    lines.append("| n_theta | Rw [N] | rel. err vs n=3200 |")
    lines.append("|---|---|---|")
    for r in res["D_discretisation"]["route_A_levels"]:
        lines.append(
            "| {} | {:.8e} | {:+.2e} |".format(r["n_theta"], r["Rw_N"], r["relerr_vs_3200"])
        )
    lines.append("")
    lines.append("### Exact invariants")
    lines.append("")
    lines.append("| check | worst relative error | tolerance |")
    lines.append("|---|---|---|")
    p = res["P_invariants"]
    lines.append(
        "| $R_w \\propto B^2$ | {:.2e} | {:.0e} |".format(
            abs(p["B2_scaling"]["worst_relerr"]), p["B2_scaling"]["tolerance"]
        )
    )
    lines.append(
        "| $R_w \\propto \\rho$ | {:.2e} | {:.0e} |".format(
            abs(p["rho_scaling"]["relerr"]), p["rho_scaling"]["tolerance"]
        )
    )
    lines.append(
        "| $C_w(\\lambda)=\\lambda^2 C_w(1)$ | {:.2e} | {:.0e} |".format(
            abs(p["froude_similarity"]["worst_relerr"]), p["froude_similarity"]["tolerance"]
        )
    )
    lines.append(
        "| inner integrals (closed vs 30-digit quad) | {:.2e} | 1e-10 |".format(
            max(
                res["I_inner_integrals"]["worst_inner_x_relerr"],
                res["I_inner_integrals"]["worst_inner_z_relerr"],
            )
        )
    )
    lines.append("")
    sp = res.get("S_platform", {})
    if sp.get("rows"):
        lines.append("### Platform `/resistance/predict` vs Michell thin-ship")
        lines.append("")
        lines.append("| speed [kn] | Fn | Rw api [N] | Rw Michell [N] | api/Michell | Cw api | Cw Michell | Cw rel. err |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in sp["rows"]:
            lines.append(
                "| {:.2f} | {:.3f} | {:.4e} | {:.4e} | {:.3f} | {:.4e} | {:.4e} | {:+.1%} |".format(
                    r["speed_kn"], r["Fn"], r["Rw_api_N"], r["Rw_michell_N"],
                    r["Rw_ratio_api_over_michell"], r["Cw_api"], r["Cw_michell"],
                    r["Cw_relerr_api_vs_michell"],
                )
            )
        lines.append("")
        sf = sp.get("shape_fit", {})
        lines.append(
            "Shape fit after removing a single scale factor: k = {:.4f}, "
            "R² = {:.4f}, max |resid| = {:.1%}.".format(
                sf.get("least_squares_scale_api_over_michell", float("nan")),
                sf.get("R2_unity_shape_after_scale", float("nan")),
                sf.get("max_abs_relerr_after_scale", float("nan")),
            )
        )
        lines.append("")
    (ART / "verification_tables.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-platform", action="store_true")
    args = ap.parse_args()

    res: dict = {
        "module": "docs/verification_debt/wigley_michell/michell_wigley.py",
        "hull": {"L": L, "B": B, "T": T, "rho": RHO},
        "prefactor_adopted": michell_prefactor(RHO, speed(FN_REF), 1.0),
        "prefactor_convention": "4 rho g^2/(pi U^2) with half-hull I (side_factor=1)",
    }
    check_geometry(res)
    check_inner_integrals(res)
    check_routes(res)
    check_discretisation(res)
    check_invariants(res)
    check_platform(res, enabled=not args.no_platform)
    res["figures"] = make_figures(res)
    write_tables(res)

    verdicts = {
        k: res[k].get("verdict")
        for k in ("I_inner_integrals", "R_routes", "D_discretisation", "P_invariants")
        if isinstance(res.get(k), dict)
    }
    res["verdicts"] = verdicts
    res["all_pass"] = all(v == "PASS" for v in verdicts.values())
    (ART / "verification_verdicts.json").write_text(
        json.dumps(res, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(verdicts, indent=2))
    print("all_pass:", res["all_pass"])
    if res.get("S_platform", {}).get("rows"):
        print("platform gate 15%:", res["S_platform"]["gate_15pct"],
              "3%:", res["S_platform"]["gate_3pct"],
              "worst Cw rel.err: %.1f%%" % (100 * res["S_platform"]["worst_abs_Cw_relerr"]))
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())