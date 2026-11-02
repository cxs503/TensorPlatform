"""Black-box verification audit of the ship-performance platform (:8001).

Checks that do not depend on the platform's own model:

  A. Geometry gate  - Wigley parabolic hull: exact Cb = 4/9, displacement = 4LBT/9,
                      wetted surface by spectral quadrature.
  B. Resistance     - Re/Fn identities (knot convention), ITTC-57 friction law
                      (implied wetted area must be speed independent), closure
                      Rf + Rw = Rt, Pe = Rt*U, Froude invariance of the *residual*
                      coefficient Cw (Ct is Re dependent by construction), and
                      Cw vs the Michell thin-ship integral for the same hull.
  C. Propeller      - Wageningen B-series: eta0 = J KT/(2 pi KQ) identity,
                      monotonicity, out-of-range J behaviour, comparison with the
                      published B4-40 open-water chart anchors (P/D = 1.0).
  D. Stability      - rectangular pontoon (Cb = Cm = 1): exact KB = T/2,
                      BM = B^2/(12T), KM, GM and wall-sided GZ(phi) up to the
                      deck-edge immersion angle (38.7 deg for B=20, T=8, D=16);
                      GZ = KN - KG sin(phi) identity from the returned curves.

Usage:  python3 audit_ship_platform.py            (writes artifacts/ + figures)
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import requests  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import references as ref  # noqa: E402
from michell_wigley import Wigley, michell_rw, wetted_surface  # noqa: E402

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts"
FIG = HERE / "figs"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)
RHO, NU = 1025.0, 1.19e-6


def post(path, body, timeout=20):
    try:
        r = requests.post(f"{BASE}/{path}", json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


def get(path, timeout=20):
    try:
        r = requests.get(f"{BASE}/{path}", timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


# ---------------------------------------------------------------------------
# A. Geometry gate
# ---------------------------------------------------------------------------
def audit_geometry(res):
    L, B, T = 100.0, 10.0, 6.25
    rec = {
        "inputs": {"L": L, "B": B, "T": T},
        "analytic_Cb": ref.wigley_cb(),
        "analytic_displacement_m3": ref.wigley_volume(L, B, T),
        "analytic_wetted_surface_m2": wetted_surface(Wigley(L=L, B=B, T=T)),
        "platform": {},
    }
    for label, path in (("verify", "geometry/verify/wigley"),
                        ("hydro_at", "geometry/hydro-at/wigley"),
                        ("hydro_curves", "geometry/hydro-curves/wigley")):
        code, data = get(path)
        rec["platform"][label] = {"status": code,
                                  "data": data if code == 200 else str(data)[:300]}
    res["A_geometry"] = rec


# ---------------------------------------------------------------------------
# B. Resistance
# ---------------------------------------------------------------------------
def audit_resistance(res):
    L, B, T, Cb = 100.0, 10.0, 6.25, ref.wigley_cb()
    disp = ref.wigley_volume(L, B, T) * RHO / 1000.0
    knots = [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
    common = {"Lwl": L, "Lpp": L, "B": B, "T": T, "displacement": disp, "Cb": Cb,
              "Cm": 0.6667, "Cwp": 0.6667}
    code, data = post("resistance/predict", {**common, "speeds": knots})
    rec = {"status": code, "raw": data, "rows": [], "speed_unit": "knot (verified)"}
    if code != 200 or not isinstance(data, list):
        res["B_resistance"] = rec
        return
    hull = Wigley(L=L, B=B, T=T, rho=RHO)
    for row in data:
        U = row["speed"] * ref.KNOT
        Re_an, Fn_an = ref.re_of_speed(U, L, NU), ref.fn_of_speed(U, L)
        Rf, Rw, Rt = (row["friction_resistance"] * 1e3, row["wave_resistance"] * 1e3,
                      row["total_resistance"] * 1e3)
        cf = ref.ittc57_cf(Re_an)
        s_imp = Rf / (0.5 * RHO * cf * U**2)
        rw_m = michell_rw(hull, U)
        rec["rows"].append({
            "speed_kn": row["speed"], "U_ms": U,
            "Re_platform": row["reynolds_number"], "Re_rel_err": ref.rel_error(row["reynolds_number"], Re_an),
            "Fn_platform": row["froude_number"], "Fn_rel_err": ref.rel_error(row["froude_number"], Fn_an),
            "Rt_kN": Rt / 1e3, "Rf_kN": Rf / 1e3, "Rw_kN": Rw / 1e3,
            "closure_rel_err": ref.rel_error(Rf + Rw, Rt),
            "Pe_platform_kW": row["effective_power"], "Pe_rel_err": ref.rel_error(row["effective_power"], Rt * U / 1e3),
            "Cf_ittc57": cf, "implied_S_m2": s_imp,
            "Cw_api": Rw / (0.5 * RHO * U**2 * s_imp),
            "Rw_michell_N": rw_m,
            "Cw_michell": rw_m / (0.5 * RHO * U**2 * s_imp),
            "Cw_ratio_api_over_michell": Rw / rw_m if rw_m > 0 else None,
        })
    sv = [r["implied_S_m2"] for r in rec["rows"]]
    rec["implied_S_mean"] = sum(sv) / len(sv)
    rec["implied_S_scatter_rel"] = (max(sv) - min(sv)) / rec["implied_S_mean"]
    rec["analytic_wetted_surface_m2"] = wetted_surface(hull)
    rec["implied_S_vs_analytic_rel_err"] = ref.rel_error(rec["implied_S_mean"],
                                                         rec["analytic_wetted_surface_m2"])
    # Froude invariance of the residual coefficient Cw (not Ct: Ct carries Cf(Re))
    # NOTE: m1792 - the half-scale speed used to be U/sqrt(0.5) (Fn=0.40, not 0.20),
    # which produced a spurious +166.6% "Froude scaling error". Fixed here.
    Fn_t = 0.20
    U = Fn_t * math.sqrt(ref.G * L)
    full = post("resistance/predict", {**common, "speeds": [U / ref.KNOT]})
    half = post("resistance/predict", {**{**common, "Lwl": L / 2, "Lpp": L / 2, "B": B / 2,
                                          "T": T / 2, "displacement": disp / 8},
                                      "speeds": [U * math.sqrt(0.5) / ref.KNOT]}  # Fn must stay equal: U_half = Fn*sqrt(g*L/2))
    try:
        f, h = full[1][0], half[1][0]
        Uf, Uh = f["speed"] * ref.KNOT, h["speed"] * ref.KNOT
        cw_f = f["wave_resistance"] * 1e3 / (0.5 * RHO * Uf**2 * wetted_surface(Wigley(L=L, B=B, T=T)))
        cw_h = h["wave_resistance"] * 1e3 / (0.5 * RHO * Uh**2 * wetted_surface(Wigley(L=L / 2, B=B / 2, T=T / 2)))
        rec["froude_scaling"] = {
            "Fn": Fn_t, "Cw_full": cw_f, "Cw_half": cw_h,
            "Cw_rel_err": ref.rel_error(cw_h, cw_f),
            "Ct_note": "Ct is Re dependent by construction; only Cw must be scale invariant",
        }
    except Exception as exc:  # pragma: no cover
        rec["froude_scaling"] = {"error": repr(exc), "full": full, "half": half}
    res["B_resistance"] = rec


# ---------------------------------------------------------------------------
# C. Propeller (Wageningen B-series)
# ---------------------------------------------------------------------------
def audit_propeller(res):
    rec = {"J_sweep": {}, "chart_anchors": {
        "source": "published B4-40 open-water diagram, P/D = 1.0 (Oosterveld & van Oossanen 1975 series)",
        "KT_at_J0": 0.40, "tenKQ_at_J0": 0.44, "J_zero_thrust": 1.00, "band_rel": 0.10}}
    for Z, aeo in ((4, 0.40), (4, 0.70), (5, 0.75)):
        sweep = []
        for J in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0, 3.0):
            code, data = post("propeller-design/wageningen-b",
                              {"J": J, "P_D": 1.0, "Z": Z, "Ae_Ao": aeo})
            item = {"J": J, "status": code}
            if code == 200 and isinstance(data, dict):
                kt, kq = data.get("KT"), data.get("KQ")
                eta = data.get("eta0")
                item.update({
                    "KT": kt, "KQ": kq, "eta0": eta,
                    "eta_identity": ref.eta_open_water(J, kt, kq) if (kt is not None and kq) else None,
                    "eta_rel_err": (ref.rel_error(eta, ref.eta_open_water(J, kt, kq))
                                    if (eta and kt is not None and kq) else None),
                    "method": data.get("method"),
                })
            else:
                item["data"] = str(data)[:200]
            sweep.append(item)
        rec["J_sweep"][f"Z{Z}_Ae{aeo}"] = sweep

    k0 = next(iter(rec["J_sweep"]))
    s = rec["J_sweep"][k0]
    rec["primary_series"] = k0
    ok = [r for r in s if r.get("KT") is not None]
    rec["monotonic_KT"] = all(ok[i]["KT"] >= ok[i + 1]["KT"] for i in range(len(ok) - 1))
    rec["monotonic_KQ"] = all(ok[i]["KQ"] >= ok[i + 1]["KQ"] for i in range(len(ok) - 1))
    rec["J_gt_1.5_still_returns_values"] = [r["J"] for r in ok if r["J"] > 1.5]
    rec["eta0_max_Z4_Ae0.40"] = max((r["eta0"] for r in ok if r.get("eta0")), default=None)
    z0 = next((r for r in ok if abs(r["J"]) < 1e-12), None)
    if z0:
        an = rec["chart_anchors"]
        rec["KT_J0_rel_err_vs_chart"] = ref.rel_error(z0["KT"], an["KT_at_J0"])
        rec["tenKQ_J0_rel_err_vs_chart"] = ref.rel_error(10 * z0["KQ"], an["tenKQ_at_J0"])
    # zero-thrust advance ratio of the platform series (linear interpolation)
    j0 = None
    for i in range(len(ok) - 1):
        if ok[i]["KT"] > 0 and ok[i + 1]["KT"] <= 0:
            j0 = ok[i]["J"] + (ok[i + 1]["J"] - ok[i]["J"]) * ok[i]["KT"] / (ok[i]["KT"] - ok[i + 1]["KT"])
            break
    rec["J_zero_thrust_platform"] = j0
    if j0 is not None:
        rec["J_zero_thrust_rel_err_vs_chart"] = ref.rel_error(j0, 1.00)
    res["C_propeller"] = rec


# ---------------------------------------------------------------------------
# D. Stability (rectangular pontoon, exact wall-sided reference)
# ---------------------------------------------------------------------------
def audit_stability(res):
    L, B, T, D = 100.0, 20.0, 8.0, 16.0
    Cb = Cm = 1.0
    disp = L * B * T * RHO / 1000.0
    rec = {"inputs": {"L": L, "B": B, "T": T, "D": D, "Cb": Cb, "Cm": Cm},
           "wall_sided_valid_to_deg": math.degrees(math.atan(T / (B / 2))),
           "km_table": {}}
    for KG in (3.0, 6.0, 9.0):
        h = ref.barge_hydrostatics(B, T, KG)
        code, data = post("stability/evaluate", {"L": L, "B": B, "T": T, "D": D,
                                                 "displacement": disp, "Cb": Cb,
                                                 "Cm": Cm, "KG": KG})
        entry = {"status": code, "analytic": h, "platform": data}
        if code == 200 and isinstance(data, dict):
            entry["GM_rel_err"] = ref.rel_error(data.get("GM"), h["GM"])
            entry["KM_platform"] = (data.get("GM") or 0) + KG
            entry["KM_analytic"] = h["KM"]
            entry["KM_rel_err"] = ref.rel_error(entry["KM_platform"], h["KM"])
            entry["GZ_max_platform"] = data.get("GZ_max")
            entry["vanishing_angle_platform"] = data.get("vanishing_angle")
        rec["km_table"][str(KG)] = entry

    KG = 6.0
    code, gz = post("stability/gz-curve", {"L": L, "B": B, "T": T, "D": D,
                                           "displacement": disp, "Cb": Cb,
                                           "Cm": Cm, "KG": KG})
    rec["gz_curve"] = {"status": code, "raw": gz}
    if code == 200 and isinstance(gz, dict) and gz.get("angles"):
        angles, gzv, knv = gz["angles"], gz.get("gz_values") or [], gz.get("kn_values") or []
        rows = []
        for i, a in enumerate(angles):
            gzi = gzv[i] if i < len(gzv) else None
            kni = knv[i] if i < len(knv) else None
            gref = ref.barge_gz(B, T, KG, a)
            rows.append({
                "phi_deg": a, "GZ_platform": gzi, "GZ_analytic": gref,
                "GZ_abs_err_m": (abs(gzi - gref) if gzi is not None else None),
                "GZ_rel_err": (ref.rel_error(gzi, gref) if (gzi is not None and abs(gref) > 1e-6) else None),
                "valid": a <= rec["wall_sided_valid_to_deg"] + 1e-9,
                "KN_platform": kni,
                "KN_identity_abs_err": (abs(gzi - (kni - KG * math.sin(math.radians(a))))
                                        if (gzi is not None and kni is not None) else None),
            })
        rec["gz_rows"] = rows
        rec["area_0_30_platform"] = gz.get("area_0_30")
        valid = [r for r in rows if r["valid"] and r["GZ_platform"] is not None]
        rec["area_0_30_analytic_trapezoid"] = ref.area_under_curve(
            [r["phi_deg"] for r in rows], [r["GZ_analytic"] for r in rows]) if len(rows) > 1 else None
        rec["gz_max_abs_err_valid_range_m"] = max(
            (r["GZ_abs_err_m"] for r in valid), default=None)
    res["D_stability"] = rec


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def make_figures(res):
    b = res["sections"].get("B_resistance", {})
    d = res["sections"].get("D_stability", {})
    c = res["sections"].get("C_propeller", {})
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.6))

    if b.get("rows"):
        fn = [r["Fn_platform"] for r in b["rows"]]
        ax[0].semilogy(fn, [r["Cw_api"] for r in b["rows"]], "o-", label="platform wave_resistance")
        ax[0].semilogy(fn, [r["Cw_michell"] for r in b["rows"]], "s--", label="Michell thin-ship (this work)")
        ax[0].set_xlabel("Froude number $Fn$")
        ax[0].set_ylabel("$C_w$ (-)")
        ax[0].set_title("Wigley hull: wave-resistance coefficient")
        ax[0].grid(True, which="both", alpha=0.3)
        ax[0].legend(fontsize=8)

    if d.get("gz_rows"):
        ph = [r["phi_deg"] for r in d["gz_rows"]]
        ax[1].plot(ph, [r["GZ_platform"] for r in d["gz_rows"]], "o-", label="platform GZ")
        ax[1].plot(ph, [r["GZ_analytic"] for r in d["gz_rows"]], "s--",
                   label="exact wall-sided pontoon")
        ax[1].axvline(d["wall_sided_valid_to_deg"], color="grey", lw=0.8, ls=":",
                      label="wall-sided validity limit")
        ax[1].set_xlabel("heel angle (deg)")
        ax[1].set_ylabel("GZ (m)")
        ax[1].set_title("Rectangular pontoon B=20, T=8, KG=6: GZ")
        ax[1].grid(alpha=0.3)
        ax[1].legend(fontsize=8)

    s = (c.get("J_sweep") or {}).get(next(iter(c.get("J_sweep") or {"x": 1}))) or []
    ok = [r for r in s if r.get("KT") is not None]
    if ok:
        ax[2].plot([r["J"] for r in ok], [r["KT"] for r in ok], "o-", label="$K_T$")
        ax[2].plot([r["J"] for r in ok], [10 * r["KQ"] for r in ok], "s-", label="$10K_Q$")
        ax[2].plot([r["J"] for r in ok], [r["eta0"] for r in ok], "^-", label=r"$\eta_0$")
        ax[2].axhline(0.40, color="grey", lw=0.8, ls=":", label="chart $K_T(J{=}0)\\approx$0.40")
        ax[2].axvline(1.5, color="red", lw=0.8, ls="--", label="polynomial validity limit J=1.5")
        ax[2].set_xlabel("advance coefficient $J$")
        ax[2].set_title("Wageningen B4-40, P/D=1.0")
        ax[2].grid(alpha=0.3)
        ax[2].legend(fontsize=8)

    fig.tight_layout()
    path = FIG / "verification_audit.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    res = {"api_base": BASE, "sections": {}}
    for fn, key in ((audit_geometry, "A_geometry"), (audit_resistance, "B_resistance"),
                    (audit_propeller, "C_propeller"), (audit_stability, "D_stability")):
        try:
            fn(res["sections"])
        except Exception as exc:  # pragma: no cover
            import traceback
            res["sections"][key] = {"error": repr(exc), "tb": traceback.format_exc()[-1200:]}
    fig_path = make_figures(res)
    res["figure"] = str(fig_path.relative_to(HERE))
    (OUT / "audit_results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))

    a, b = res["sections"].get("A_geometry", {}), res["sections"].get("B_resistance", {})
    print("A analytic: Cb=%.6f vol=%.3f S=%.1f" % (a["analytic_Cb"], a["analytic_displacement_m3"],
                                                   a["analytic_wetted_surface_m2"]))
    for k, v in (a.get("platform") or {}).items():
        print("   platform", k, v["status"], str(v.get("data"))[:120])
    if b.get("rows"):
        print("B implied S=%.1f m2 (scatter %.3f%%) vs analytic %.1f m2 -> %+.1f%%"
              % (b["implied_S_mean"], b["implied_S_scatter_rel"] * 100,
                 b["analytic_wetted_surface_m2"], b["implied_S_vs_analytic_rel_err"] * 100))
        for r in b["rows"]:
            print("  %5.1fkn Fn=%.4f Re_err=%+.3f%% Fn_err=%+.4f%% closure=%+.3f%% Pe_err=%+.4f%% "
                  "Cw_api=%.3e Cw_mich=%.3e ratio=%s"
                  % (r["speed_kn"], r["Fn_platform"], r["Re_rel_err"] * 100, r["Fn_rel_err"] * 100,
                     r["closure_rel_err"] * 100, r["Pe_rel_err"] * 100, r["Cw_api"], r["Cw_michell"],
                     ("%.1f" % r["Cw_ratio_api_over_michell"]) if r["Cw_ratio_api_over_michell"] else "n/a"))
        print("  Froude scaling:", {k: (round(v, 6) if isinstance(v, float) else v)
                                    for k, v in (b.get("froude_scaling") or {}).items() if k != "Ct_note"})
    c = res["sections"].get("C_propeller", {})
    if c.get("error"):
        print("C ERROR", c["error"])
    elif c.get("J_sweep"):
        print("C monotone KT=%s KQ=%s  out-of-range J still valued: %s"
              % (c["monotonic_KT"], c["monotonic_KQ"], c["J_gt_1.5_still_returns_values"]))
        print("  KT(J=0) err vs chart %+.1f%%, 10KQ(J=0) err %+.1f%%, J0_platform=%s (chart 1.00, %+.1f%%)"
              % (c.get("KT_J0_rel_err_vs_chart", float("nan")) * 100,
                 c.get("tenKQ_J0_rel_err_vs_chart", float("nan")) * 100,
                 round(c["J_zero_thrust_platform"], 4) if c.get("J_zero_thrust_platform") else None,
                 (c.get("J_zero_thrust_rel_err_vs_chart") or float("nan")) * 100))
        print("  eta0 max %s" % c.get("eta0_max_Z4_Ae0.40"))
        for r in c["J_sweep"][next(iter(c.get("J_sweep") or {"x": 1}))]:
            print("   J=%.2f KT=%s KQ=%s eta0=%s eta_id_err=%s" % (r["J"], r.get("KT"), r.get("KQ"),
                                                                  r.get("eta0"), r.get("eta_rel_err")))
    d = res["sections"].get("D_stability", {})
    if d.get("error"):
        print("D ERROR", d["error"], d.get("tb"))
    elif d.get("km_table"):
        for kg, e in d["km_table"].items():
            print("D KG=%s analytic KM=%.4f GM=%.4f | platform GM=%s KM=%s KM_err=%s GZmax=%s vanish=%s"
                  % (kg, e["analytic"]["KM"], e["analytic"]["GM"], (e.get("platform") or {}).get("GM"),
                     round(e.get("KM_platform", float("nan")), 4), 
                     ("%+.1f%%" % (e["KM_rel_err"] * 100)) if e.get("KM_rel_err") is not None else None,
                     (e.get("platform") or {}).get("GZ_max"), (e.get("platform") or {}).get("vanishing_angle")))
        if d.get("gz_rows"):
            for r in d["gz_rows"]:
                print("   phi=%5.1f GZ=%s ref=%.4f err=%s valid=%s KN_id_err=%s"
                      % (r["phi_deg"], r["GZ_platform"], r["GZ_analytic"],
                         r["GZ_abs_err_m"] and round(r["GZ_abs_err_m"], 4), r["valid"],
                         r["KN_identity_abs_err"] and round(r["KN_identity_abs_err"], 5)))
            print("   area_0_30 platform=%s analytic=%s unrelated check"
                  % (d.get("area_0_30_platform"), d.get("area_0_30_analytic_trapezoid")))
    print("figure:", res["figure"])


if __name__ == "__main__":
    main()