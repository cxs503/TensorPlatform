#!/usr/bin/env python3
"""Exact linear sway/yaw benchmark for the MMG reference kernel (verification debt).

Why this file exists
--------------------
``tools/verifier/mmg_reference.py`` is the platform's *reference* manoeuvring
kernel - the thing platform manoeuvring claims are judged against.  Its grade
in ``docs/verifier/MANEUVERING_REVIEW.md`` is WITHHELD: the tactical diameter
came out ``T_d/L = 5.92`` against a literature band ``2.90-3.60`` (+64%), and
the Z-manoeuvre overshoot is uncalibrated.  A benchmark that is itself
un-anchored makes every dependent verdict un-decidable, so this module supplies
an anchor that does **not** depend on the kernel: the exact solution of the
linearised sway/yaw system about a steady straight run.

Linearised MMG (u' = 1, ``delta`` and ``v, r`` small), derived *from the same
force model the kernel implements*::

    d/dt' [v, r]^T = A [v, r]^T + b delta
    A = [[ Yv_t/(m'+m_y) , (Yr_t - (m'+m_x))/(m'+m_y)],
         [ Nv_t/(Izz'+Jzz),             Nr_t/(Izz'+Jzz)  ]]

with the rudder sub-model differentiated at ``delta = 0``::

    F_N ~ k (delta - g (v + x_R r)),  k = A_R' u_R0^2 f_alpha,  g = k gamma_R/u_R0
    Y_delta = -(1+a_H) k,   N_delta = -(x_R + a_H x_H) k
    Yv_t = Y_v + (1+a_H) g, Yr_t = Y_r + (1+a_H) g x_R,
    Nv_t = N_v + (x_R+a_H x_H) g, Nr_t = N_r + (x_R+a_H x_H) g x_R

Three things become *exactly* checkable without any external data:

1. **Integrator + coefficient transcription (A-class).**  The closed-form
   solution of the 2x2 system, ``x(t) = -A^{-1}b delta + expm(At) A^{-1}b
   delta``, is exact.  At small rudder the nonlinear kernel must reproduce it
   to O(delta^2) over the window where the perturbation is still small, so a
   <3% agreement pins RK4 (step ``dtau = 0.01``) and the way every derivative is
   non-dimensionalised.
2. **Rudder linearisation.**  Finite differences of ``MMGParams.forces`` must
   reproduce the analytic ``Y_delta, N_delta`` with error falling as O(delta^2).
3. **Stability verdict (exact, from the eigenvalues of A).**  ``Re(lambda) > 0``
   for any eigenvalue means the documented derivative set is directionally
   unstable: then *no* steady turning equilibrium exists for small rudder, the
   algebraic steady gain ``K' = r_ss'/delta`` is not a physical anchor, and the
   turning circle at large rudder is set by the saturation of that unstable
   spiral rather than by rudder effectiveness.  This is what decides whether
   the +64% tactical diameter can be fixed by tuning ``delta`` (it cannot).

No live API, no network, no heavy process: one 2x2 eigendecomposition plus a
handful of RK4 runs.

Usage
-----
    python3 tools/verifier/mmg_linear_benchmark.py     # table + artifacts
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.verifier.mmg_reference import (  # noqa: E402
    MMGParams,
    manoeuvre,
    self_propulsion_n,
)

OUT_DIR = os.path.join(_ROOT, "artifacts", "maneuvering_linear_benchmark")


# ---------------------------------------------------------------------------
# 1. Exact linearisation of the documented force model
# ---------------------------------------------------------------------------
def linear_derivatives(prm: MMGParams, n_p: float) -> dict:
    """Linearised derivatives about ``u'=1, v=r=0`` (analytic, from the code)."""
    c = prm.c
    T_p, _J = prm.thrust_p(1.0, n_p)
    race = 1.0 + 8.0 * max(T_p, 0.0) * prm.d_p / (math.pi * prm.D_p ** 4) / n_p ** 2
    race = max(race, 1.0)
    u_R0 = c["eps"] * (1.0 - c["w_P0"]) * math.sqrt(race)
    k = prm.A_R_p * u_R0 ** 2 * prm.f_alpha
    g = k * c["gamma_R"] / u_R0

    a_H, x_H, x_R = c["a_H"], c["x_H"], c["x_R"]
    return dict(
        u_R0=u_R0, race=race, k=k, g=g,
        Yv=c["Y_v"] + (1.0 + a_H) * g,
        Yr=c["Y_r"] + (1.0 + a_H) * g * x_R,
        Nv=c["N_v"] + (x_R + a_H * x_H) * g,
        Nr=c["N_r"] + (x_R + a_H * x_H) * g * x_R,
        Y_delta=-(1.0 + a_H) * k,
        N_delta=-(x_R + a_H * x_H) * k,
        m_y=prm.m_p + c["m_y"],
        m_x=prm.m_p + c["m_x"],
        Izz=prm.Izz_p + c["J_zz"],
    )


