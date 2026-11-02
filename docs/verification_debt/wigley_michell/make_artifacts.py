"""Fast artifact generator for the Wigley/Michell verification debt.

This is the bounded-cost companion of ``run_verification.py``: it performs the
same checks with quadrature orders chosen for a <60 s run and writes

    artifacts/verification_verdicts.json
    artifacts/verification_tables.md
    figs/fig1_cw_vs_fn.png, fig2_convergence.png, fig3_invariants.png,
    figs/fig4_wigley_geometry.png

Every number in the JSON is computed here (nothing hand-copied).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from michell_wigley import (  # noqa: E402
    Wigley,
    amplitude_closed,
    amplitude_double_quad,
    inner_x_closed,
    inner_z_closed,
    panel_gauss,
    rw_double_quad,
    rw_highprec,
    rw_theta_integral,
    wetted_surface,
)

ART, FIG = HERE / "artifacts", HERE / "figs"
ART.mkdir(exist_ok=True)
FIG.mkdir(exist_ok=True)
RHO, G, NU, KNOT = 1025.0, 9.81, 1.19e-6, 0.5144444444444445
L, B, T = 100.0, 10.0, 6.25
H = Wigley(L=L, B=B, T=T, rho=RHO)
FN_SWEEP = [0.15, 0.20, 0.25, 0.30, 0.35]
PLATFORM = "http://127.0.0.1:8001/api/v1"


def rel(x, ref):
    return (x - ref) / ref if ref else float("nan")


def U_of(fn, hull=H):
    return fn * math.sqrt(hull.g * hull.L)


res = {"module": "docs/verification_debt/wigley_michell/michell_wigley.py",
       "hull": {"L": L, "B": B, "T": T, "rho": RHO}}

# ---------------------------------------------------------------- geometry
S_fine = wetted_surface(H, n_x=200, n_z=80)
conv = [{"n": n, "S_m2": wetted_surface(H, n_x=n, n_z=n), "relerr_vs_200": rel(wetted_surface(H, n_x=n, n_z=n), S_fine)}
        for n in (25, 50, 100, 200)]
res["G_geometry"] = {
    "Cb_exact": 4.0 / 9.0, "Cb_module": H.cb, "Cb_relerr": rel(H.cb, 4.0 / 9.0),
    "volume_exact_m3": 4.0 * L * B * T / 9.0, "volume_module_m3": H.volume,
    "volume_relerr": rel(H.volume, 4.0 * L * B * T / 9.0),
    "Cm_exact": 2.0 / 3.0, "Cwp_exact": 2.0 / 3.0,
    "wetted_surface_m2": S_fine, "wetted_surface_over_L2": S_fine / L**2,
    "wetted_surface_convergence": conv,
    "verdict": "PASS" if abs(rel(H.cb, 4 / 9)) < 1e-15 and abs(rel(conv[-1]["S_m2"], S_fine)) < 1e-8 else "FAIL",
}

# --------------------------------------------------- inner integrals (closed vs numeric)
inner = []
for fn in (0.15, 0.30):
    U = U_of(fn)
    k0 = G / U**2
    for th in (0.25, 0.6):
        sec = 1.0 / math.cos(th)
        b, a = k0 * sec, k0 * sec**2
        x, wx = panel_gauss(-L / 2, L / 2, 48, 24)
        z, wz = panel_gauss(0.0, T, 12, 24)
        ix = complex(np.sum(wx * x * np.exp(1j * b * x)))
        iz = float(np.sum(wz * (1 - (z / T) ** 2) * np.exp(-a * z)))
        amp_n = amplitude_double_quad(H, U, th)
        amp_c = amplitude_closed(H, U, th)
        inner.append({"Fn": fn, "theta": th,
                      "inner_x_relerr": abs(ix - inner_x_closed(H, b)) / abs(ix),
                      "inner_z_relerr": abs(iz - inner_z_closed(H, a)) / iz,
                      "amplitude_relerr": abs(amp_n - amp_c) / abs(amp_c)})
worst_inner = max(max(r["inner_x_relerr"], r["inner_z_relerr"], r["amplitude_relerr"]) for r in inner)
res["I_inner_integrals"] = {"rows": inner, "worst_relerr": worst_inner, "tolerance": 1e-6,
                            "verdict": "PASS" if worst_inner < 1e-6 else "FAIL"}

# ---------------------------------------------------------------- routes A/B/C
routes = []
for fn in FN_SWEEP:
    U = U_of(fn)
    a = rw_theta_integral(H, U, n_theta=300)
    b = rw_double_quad(H, U)
    c = rw_highprec(H, U, dps=20)
    routes.append({"Fn": fn, "U_ms": U, "route_A_N": a, "route_B_N": b, "route_C_N": c,
                   "A_vs_B_relerr": rel(a, b), "A_vs_C_relerr": rel(a, c),
                   "Cw_A": a / (0.5 * RHO * U**2 * S_fine)})
wAB = max(abs(r["A_vs_B_relerr"]) for r in routes)
wAC = max(abs(r["A_vs_C_relerr"]) for r in routes)
res["R_routes"] = {"rows": routes, "worst_A_vs_B_relerr": wAB, "worst_A_vs_C_relerr": wAC,
                   "tolerance_A_vs_B": 1e-3, "tolerance_A_vs_C": 1e-8,
                   "verdict": "PASS" if wAB < 1e-3 and wAC < 1e-8 else "FAIL"}

# ------------------------------------------------------------ discretisation
U = U_of(0.30)
fine = rw_theta_integral(H, U, n_theta=1600)
lev = [{"n_theta": n, "Rw_N": rw_theta_integral(H, U, n_theta=n),
        "relerr_vs_1600": rel(rw_theta_integral(H, U, n_theta=n), fine)} for n in (50, 100, 200, 400, 800)]
orders = []
for i in range(len(lev) - 2):
    e0, e1, e2 = (lev[i]["relerr_vs_1600"], lev[i + 1]["relerr_vs_1600"], lev[i + 2]["relerr_vs_1600"])
    if min(abs(e0), abs(e1), abs(e2)) > 0 and e0 * e1 > 0:
        orders.append(math.log(abs(e1 / e0)) / math.log(0.5))
bfine = rw_double_quad(H, U, n_panel_x=48, n_panel_z=12, n_theta=96)
blev = []
for nx, nz, nt in ((12, 5, 48), (24, 8, 96), (32, 10, 96)):
    val = rw_double_quad(H, U, n_panel_x=nx, n_panel_z=nz, n_theta=nt)
    blev.append({"n_panel_x": nx, "n_panel_z": nz, "n_theta": nt, "Rw_N": val,
                 "relerr_vs_finest": rel(val, bfine)})
res["D_discretisation"] = {
    "Fn": 0.30, "U_ms": U, "route_A_levels": lev, "route_A_observed_order": orders,
    "route_B_levels": blev, "route_A_finest_N": fine, "route_B_finest_N": bfine,
    "A_finest_vs_B_finest_relerr": rel(fine, bfine),
    "verdict": "PASS" if abs(lev[-1]["relerr_vs_1600"]) < 1e-5 and abs(rel(fine, bfine)) < 1e-3 else "FAIL",
}

# ---------------------------------------------------------------- invariants
Ub = U_of(0.25)
base = rw_theta_integral(H, Ub, n_theta=300)
brows = []
for sc in (0.5, 0.75, 1.0, 1.25, 1.5):
    v = rw_theta_integral(Wigley(L=L, B=B * sc, T=T), Ub, n_theta=300)
    brows.append({"B_scale": sc, "Rw_N": v, "Rw_over_scale2": v / sc**2, "relerr_vs_B2": rel(v / sc**2, base)})
rho_err = rel(rw_theta_integral(Wigley(L=L, B=B, T=T, rho=2 * RHO), Ub, n_theta=300), 2 * base)
fn = 0.28
cw1 = rw_theta_integral(H, U_of(fn), n_theta=300) / (0.5 * RHO * U_of(fn) ** 2 * S_fine)
frows = []
for lam in (0.5, 1.0, 2.0):
    hh = Wigley(L=L * lam, B=B * lam, T=T * lam)
    s = wetted_surface(hh, n_x=200, n_z=80)
    Uu = fn * math.sqrt(G * L * lam)
    cw = rw_theta_integral(hh, Uu, n_theta=300) / (0.5 * RHO * Uu**2 * s)
    frows.append({"lambda": lam, "Fn": fn, "S_m2": s, "Cw": cw, "relerr_vs_lambda1": rel(cw, cw1)})
low = [{"Fn": f, "Rw_N": rw_theta_integral(H, U_of(f), n_theta=300)} for f in (0.10, 0.12, 0.15, 0.20, 0.25)]
res["P_invariants"] = {
    "B2_scaling": {"U_ms": Ub, "Fn": 0.25, "rows": brows,
                   "worst_relerr": max(abs(r["relerr_vs_B2"]) for r in brows), "tolerance": 1e-9},
    "rho_scaling": {"relerr": rho_err, "tolerance": 1e-12},
    "froude_invariance_of_Cw": {"rows": frows,
                                "worst_relerr": max(abs(r["relerr_vs_lambda1"]) for r in frows),
                                "tolerance": 1e-8,
                                "statement": "Cw is dimensionless in (Fn, geometry ratios) -> exactly scale invariant"},
    "low_fn": low,
    "verdict": "PASS" if max(abs(r["relerr_vs_B2"]) for r in brows) < 1e-9
    and abs(rho_err) < 1e-12 and max(abs(r["relerr_vs_lambda1"]) for r in frows) < 1e-8 else "FAIL",
}

# ---------------------------------------------------------------- platform
plat = {"endpoint": f"{PLATFORM}/resistance/predict"}
try:
    import requests

    payload = {"Lwl": L, "Lpp": L, "B": B, "T": T, "displacement": H.volume * RHO / 1000.0,
               "Cb": 4.0 / 9.0, "Cm": 2.0 / 3.0, "Cwp": 2.0 / 3.0,
               "speeds": [round(U_of(f) / KNOT, 3) for f in FN_SWEEP]}
    r = requests.post(f"{PLATFORM}/resistance/predict", json=payload, timeout=10)
    plat["status"] = r.status_code
    rows = []
    if r.status_code == 200:
        for row in r.json():
            Uu = row["speed"] * KNOT
            rw_api = row["wave_resistance"] * 1e3
            rw_m = rw_theta_integral(H, Uu, n_theta=300)
            cwa = rw_api / (0.5 * RHO * Uu**2 * S_fine)
            cwm = rw_m / (0.5 * RHO * Uu**2 * S_fine)
            rows.append({"speed_kn": row["speed"], "Fn": Uu / math.sqrt(G * L), "U_ms": Uu,
                         "Rw_api_N": rw_api, "Rw_michell_N": rw_m,
                         "Rw_ratio_api_over_michell": rw_api / rw_m,
                         "Cw_api": cwa, "Cw_michell": cwm,
                         "Ct_api": row["total_resistance"] * 1e3 / (0.5 * RHO * Uu**2 * S_fine),
                         "Cw_relerr_api_vs_michell": rel(cwa, cwm)})
        plat["rows"] = rows
        worst = max(abs(x["Cw_relerr_api_vs_michell"]) for x in rows)
        x = np.array([q["Cw_michell"] for q in rows]); y = np.array([q["Cw_api"] for q in rows])
        k = float((x @ y) / (x @ x)); resid = y - k * x
        plat["shape_fit"] = {"scale_api_over_michell": k,
                             "R2_after_scale": 1 - float(resid @ resid) / float(((y - y.mean()) ** 2).sum()),
                             "max_abs_relerr_after_scale": float(np.max(np.abs(resid / (k * x))))}
        plat["worst_abs_Cw_relerr"] = worst
        plat["gate_15pct"] = "PASS" if worst < 0.15 else "FAIL"
        plat["gate_3pct"] = "PASS" if worst < 0.03 else "FAIL"
    else:
        plat["error"] = str(r.text)[:300]
except Exception as exc:
    plat["status"] = -1
    plat["error"] = repr(exc)
res["S_platform"] = plat

# ---------------------------------------------------------------- figures
figs = []

fig, ax = plt.subplots(figsize=(7.2, 4.6))
f = [r["Fn"] for r in routes]
ax.plot(f, [1e3 * r["route_A_N"] / (0.5 * RHO * r["U_ms"] ** 2 * S_fine) for r in routes], "o-", label="route A (closed form + Gauss)", ms=4)
ax.plot(f, [1e3 * r["route_B_N"] / (0.5 * RHO * r["U_ms"] ** 2 * S_fine) for r in routes], "s--", label="route B (numerical surface quadrature)", ms=4)
ax.plot(f, [1e3 * r["route_C_N"] / (0.5 * RHO * r["U_ms"] ** 2 * S_fine) for r in routes], "^:", label="route C (mpmath, 20 digits)", ms=4)
if plat.get("rows"):
    ax.plot([q["Fn"] for q in plat["rows"]], [1e3 * q["Cw_api"] for q in plat["rows"]], "d-", color="crimson", ms=5, label="platform /resistance/predict")
ax.set_xlabel("Froude number Fn = U/sqrt(gL)")
ax.set_ylabel(r"$10^3 C_w$,  $C_w=R_w/(\frac{1}{2}\rho S U^2)$")
ax.set_title("Wigley hull (L=100 m, B=10 m, T=6.25 m, Cb=4/9): Michell reference vs platform")
ax.grid(alpha=0.3); ax.legend(fontsize=7.5); fig.tight_layout()
fig.savefig(FIG / "fig1_cw_vs_fn.png", dpi=150); plt.close(fig); figs.append("fig1_cw_vs_fn.png")

fig, ax = plt.subplots(1, 2, figsize=(9.6, 4.0))
ax[0].loglog([r["n_theta"] for r in lev], [max(abs(r["relerr_vs_1600"]), 1e-16) for r in lev], "o-")
ax[0].set_xlabel(r"theta Gauss nodes $n_\theta$"); ax[0].set_ylabel("rel. error of $R_w$ vs n=1600")
ax[0].set_title("route A theta convergence (Fn=0.30)"); ax[0].grid(alpha=0.3, which="both")
ax[1].loglog([r["n_panel_x"] for r in blev], [max(abs(r["relerr_vs_finest"]), 1e-12) for r in blev], "s-", color="darkorange")
ax[1].set_xlabel("surface panels $n_{panel,x}$"); ax[1].set_ylabel("rel. error of $R_w$")
ax[1].set_title("route B surface-quadrature convergence"); ax[1].grid(alpha=0.3, which="both")
fig.tight_layout(); fig.savefig(FIG / "fig2_convergence.png", dpi=150); plt.close(fig); figs.append("fig2_convergence.png")

fig, ax = plt.subplots(1, 3, figsize=(12.0, 3.6))
ax[0].plot([r["B_scale"] for r in brows], [r["Rw_over_scale2"] for r in brows], "o-")
ax[0].set_xlabel("B/B0 at fixed L, T, U"); ax[0].set_ylabel(r"$R_w/(B/B_0)^2$ [N]")
ax[0].set_title(r"exact: $R_w \propto B^2$"); ax[0].grid(alpha=0.3)
ax[1].plot([r["lambda"] for r in frows], [r["Cw"] for r in frows], "s-")
ax[1].set_xscale("log"); ax[1].set_xlabel(r"scale $\lambda$ at fixed Fn"); ax[1].set_ylabel(r"$C_w$")
ax[1].set_title(r"exact: $C_w$ Froude-invariant"); ax[1].grid(alpha=0.3, which="both")
ax[2].plot([1 / r["Fn"] ** 2 for r in low], [math.log(r["Rw_N"]) for r in low], "^-")
ax[2].set_xlabel(r"$1/Fn^2$"); ax[2].set_ylabel(r"$\ln R_w$ [N]")
ax[2].set_title("low-Fn decay of $R_w$"); ax[2].grid(alpha=0.3)
fig.tight_layout(); fig.savefig(FIG / "fig3_invariants.png", dpi=150); plt.close(fig); figs.append("fig3_invariants.png")

fig, ax = plt.subplots(1, 2, figsize=(10.4, 3.8))
xx = [(-0.5 + i / 400.0) * L for i in range(401)]
for zf, st, lb in ((0.0, "b-", "waterline z=0"), (0.5, "g--", "z=T/2"), (0.95, "r:", "z=0.95T")):
    ax[0].plot(xx, [H.half_beam(x, zf * T) for x in xx], st, label=lb)
ax[0].set_xlabel("x [m]"); ax[0].set_ylabel("half-breadth [m]"); ax[0].set_title("Wigley waterlines")
ax[0].grid(alpha=0.3); ax[0].legend(fontsize=8)
zz = [T * i / 200.0 for i in range(201)]
for xs, lb in ((0.0, "midship"), (0.25 * L, "x=L/4"), (0.45 * L, "x=0.45L")):
    ax[1].plot([H.half_beam(xs, z) for z in zz], zz, label=lb)
ax[1].invert_yaxis(); ax[1].set_xlabel("half-breadth [m]"); ax[1].set_ylabel("z below WL [m]")
ax[1].set_title("body plan (Cb=4/9, Cm=Cwp=2/3 exact)"); ax[1].grid(alpha=0.3); ax[1].legend(fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "fig4_wigley_geometry.png", dpi=150); plt.close(fig); figs.append("fig4_wigley_geometry.png")

res["figures"] = figs
res["verdicts"] = {k: res[k].get("verdict") for k in ("G_geometry", "I_inner_integrals", "R_routes", "D_discretisation", "P_invariants")}
res["all_pass"] = all(v == "PASS" for v in res["verdicts"].values())
(ART / "verification_verdicts.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")

# ---------------------------------------------------------------- tables
lines = ["<!-- generated by make_artifacts.py; do not edit by hand -->", ""]
lines += ["## Route consistency (Michell, Wigley hull)", "",
          "| Fn | route A [N] | route B [N] | route C [N] | A vs B | A vs C |", "|---|---|---|---|---|---|"]
for r in routes:
    lines.append("| {:.2f} | {:.6e} | {:.6e} | {:.6e} | {:+.2e} | {:+.2e} |".format(
        r["Fn"], r["route_A_N"], r["route_B_N"], r["route_C_N"], r["A_vs_B_relerr"], r["A_vs_C_relerr"]))
lines += ["", "## Discretisation (Fn=0.30)", "", "| n_theta | Rw [N] | rel. err vs 1600 |", "|---|---|---|"]
for r in lev:
    lines.append("| {} | {:.8e} | {:+.2e} |".format(r["n_theta"], r["Rw_N"], r["relerr_vs_1600"]))
lines += ["", "## Exact invariants", "", "| check | worst rel. error | tolerance |", "|---|---|---|"]
lines.append("| $R_w\\propto B^2$ | {:.2e} | 1e-09 |".format(abs(res["P_invariants"]["B2_scaling"]["worst_relerr"])))
lines.append("| $R_w\\propto\\rho$ | {:.2e} | 1e-12 |".format(abs(rho_err)))
lines.append("| $C_w$ Froude invariance | {:.2e} | 1e-08 |".format(abs(res["P_invariants"]["froude_invariance_of_Cw"]["worst_relerr"])))
lines.append("| inner integrals (closed vs numeric) | {:.2e} | 1e-06 |".format(worst_inner))
if plat.get("rows"):
    lines += ["", "## Platform `/resistance/predict` vs Michell", "",
              "| speed [kn] | Fn | Rw api [N] | Rw Michell [N] | api/Michell | Cw api | Cw Michell | Cw rel. err |",
              "|---|---|---|---|---|---|---|---|"]
    for q in plat["rows"]:
        lines.append("| {:.2f} | {:.3f} | {:.4e} | {:.4e} | {:.3f} | {:.4e} | {:.4e} | {:+.1%} |".format(
            q["speed_kn"], q["Fn"], q["Rw_api_N"], q["Rw_michell_N"], q["Rw_ratio_api_over_michell"],
            q["Cw_api"], q["Cw_michell"], q["Cw_relerr_api_vs_michell"]))
    sf = plat["shape_fit"]
    lines += ["", "Shape fit: scale k = {:.4f}, R2 = {:.4f}, max |resid| = {:.1%}.".format(
        sf["scale_api_over_michell"], sf["R2_after_scale"], sf["max_abs_relerr_after_scale"])]
(ART / "verification_tables.md").write_text("\n".join(lines), encoding="utf-8")

print(json.dumps({"verdicts": res["verdicts"], "all_pass": res["all_pass"],
                  "platform_status": plat.get("status"),
                  "platform_gate": plat.get("gate_15pct"),
                  "worst_A_vs_B": wAB, "worst_A_vs_C": wAC, "worst_inner": worst_inner}, indent=2))