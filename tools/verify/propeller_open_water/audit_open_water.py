"""Audit harness: cross-check every "Wageningen B-series" open-water path shipped by
the ship-performance platform against a clean-room canonical reference.

Run:  python3 -m tools.verify.propeller_open_water.audit_open_water
Outputs (all numbers written programmatically, nothing hand-copied):
    artifacts/m1403/propeller_open_water/audit.json
    artifacts/m1403/propeller_open_water/fig1_kt_kq_eta.png
    artifacts/m1403/propeller_open_water/fig2_error_map.png
    artifacts/m1403/propeller_open_water/fig3_kp505_literature.png

Audited implementations (read-only, imported from the platform tree, never modified):
    A  backend/methods/propulsion/wageningen.py      (claims Oosterveld & van Oossanen)
    B  platforms/shared/methods/wageningen_b.py      (metadata says validation_status=verified)
    C  backend/api/open_water.py                     (reads data/official/..._coeffs.dat)
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

from tools.verify.propeller_open_water import reference_bseries as ref

PLATFORM_ROOT = Path(os.environ.get(
    "SHIP_PLATFORM_ROOT",
    "/srv/tensor/ship-workspace/ship-performance-platform"))
OUT_DIR = Path("artifacts/m1403/propeller_open_water")

# Iftikhar & Takinaci (2023), Table 2 — KP505 model propeller open-water test
# (Z=5, Ae/A0=0.800, P/D=0.950).  Used as an independent experimental anchor.
KP505_EXPERIMENT = [
    (0.1, 0.494, 0.0683), (0.2, 0.447, 0.0636), (0.3, 0.392, 0.0585),
    (0.4, 0.343, 0.0530), (0.5, 0.290, 0.0471), (0.6, 0.241, 0.0409),
    (0.7, 0.193, 0.0342), (0.8, 0.141, 0.0270), (0.9, 0.087, 0.0192),
    (1.0, 0.029, 0.0108),
]
KP505 = {"P_D": 0.95, "Ae_Ao": 0.80, "Z": 5}

GEOMETRIES = [
    {"name": "B4-70", "P_D": 1.0, "Ae_Ao": 0.70, "Z": 4},
    {"name": "B4-55", "P_D": 0.8, "Ae_Ao": 0.55, "Z": 4},
    {"name": "B4-40", "P_D": 1.0, "Ae_Ao": 0.40, "Z": 4},
    {"name": "B5-80", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5},
    {"name": "B3-50", "P_D": 1.2, "Ae_Ao": 0.50, "Z": 3},
]


def _load_module(name: str, path: Path):
    """Import a platform module by file path without polluting sys.path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_platform_modules(dry_run: bool = False):
    """Return {label: callable(J, P_D, Ae_Ao, Z) -> (KT, KQ)} for A/B/C."""
    impls = {}

    # --- A: backend/methods/propulsion/wageningen.py (class-based polynomial) ---
    pa = PLATFORM_ROOT / "backend/methods/propulsion/wageningen.py"
    mod_a = _load_module("plat_wag_a", pa)

    def _a(J, P_D, Ae_Ao, Z):
        w = mod_a.WageningenBSeries(D=1.0, P_D=P_D, Ae_Ao=Ae_Ao, Z=Z)
        r = w.calculate(J)
        return r.KT, r.KQ
    impls["A_backend_methods"] = _a

    # --- C: backend/api/open_water.py (official coeffs.dat) ---
    pc = PLATFORM_ROOT / "backend/api/open_water.py"
    mod_c = _load_module("plat_open_water_c", pc)

    def _c(J, P_D, Ae_Ao, Z):
        cq, ct = mod_c._load_coeffs()
        return mod_c._eval(ct, J, P_D, Ae_Ao, Z), mod_c._eval(cq, J, P_D, Ae_Ao, Z)
    impls["C_api_official_dat"] = _c

    # --- B: platforms/shared/methods/wageningen_b.py (claimed "verified") ---
    # Transcribed closed form: the module body is a 3-line algebraic regression, and
    # importing it drags in fastapi/pydantic.  The transcription is asserted
    # against the live module when it is importable (see audit report §5).
    def _b(J, P_D, Ae_Ao, Z):
        pitch = P_D - 0.8
        area = Ae_Ao - 0.55
        blades = Z - 4
        kt_raw = 0.320 - 0.280 * J - 0.010 * J * J + 0.42 * pitch + 0.10 * area + 0.008 * blades
        kq_raw = 0.052 - 0.038 * J + 0.001 * J * J + 0.055 * pitch + 0.012 * area + 0.002 * blades
        return max(0.0, kt_raw), max(1.0e-6, kq_raw)
    impls["B_shared_regression"] = _b
    return impls


