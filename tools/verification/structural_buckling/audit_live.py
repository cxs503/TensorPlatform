"""Live audit of the deployed ship-platform panel-buckling endpoints.

Probes :8001 /api/v1/plate-stability/{plate,stiffener,grillage} and compares the
served numbers with the closed-form references in ``ref_buckling.py``.

Outputs (relative to the repo root, override with --out):
    artifacts/verification/structural_buckling/audit.json
    artifacts/verification/structural_buckling/fig_k_vs_aspect.png
    artifacts/verification/structural_buckling/fig_error_vs_aspect.png

Run:  python3 tools/verification/structural_buckling/audit_live.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ref_buckling as ref  # noqa: E402

PORTS = (8001, 8002)
BASE = "/api/v1/plate-stability"
E, NU, SY = 206e9, 0.30, 315e6
B_PANEL, T_PANEL = 0.80, 0.012
ASPECTS = [1.0, 2.0, 3.0, 4.0, 6.0]


def _post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    last = None
    for port in PORTS:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{BASE}{path}",
                                     data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return {"ok": True, "port": port, "data": json.load(r)}
        except Exception as exc:  # noqa: BLE001 - audit must survive any failure
            last = f"{type(exc).__name__}: {exc}"
    return {"ok": False, "error": last}


def _rel(served: float, reference: float) -> float:
    return 100.0 * (served - reference) / reference


def audit_plate() -> list:
    rows = []
    for clamped in (False, True):
        bc = "clamped" if clamped else "simply_supported"
        for aspect in ASPECTS:
            a = aspect * B_PANEL
            got = _post("/plate", {
                "a": a, "b": B_PANEL, "t": T_PANEL, "E": E, "nu": NU, "sigma_y": SY,
                "edge_condition": bc, "axial_load_a": 1.0e6, "axial_load_b": 0.0,
                "shear_load": 0.0, "lateral_pressure": 0.0,
            })
            s_ref = ref.sigma_cr_plate(E, NU, B_PANEL, T_PANEL, aspect, clamped)
            t_ref = ref.tau_cr_plate(E, NU, B_PANEL, T_PANEL, aspect)
            k_ref = ref.k_uniaxial(aspect, clamped)
            row = {"bc": bc, "aspect": aspect, "k_ref": round(k_ref, 4),
                   "sigma_ref_MPa": round(s_ref, 3), "tau_ref_MPa": round(t_ref, 3),
                   "k_shear_ref": round(ref.k_shear(aspect), 4)}
            if got["ok"]:
                d = got["data"]
                row.update({
                    "k_served": d["buckling_coefficient"],
                    "sigma_served_MPa": d["critical_stress_a"],
                    "tau_served_MPa": d["critical_shear"],
                    "err_k_pct": round(_rel(d["buckling_coefficient"], k_ref), 3),
                    "err_sigma_pct": round(_rel(d["critical_stress_a"], s_ref), 3),
                    "err_tau_pct": round(_rel(d["critical_shear"], t_ref), 3),
                })
            else:
                row["error"] = got["error"]
            rows.append(row)
    return rows


def audit_stiffener() -> list:
    """The deployed model has no span input: flexural buckling uses plate_width as span.

    Sweeping the effective-plate width therefore moves the *buckling stress*, which is
    physically meaningless - quantified here against an Euler strut of fixed span.
    """
    geo = {"stiffener_type": "T-bar", "web_height": 0.30, "web_thickness": 0.010,
           "flange_width": 0.12, "flange_thickness": 0.015}
    rows = []
    span_ref = 4.0  # transverse frame spacing, m
    for s in (0.6, 0.8, 1.2, 2.4):
        got = _post("/stiffener", dict(geo, plate_width=s, plate_thickness=0.012,
                                       E=E, sigma_y=SY, axial_load=1.0e6))
        sec = ref.TeeSection(hw=geo["web_height"], tw=geo["web_thickness"],
                             bf=geo["flange_width"], tf=geo["flange_thickness"],
                             s=s, t=0.012).props()
        row = {"plate_width_m": s, "span_ref_m": span_ref,
               "sigma_euler_ref_MPa": round(ref.sigma_euler_column(E, sec["I"], sec["A"], span_ref), 3),
               "I_ref_cm4": round(sec["I"] * 1e8, 2), "A_ref_cm2": round(sec["A"] * 1e4, 2),
               "r_ref_cm": round(sec["r"] * 100, 2)}
        if got["ok"]:
            d = got["data"]
            row.update({"sigma_flexural_served_MPa": d["flexural_buckling"],
                        "I_served_cm4": d["moment_of_inertia"],
                        "A_served_cm2": d["area"],
                        "tau_served_MPa": d["torsional_buckling"],
                        "sigma_web_served_MPa": d["web_buckling"],
                        "err_flexural_vs_euler_pct": round(_rel(d["flexural_buckling"],
                                                                row["sigma_euler_ref_MPa"]), 3)})
        else:
            row["error"] = got["error"]
        rows.append(row)
    return rows


def audit_grillage() -> dict:
    body = {"L": 12.0, "B": 9.0, "plate_thickness": 0.012,
            "n_longitudinals": 10, "longitudinal_spacing": 0.9,
            "longitudinal_size": {"web_height": 0.25, "web_thickness": 0.01,
                                  "flange_width": 0.08, "flange_thickness": 0.012},
            "n_transverses": 4, "transverse_spacing": 3.0,
            "transverse_size": {"web_height": 0.40, "web_thickness": 0.012,
                                "flange_width": 0.10, "flange_thickness": 0.015},
            "E": E, "nu": NU, "sigma_y": SY, "axial_load": 50e6, "lateral_pressure": 0.0}
    got = _post("/grillage", body)
    # Reference: plate panel between longitudinals (simply supported, a=L=2*spacing of
    # transverse frames, b=0.9) and the stiffener column over the transverse spacing.
    a_panel = body["transverse_spacing"] * 2.0
    s_panel = ref.sigma_cr_plate(E, NU, body["longitudinal_spacing"], 0.012,
                                 a_panel / body["longitudinal_spacing"], False)
    ls = body["longitudinal_size"]
    sec = ref.TeeSection(hw=ls["web_height"], tw=ls["web_thickness"], bf=ls["flange_width"],
                         tf=ls["flange_thickness"], s=body["longitudinal_spacing"],
                         t=0.012).props()
    sig_col = ref.sigma_euler_column(E, sec["I"], sec["A"], body["transverse_spacing"])
    out = {"plate_ref_MPa": round(s_panel, 3), "column_ref_MPa": round(sig_col, 3),
           "ultimate_ref_faulkner_MPa": round(ref.faulkner_ultimate(SY, body["longitudinal_spacing"], 0.012, E), 3)}
    if got["ok"]:
        d = got["data"]
        out.update({"served": d,
                    "err_plate_pct": round(_rel(d["plate_buckling_stress"], s_panel), 3),
                    "err_column_pct": round(_rel(d["stiffener_buckling_stress"], sig_col), 3)})
    else:
        out["error"] = got["error"]
    return out


def figures(plate_rows: list, out_dir: str) -> list:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return [{"skipped": f"matplotlib unavailable: {exc}"}]
    made = []
    for clamped, color, label in ((False, "tab:blue", "simply supported (k_ref=4.00)"),
                                  (True, "tab:red", "clamped (k_ref=6.97, DNV-RP-C201)")):
        rows = [r for r in plate_rows if r["bc"] == ("clamped" if clamped else "simply_supported")
                and "k_served" in r]
        if not rows:
            continue
        xs = [r["aspect"] for r in rows]
        fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
        ax.plot(xs, [r["k_ref"] for r in rows], "--", color=color, lw=1.8,
                label=f"reference: {label}")
        ax.plot(xs, [r["k_served"] for r in rows], "o-", color=color, ms=6,
                label="served API :8001")
        ax.set_xlabel("panel aspect ratio a/b")
        ax.set_ylabel("buckling factor k [-]")
        ax.set_title(f"Uniaxial panel buckling factor - {'clamped' if clamped else 'simply supported'}")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        p = os.path.join(out_dir, f"fig_k_vs_aspect_{'clamped' if clamped else 'ss'}.png")
        fig.tight_layout()
        fig.savefig(p)
        plt.close(fig)
        made.append(p)
    # relative error of the served critical stress for both edge conditions
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    for clamped, color, lab in ((False, "tab:blue", "simply supported"),
                                (True, "tab:red", "clamped")):
        rows = [r for r in plate_rows if r["bc"] == ("clamped" if clamped else "simply_supported")
                and "err_sigma_pct" in r]
        if rows:
            ax.plot([r["aspect"] for r in rows], [r["err_sigma_pct"] for r in rows],
                    "o-", color=color, label=lab)
    ax.axhline(0, color="k", lw=0.8)
    ax.axhspan(-15, 15, color="green", alpha=0.08, label="acceptance band +/-15%")
    ax.set_xlabel("panel aspect ratio a/b")
    ax.set_ylabel("relative error of sigma_cr [%]  (served vs closed form)")
    ax.set_title("Panel buckling stress error of the deployed endpoint")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(out_dir, "fig_error_vs_aspect.png")
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/verification/structural_buckling")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    plate = audit_plate()
    stiff = audit_stiffener()
    grill = audit_grillage()
    fig_paths = figures(plate, args.out)

    worst = None
    for r in plate:
        if "err_sigma_pct" in r and (worst is None or abs(r["err_sigma_pct"]) > abs(worst["err_sigma_pct"])):
            worst = r
    summary = {
        "worst_plate_case": worst,
        "n_plate_cases": len(plate),
        "n_plate_beyond_15pct": sum(1 for r in plate if abs(r.get("err_sigma_pct", 0)) > 15),
        "n_plate_beyond_3pct": sum(1 for r in plate if abs(r.get("err_sigma_pct", 0)) > 3),
    }
    payload = {"summary": summary, "plate": plate, "stiffener": stiff,
               "grillage": grill, "figures": fig_paths}
    json_path = os.path.join(args.out, "audit.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("wrote", json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())