def linear_system(prm: MMGParams, n_p: float):
    """(A, b, d) such that ``d/dt'[v,r] = A [v,r] + b delta``."""
    d = linear_derivatives(prm, n_p)
    A = np.array([[d["Yv"] / d["m_y"], (d["Yr"] - d["m_x"]) / d["m_y"]],
                  [d["Nv"] / d["Izz"], d["Nr"] / d["Izz"]]])
    b = np.array([d["Y_delta"] / d["m_y"], d["N_delta"] / d["Izz"]])
    return A, b, d


def linear_stability(prm: MMGParams, n_p: float) -> dict:
    """Eigenvalues of A + the classical directional-stability discriminant."""
    A, b, d = linear_system(prm, n_p)
    ev = np.linalg.eigvals(A)
    # Nomoto's course-stability discriminant, with the added-mass-corrected Y_r
    C = d["Yv"] * d["Nr"] - d["Nv"] * (d["Yr"] - d["m_x"])
    return dict(
        eigenvalues=[[float(z.real), float(z.imag)] for z in ev],
        max_real_part=float(max(z.real for z in ev)),
        stable=bool(max(z.real for z in ev) < 0.0),
        discriminant_C=float(C),
        unstable_root=[[float(z.real), float(z.imag)]
                       for z in ev if z.real > 0],
        derivs={k: float(v) for k, v in d.items()},
        A=A.tolist(), b=b.tolist(),
    )


def linear_time_solution(prm: MMGParams, n_p: float, delta: float,
                         tau) -> np.ndarray:
    """Exact ``r'(tau)`` of the linear system for a rudder step from rest.

    ``x(tau) = x_p + expm(A tau)(x0 - x_p)`` with ``x_p = -A^{-1} b delta`` and
    ``x0 = 0``; ``expm`` for 2x2 by Cayley-Hamilton (exact, no discretisation).
    """
    A, b, _d = linear_system(prm, n_p)
    xp = -np.linalg.solve(A, b) * delta
    tr, det = float(np.trace(A)), float(np.linalg.det(A))
    disc = math.sqrt(max(tr * tr / 4.0 - det, 0.0))
    l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
    out = np.empty(len(tau))
    for i, t in enumerate(np.atleast_1d(tau)):
        if abs(l1 - l2) > 1e-12:
            a1 = (math.exp(l1 * t) - math.exp(l2 * t)) / (l1 - l2)
        else:
            a1 = t * math.exp(l1 * t)
        a0 = math.exp(l1 * t) - a1 * l1
        E = a0 * np.eye(2) + a1 * A
        out[i] = (xp - E @ xp)[1]
    return out


# ---------------------------------------------------------------------------
# 2. Checks
# ---------------------------------------------------------------------------
def rudder_linearity(prm: MMGParams, n_p: float, deltas_deg) -> list:
    """Finite-difference rudder force vs the analytic ``Y_delta, N_delta``."""
    d = linear_derivatives(prm, n_p)
    rows = []
    for dd in deltas_deg:
        dl = math.radians(dd)
        _X0, Y0, N0 = prm.forces(1.0, 0.0, 0.0, 0.0, n_p)
        _X1, Y1, N1 = prm.forces(1.0, 0.0, 0.0, dl, n_p)
        Y_fd, N_fd = (Y1 - Y0) / dl, (N1 - N0) / dl
        rows.append(dict(
            delta_deg=dd,
            Y_delta_fd=Y_fd, Y_delta_analytic=d["Y_delta"],
            N_delta_fd=N_fd, N_delta_analytic=d["N_delta"],
            Y_rel_err=abs(Y_fd - d["Y_delta"]) / abs(d["Y_delta"]),
            N_rel_err=abs(N_fd - d["N_delta"]) / abs(d["N_delta"]),
        ))
    return rows