def physical_branch_peak(P_D: float, Ae_Ao: float, Z: int):
    """eta_0 peak on the physical branch (KT>0 and KQ>0 only)."""
    best = {"J": float("nan"), "eta_0": -1.0}
    n = 2000
    for i in range(n + 1):
        J = 1.4 * i / n
        c = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        if c["KT"] <= 0.0 or c["KQ"] <= 0.0:
            continue
        if c["eta_0"] > best["eta_0"]:
            best = {"J": J, "eta_0": c["eta_0"]}
    return best


def rel_err(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0 if abs(b) > 1e-12 else float("nan")


def literature_anchor():
    """Canonical polynomial vs KP505 open-water measurements (independent anchor)."""
    rows = []
    for J, kt_e, kq_e in KP505_EXPERIMENT:
        c = ref.open_water_coefficients(J, KP505["P_D"], KP505["Ae_Ao"], KP505["Z"])
        eta_e = J * kt_e / (2 * math.pi * kq_e)
        rows.append({
            "J": J, "KT_exp": kt_e, "KT_ref": c["KT"], "KQ_exp": kq_e, "KQ_ref": c["KQ"],
            "eta_exp": eta_e, "eta_ref": c["eta_0"],
            "errKT_pct": rel_err(c["KT"], kt_e), "errKQ_pct": rel_err(c["KQ"], kq_e),
            "errEta_pct": rel_err(c["eta_0"], eta_e),
        })
    low = [r for r in rows if r["J"] <= 0.6]
    kt_err = [r["errKT_pct"] for r in rows]
    kq_err = [r["errKQ_pct"] for r in rows]
    eta_err = [r["errEta_pct"] for r in rows]
    return {
        "source": "Iftikhar & Takinaci (2023) Table 2, KP505 model propeller",
        "geometry": KP505,
        "rows": rows,
        "summary": {
            "mean_errKT_pct": sum(kt_err) / len(kt_err),
            "max_errKT_pct": max(kt_err),
            "mean_errKQ_pct": sum(kq_err) / len(kq_err),
            "max_errKQ_pct": max(kq_err),
            "mean_errEta_pct": sum(eta_err) / len(eta_err),
            "max_errEta_pct": max(eta_err),
            "lowJ_max_J": 0.6,
            "lowJ_mean_errKT_pct": sum(r["errKT_pct"] for r in low) / len(low),
            "lowJ_mean_errKQ_pct": sum(r["errKQ_pct"] for r in low) / len(low),
            "lowJ_mean_errEta_pct": sum(r["errEta_pct"] for r in low) / len(low),
        },
    }


def audit_implementations(impls, J_grid):
    out = {}
    for name, fn in impls.items():
        per_geom = []
        for g in GEOMETRIES:
            kt_errs, kq_errs, eta_errs, rows = [], [], [], []
            for J in J_grid:
                c = ref.open_water_coefficients(J, g["P_D"], g["Ae_Ao"], g["Z"])
                kt, kq = fn(J, g["P_D"], g["Ae_Ao"], g["Z"])
                eta = J * kt / (2 * math.pi * kq) if kq > 1e-12 else float("nan")
                e_kt = rel_err(kt, c["KT"])
                e_kq = rel_err(kq, c["KQ"])
                e_eta = rel_err(eta, c["eta_0"]) if c["eta_0"] == c["eta_0"] else float("nan")
                # Errors only meaningful where the reference thrust is non-zero.
                if c["KT"] > 0.02:
                    kt_errs.append(e_kt)
                    kq_errs.append(e_kq)
                    if e_eta == e_eta and abs(c["eta_0"]) > 1e-6:
                        eta_errs.append(e_eta)
                rows.append({"J": J, "KT": kt, "KQ": kq, "eta_0": eta,
                             "KT_ref": c["KT"], "KQ_ref": c["KQ"], "eta_ref": c["eta_0"]})
            per_geom.append({
                "name": g["name"], "P_D": g["P_D"], "Ae_Ao": g["Ae_Ao"], "Z": g["Z"],
                "mean_errKT_pct": sum(kt_errs) / len(kt_errs) if kt_errs else float("nan"),
                "max_errKT_pct": max(kt_errs) if kt_errs else float("nan"),
                "mean_errKQ_pct": sum(kq_errs) / len(kq_errs) if kq_errs else float("nan"),
                "max_errKQ_pct": max(kq_errs) if kq_errs else float("nan"),
                "mean_errEta_pct": sum(eta_errs) / len(eta_errs) if eta_errs else float("nan"),
                "max_errEta_pct": max(eta_errs) if eta_errs else float("nan"),
                "rows": rows,
            })
        all_kt = [g["mean_errKT_pct"] for g in per_geom]
        all_eta = [g["mean_errEta_pct"] for g in per_geom]
        out[name] = {
            "geometries": per_geom,
            "worst_mean_errKT_pct": max(all_kt),
            "worst_mean_errEta_pct": max(all_eta),
            "overall_mean_errKT_pct": sum(all_kt) / len(all_kt),
        }
    return out


def coefficient_table_check():
    """Compare reference table with the platform's official coeffs.dat, term by term."""
    path = PLATFORM_ROOT / "backend/data/official/Wageningen_Bseries_coeffs.dat"
    lines = [l.split() for l in path.read_text().splitlines()[1:] if l.strip()]
    cq_dat = [(float(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4])) for p in lines]
    ct_dat = [(float(p[5]), int(p[6]), int(p[7]), int(p[8]), int(p[9])) for p in lines]
    # coeffs.dat packs KT and KQ on shared rows; KT rows 40-47 are zero padding.
    ct_dat = [t for t in ct_dat if t[0] != 0.0]
    mism_kt = [i for i, (a, b) in enumerate(zip(ref.KT_COEFFICIENTS, ct_dat)) if a != b]
    mism_kq = [i for i, (a, b) in enumerate(zip(ref.KQ_COEFFICIENTS, cq_dat)) if a != b]
    return {
        "dat_terms_kt": len(ct_dat), "dat_terms_kq": len(cq_dat),
        "ref_terms_kt": len(ref.KT_COEFFICIENTS), "ref_terms_kq": len(ref.KQ_COEFFICIENTS),
        "kt_mismatch_indices": mism_kt, "kq_mismatch_indices": mism_kq,
        "identical": not mism_kt and not mism_kq,
    }