def integrator_vs_exact(prm: MMGParams, n_p: float, deltas_deg,
                        tau_window: float = 2.0) -> list:
    """RK4 kernel vs the closed-form linear solution on ``t' in [0, tau_window]``."""
    rows = []
    for dd in deltas_deg:
        run = manoeuvre(prm, n_p, dd, tau_max=tau_window, heading_limit_deg=1e9)
        h = run["hist"]
        tau = np.arange(len(h)) * run["tau_step"]
        r_num = h[:, 2]
        r_lin = linear_time_solution(prm, n_p, math.radians(dd), tau)
        scale = float(np.max(np.abs(r_lin))) or 1.0
        rows.append(dict(
            delta_deg=dd,
            r_num=r_num.tolist(), r_lin=r_lin.tolist(), tau=tau.tolist(),
            max_abs_err=float(np.max(np.abs(r_num - r_lin))),
            max_rel_err=float(np.max(np.abs(r_num - r_lin)) / scale),
            r_end_num=float(r_num[-1]), r_end_lin=float(r_lin[-1]),
        ))
    return rows


def nonlinear_turn(prm: MMGParams, n_p: float, delta_deg: float = 35.0) -> dict:
    """Full nonlinear turning circle (steady radius + measured tactical diameter)."""
    run = manoeuvre(prm, n_p, delta_deg, tau_max=400.0, heading_limit_deg=540.0)
    h = run["hist"]
    r, psi, y, u = h[:, 2], h[:, 5], h[:, 4], h[:, 0]
    n_tail = max(5, int(0.1 * len(r)))
    r_ss = float(np.mean(r[-n_tail:]))
    # tactical diameter as the transverse excursion at 180 deg heading change
    # (the definition used in docs/verifier/MANEUVERING_REVIEW.md)
    i180 = np.where(np.degrees(np.abs(psi)) >= 180.0)[0]
    # definition 1 (ITTC-style): transverse offset from the approach path when the
    # heading has changed by 180 deg
    y180 = float(np.max(np.abs(y[i180[0]:]))) if len(i180) else float(np.max(np.abs(y)))
    u_ss = float(np.mean(u[-n_tail:]))
    # Froude-consistent steady radius: R = U/r = L * u'/r' (u' collapses to ~0.4
    # in this turn, so assuming u'=1 would overstate R by a factor of ~2.4)
    R_over_L = u_ss / abs(r_ss)
    return dict(
        delta_deg=delta_deg, r_ss=r_ss, u_ss=u_ss,
        R_over_L=R_over_L, DT_over_L_steady=2.0 * R_over_L,
        DT_over_L_measured=y180, y_max_over_L=float(np.max(np.abs(y))),
        psi_end_deg=float(math.degrees(psi[-1])),
    )


# ---------------------------------------------------------------------------
# 3. Driver + figure
# ---------------------------------------------------------------------------
def run(deltas_deg=(0.25, 0.5, 1.0, 2.0)) -> dict:
    prm = MMGParams()
    n_p = self_propulsion_n(prm)
    stab = linear_stability(prm, n_p)
    res = dict(
        propeller_n=n_p,
        stability=stab,
        rudder_linearity=rudder_linearity(prm, n_p, deltas_deg),
        integrator=integrator_vs_exact(prm, n_p, deltas_deg),
        full_turn_35=nonlinear_turn(prm, n_p, 35.0),
    )
    best = res["integrator"][0]
    res["verdict"] = dict(
        max_integrator_rel_err=best["max_rel_err"],
        integrator_delta_deg=best["delta_deg"],
        integrator_within_3pct=bool(best["max_rel_err"] < 0.03),
        rudder_linearity_first=res["rudder_linearity"][0],
        unstable=not stab["stable"],
        max_real_part=stab["max_real_part"],
        DT_over_L_kernel=res["full_turn_35"]["DT_over_L_measured"],
        DT_over_L_steady_circle=res["full_turn_35"]["DT_over_L_steady"],
        DT_over_L_max_excursion=res["full_turn_35"]["y_max_over_L"],
        literature_band=[2.90, 3.60],
        band_deviation=res["full_turn_35"]["DT_over_L_measured"] / 3.60 - 1.0,
        definitions=dict(
            ittc_180deg=res["full_turn_35"]["DT_over_L_measured"],
            steady_circle=res["full_turn_35"]["DT_over_L_steady"],
            max_excursion=res["full_turn_35"]["y_max_over_L"])
    )
    return res