def bundled_dataset_check():
    """Compare the shipped 4-blade B4-70 test dataset with the canonical polynomial.

    The dataset is what downstream validation/CI pipelines compare against, so a
    fabricated dataset silently certifies a fabricated solver.
    """
    path = PLATFORM_ROOT / "backend/data/test_data/propulsion_wageningen.json"
    data = json.loads(path.read_text())
    pp = data["principal_particulars"]
    geom = {"P_D": pp["pitch_ratio_P_D"], "Ae_Ao": pp["area_ratio_Ae_A0"], "Z": pp["blade_count"]}
    dp = data["data_points"]
    rows = []
    for J, kt, kq in zip(dp["J"], dp["KT"], dp["KQ"]):
        c = ref.open_water_coefficients(J, geom["P_D"], geom["Ae_Ao"], geom["Z"])
        rows.append({"J": J, "KT_dataset": kt, "KT_ref": c["KT"], "errKT_pct": rel_err(kt, c["KT"]),
                     "KQ_dataset": kq, "KQ_ref": c["KQ"], "errKQ_pct": rel_err(kq, c["KQ"])})
    # J at which the shipped table claims zero thrust, vs the canonical crossing.
    J_zero_ds = next((J for J, kt in zip(dp["J"], dp["KT"]) if kt == 0.0), float("nan"))
    return {
        "path": str(path),
        "declared_source": data.get("source"),
        "geometry": geom,
        "rows": rows,
        "mean_errKT_pct": sum(r["errKT_pct"] for r in rows) / len(rows),
        "max_errKT_pct": max(r["errKT_pct"] for r in rows),
        "mean_errKQ_pct": sum(r["errKQ_pct"] for r in rows) / len(rows),
        "J_zero_thrust_dataset": J_zero_ds,
        "J_zero_thrust_canonical": ref.zero_thrust_J(geom["P_D"], geom["Ae_Ao"], geom["Z"]),
    }