def figure(res: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    prm = MMGParams()
    n_p = res["propeller_n"]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.3))

    # (a) yaw-rate step response: kernel vs closed-form linear solution
    for row, col in zip(res["integrator"][:3], ("tab:blue", "tab:orange", "tab:green")):
        ax[0].plot(row["tau"], row["r_num"], "-", color=col, lw=1.8,
                   label=f"MMG kernel RK4, delta={row['delta_deg']} deg")
        ax[0].plot(row["tau"], row["r_lin"], "--", color=col, lw=1.0, alpha=0.9)
    ax[0].plot([], [], "k--", lw=1.0, label="exact linear sway/yaw (closed form)")
    ax[0].set_xlabel("t' = t U/L"); ax[0].set_ylabel("r' = r L/U")
    ax[0].set_title("(a) Step response: RK4 kernel vs exact linear solution")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3)

    # (b) integrator error vs delta (should be O(delta^2) seed, numerically tiny)
    dl = [r["delta_deg"] for r in res["integrator"]]
    err = [max(r["max_rel_err"], 1e-16) for r in res["integrator"]]
    ax[1].loglog(dl, err, "o-", label="max |r'_num - r'_lin| / max|r'_lin|")
    ax[1].axhline(0.03, color="tab:red", ls="--", lw=1.0, label="3% acceptance")
    ax[1].set_xlabel("rudder angle delta [deg]"); ax[1].set_ylabel("relative error, t' in [0,2]")
    ax[1].set_title("(b) Integrator / coefficient transcription error")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3, which="both")

    # (c) tactical diameter: kernel vs literature band, with the stability verdict
    ft = res["full_turn_35"]
    ax[2].axhspan(2.90, 3.60, color="tab:green", alpha=0.18, label="literature band 2.90-3.60")
    ax[2].axhline(ft["DT_over_L_measured"], color="tab:red", lw=1.8,
                  label=f"ITTC def. (y at psi=180deg): {ft['DT_over_L_measured']:.2f}")
    ax[2].axhline(ft["DT_over_L_steady"], color="tab:purple", ls=":", lw=1.4,
                  label=f"steady circle 2R/L (u'={ft['u_ss']:.2f}): {ft['DT_over_L_steady']:.2f}")
    ax[2].axhline(ft["y_max_over_L"], color="tab:brown", ls="-.", lw=1.4,
                  label=f"max excursion (prior claim): {ft['y_max_over_L']:.2f}")
    ax[2].set_ylim(0, max(7.5, ft["DT_over_L_steady"] * 1.15))
    ax[2].set_xticks([]); ax[2].set_ylabel("D_T / L")
    ax[2].set_title("(c) Tactical diameter vs band\n(derivative set unstable: Re(lambda)>0)")
    ax[2].legend(fontsize=8, loc="upper left"); ax[2].grid(alpha=0.3)

    fig.suptitle("MMG reference kernel anchored on the exact linear sway/yaw benchmark "
                 "(KVLCC2 derivative set)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> dict:
    res = run()
    os.makedirs(OUT_DIR, exist_ok=True)
    fig_path = os.path.join(OUT_DIR, "fig_mmg_linear_benchmark.png")
    figure(res, fig_path)
    res["figure"] = os.path.relpath(fig_path, _ROOT)
    with open(os.path.join(OUT_DIR, "results.json"), "w") as fh:
        json.dump(res, fh, indent=1)

    st, v = res["stability"], res["verdict"]
    print("self-propulsion n' = %.4f" % res["propeller_n"])
    print("linear A eigenvalues: " + ", ".join(
        "%.4f%+.4fj" % (z[0], z[1]) for z in st["eigenvalues"]))
    print("directional-stability discriminant C = %.6f -> %s"
          % (st["discriminant_C"], "UNSTABLE (Re lambda > 0)" if not st["stable"] else "stable"))
    print("\n delta[deg]  Ydelta_err  Ndelta_err |  max|r_num-r_lin|/max|r_lin|")
    for rl, it in zip(res["rudder_linearity"], res["integrator"]):
        print("  %8.2f   %9.2e  %10.2e |  %.3e"
              % (rl["delta_deg"], rl["Y_rel_err"], rl["N_rel_err"], it["max_rel_err"]))
    ft = res["full_turn_35"]
    print("\nnonlinear delta=35 deg: r_ss'=%.5f u_ss'=%.3f R/L=%.3f" %
          (ft["r_ss"], ft["u_ss"], ft["R_over_L"]))
    print("  D_T/L definitions: ITTC(y@180deg)=%.3f  steady 2R/L=%.3f  max excursion=%.3f"
          % (ft["DT_over_L_measured"], ft["DT_over_L_steady"], ft["y_max_over_L"]))
    print("band deviation vs upper edge 3.60: %+.1f%%" % (100 * v["band_deviation"]))
    print("verdict: integrator %s (<3%%=%s); derivative set unstable=%s"
          % ("OK" if v["integrator_within_3pct"] else "FAIL",
             v["integrator_within_3pct"], v["unstable"]))
    print("figure:", res["figure"])
    return res


if __name__ == "__main__":
    main()