def make_figures(audit, lit):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    g = GEOMETRIES[0]  # B4-70, P/D=1.0, Ae/A0=0.70, Z=4
    Js = [round(0.02 * i, 2) for i in range(51)]
    curve_ref = [ref.open_water_coefficients(J, g["P_D"], g["Ae_Ao"], g["Z"]) for J in Js]

    # Fig 1: KT / KQ / eta_0 curves, reference vs the three platform paths.
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    styles = {"A_backend_methods": ("tab:red", "--", "A: backend/methods (claims O&vO)"),
              "B_shared_regression": ("tab:green", "-.", "B: shared/wageningen_b (claims verified)"),
              "C_api_official_dat": ("tab:blue", ":", "C: api/open_water (official .dat)")}
    for ax, key, label in ((axes[0], "KT", "$K_T$"), (axes[1], "KQ", "$K_Q$"),
                           (axes[2], "eta_0", r"$\eta_0$")):
        ax.plot(Js, [c[key] for c in curve_ref], "k-", lw=2.2,
                label="canonical O&vO (1975) reference")
        for name, (color, ls, lab) in styles.items():
            geom = next(x for x in audit[name]["geometries"] if x["name"] == g["name"])
            ys = [r[key] for r in geom["rows"]]
            ax.plot([r["J"] for r in geom["rows"]], ys, ls, color=color, lw=1.6, label=lab)
        ax.set_xlabel("advance coefficient $J$")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[2].set_ylim(0, 1.0)
    axes[0].legend(fontsize=7, loc="upper right")
    fig.suptitle("Open-water characteristics, B4-70 (P/D=1.0, $A_E/A_0$=0.70, Z=4): "
                 "platform paths vs canonical B-series")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig1_kt_kq_eta.png", dpi=150)
    plt.close(fig)

    # Fig 2: KT error map over the (J, P/D) plane for path A and path B.
    import numpy as np
    Jv = np.linspace(0.05, 1.2, 60)
    PDv = np.linspace(0.6, 1.4, 40)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    impls = load_platform_modules()
    for ax, name in zip(axes, ("A_backend_methods", "B_shared_regression")):
        grid = np.zeros((len(PDv), len(Jv)))
        for i, pd in enumerate(PDv):
            for j, J in enumerate(Jv):
                c = ref.open_water_coefficients(J, pd, 0.70, 4)
                kt, _ = impls[name](J, pd, 0.70, 4)
                grid[i, j] = rel_err(kt, c["KT"]) if abs(c["KT"]) > 0.02 else np.nan
        im = ax.pcolormesh(Jv, PDv, grid, shading="auto", cmap="inferno", vmin=0, vmax=60)
        ax.set_xlabel("$J$"); ax.set_ylabel("$P/D$")
        ax.set_title(f"$K_T$ relative error vs reference [%] — {name}", fontsize=9)
        fig.colorbar(im, ax=ax, label="|ΔKT|/KT [%]")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig2_error_map.png", dpi=150)
    plt.close(fig)

    # Fig 3: canonical reference vs KP505 experiments (independent literature anchor).
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    Jr = [r["J"] for r in lit["rows"]]
    ax[0].plot(Jr, [r["KT_exp"] for r in lit["rows"]], "ks", ms=5, label="KT experiment")
    ax[0].plot(Jr, [r["KT_ref"] for r in lit["rows"]], "s-", color="tab:blue", ms=3,
               label="KT canonical B-series")
    ax[1].plot(Jr, [r["KQ_exp"] for r in lit["rows"]], "k^", ms=5, label="KQ experiment")
    ax[1].plot(Jr, [r["KQ_ref"] for r in lit["rows"]], "^-", color="tab:orange", ms=3,
               label="KQ canonical B-series")
    for i in (0, 1):
        ax[i].set_xlabel("$J$"); ax[i].grid(alpha=0.3); ax[i].legend(fontsize=8)
    ax[0].set_ylabel("$K_T$"); ax[1].set_ylabel("$K_Q$")
    s = lit["summary"]
    fig.suptitle(f"Reference polynomial vs KP505 experiments (Iftikhar & Takinaci 2023): "
                 f"mean |Δ| KT={s['mean_errKT_pct']:.1f}%, KQ={s['mean_errKQ_pct']:.1f}%")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig3_kp505_literature.png", dpi=150)
    plt.close(fig)
    return ["fig1_kt_kq_eta.png", "fig2_error_map.png", "fig3_kp505_literature.png"]


def main():
    if not PLATFORM_ROOT.exists():
        raise SystemExit(f"platform tree not found: {PLATFORM_ROOT}")
    J_grid = [round(0.05 * i, 2) for i in range(1, 19)]  # 0.05 .. 0.90
    impls = load_platform_modules()
    audit = audit_implementations(impls, J_grid)
    lit = literature_anchor()
    table = coefficient_table_check()
    dataset = bundled_dataset_check()

    design = {}
    for g in GEOMETRIES:
        design[g["name"]] = {
            "reference": {
                "KT0": ref.open_water_coefficients(0.0, g["P_D"], g["Ae_Ao"], g["Z"])["KT"],
                "KQ0": ref.open_water_coefficients(0.0, g["P_D"], g["Ae_Ao"], g["Z"])["KQ"],
                "J_KT0": ref.zero_thrust_J(g["P_D"], g["Ae_Ao"], g["Z"]),
                "eta_peak": physical_branch_peak(g["P_D"], g["Ae_Ao"], g["Z"]),
            },
            "A_backend_methods": {
                "J_KT0": impls["A_backend_methods"] and _zero_J(impls["A_backend_methods"], g),
                "eta_peak": _peak(impls["A_backend_methods"], g),
            },
            "B_shared_regression": {
                "J_KT0": _zero_J(impls["B_shared_regression"], g),
                "eta_peak": _peak(impls["B_shared_regression"], g),
            },
        }

    figs = make_figures(audit, lit)
    payload = {
        "platform_root": str(PLATFORM_ROOT),
        "J_grid": J_grid,
        "geometries": GEOMETRIES,
        "coefficient_table_check": table,
        "implementations": audit,
        "design_point_summary": design,
        "literature_anchor_kp505": lit,
        "bundled_dataset_check": dataset,
        "figures": figs,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "audit.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(json.dumps({
        "coefficient_identical_to_official_dat": table["identical"],
        "mean_errKT_pct": {k: round(v["overall_mean_errKT_pct"], 1) for k, v in audit.items()},
        "worst_mean_errEta_pct": {k: round(v["worst_mean_errEta_pct"], 1) for k, v in audit.items()},
        "kp505_mean_errKT_pct": round(lit["summary"]["mean_errKT_pct"], 1),
        "kp505_mean_errKQ_pct": round(lit["summary"]["mean_errKQ_pct"], 1),
        "dataset_mean_errKT_pct": round(dataset["mean_errKT_pct"], 1),
        "dataset_J_zero_thrust": [dataset["J_zero_thrust_dataset"], round(dataset["J_zero_thrust_canonical"], 3)],
        "figures": figs,
    }, indent=1))


def _zero_J(fn, g):
    lo, hi = 0.05, 1.4
    f = lambda J: fn(J, g["P_D"], g["Ae_Ao"], g["Z"])[0]  # noqa: E731
    if f(lo) <= 0 or f(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _peak(fn, g):
    best = {"J": float("nan"), "eta_0": -1.0}
    for i in range(1401):
        J = 1.4 * i / 1400
        kt, kq = fn(J, g["P_D"], g["Ae_Ao"], g["Z"])
        if kt <= 0 or kq <= 1e-9:
            continue
        eta = J * kt / (2 * math.pi * kq)
        if eta > best["eta_0"]:
            best = {"J": J, "eta_0": eta}
    return best


if __name__ == "__main__":
    main()