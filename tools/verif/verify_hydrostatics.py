#!/usr/bin/env python3
"""Verification of hydrostatics integration against closed-form benchmarks.

Mission m1821 (verifier self-start).  Lightweight: pure-Python numerics,
no CFD, no heavy compute.

What it verifies
----------------
1. **Box barge hydrostatics** (closed-form):
   - Volume = L * B * T
   - Cb = 1.0
   - KB = T / 2
   - BM_t = B^2 / (12 T)
   - Waterplane area = L * B
   - Wetted surface = L*B + 2*T*(L+B)
   All compared against the platform's ``compute_hydrostatics`` with a
   box-barge offset table.

2. **Wigley hull hydrostatics** (semi-analytical):
   The Wigley parabolic hull has known analytical integrals:
   - Volume = (2/3) * L * B * T  =>  Cb = 2/3
   - Waterplane area = (2/3) * L * B  =>  Cw = 2/3
   - KB = 3T/8  (for the standard Wigley form)
   These are compared against the numerical integration at increasing
   resolution to verify convergence.

3. **GZ cross-validation**: wall-sided closed form vs independent numeric
   box-barge GZ solver (polygon clipping + equal-displacement bisection).

4. **Wetted-surface estimator audit**: legacy empirical formula vs
   Holtrop-Mennen (1982) vs exact closed-form for canonical shapes.

Usage
-----
    python3 tools/verif/verify_hydrostatics.py [--json PATH] [--figdir DIR]

Outputs (default): artifacts/verif/hydrostatics_verification.json
                  artifacts/verif/hydrostatics_*.png
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

RHO = 1025.0  # sea water


def _load_hydrostatics_module():
    """Import backend/services/ship_hydrostatics.py by path."""
    spec = importlib.util.spec_from_file_location(
        "tp_hydro_verif", BACKEND / "services" / "ship_hydrostatics.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_hydro_verif"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_hull_geometry_module():
    """Import backend/services/hull_geometry.py by path."""
    spec = importlib.util.spec_from_file_location(
        "tp_hull_geo", BACKEND / "services" / "hull_geometry.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_hull_geo"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Box barge hydrostatics (closed-form benchmark)
# ---------------------------------------------------------------------------

def check_box_barge(hydro) -> dict:
    """Verify hydrostatics integration against a box-barge offset table.

    A box barge has analytically known hydrostatic quantities:
      Volume = L * B * T,  Cb = 1.0,  KB = T/2,  BM_t = B^2/(12T),
      Waterplane = L*B,  Cw = 1.0,  LCB = L/2,  LCF = L/2.
    """
    L, B, T = 100.0, 20.0, 8.0
    n_stations = 41
    n_wl = 21

    # Build box-barge offset table: half-breadth = B/2 for all stations at WL<=T
    stations = [i * L / (n_stations - 1) for i in range(n_stations)]
    waterlines = [i * T / (n_wl - 1) for i in range(n_wl)]
    half_breadths = []
    for i in range(n_stations):
        row = []
        for j in range(n_wl):
            z = waterlines[j]
            if z <= T + 1e-9:
                row.append(B / 2.0)
            else:
                row.append(0.0)
        half_breadths.append(row)

    offsets = hydro.HullOffsets(
        length=L,
        stations=stations,
        waterlines=waterlines,
        half_breadths=half_breadths,
    )
    resp = hydro.compute_hydrostatics(offsets, rho=RHO)

    # Closed-form references
    vol_ref = L * B * T
    cb_ref = 1.0
    kb_ref = T / 2.0
    bm_ref = B ** 2 / (12.0 * T)
    wp_ref = L * B
    cw_ref = 1.0
    ws_ref = L * B + 2.0 * T * (L + B)

    def rel_err(computed, ref):
        if ref == 0:
            return abs(computed) * 100 if computed != 0 else 0.0
        return abs(computed - ref) / abs(ref) * 100.0

    checks = {
        "volume_m3": {"computed": resp.volume_m3, "reference": vol_ref,
                       "error_pct": round(rel_err(resp.volume_m3, vol_ref), 4)},
        "cb": {"computed": resp.cb, "reference": cb_ref,
               "error_pct": round(rel_err(resp.cb, cb_ref), 4)},
        "kb_m": {"computed": resp.kb_m, "reference": kb_ref,
                 "error_pct": round(rel_err(resp.kb_m, kb_ref), 4)},
        "bm_t_m": {"computed": resp.bm_t_m, "reference": bm_ref,
                   "error_pct": round(rel_err(resp.bm_t_m, bm_ref), 4)},
        "waterplane_area_m2": {"computed": resp.waterplane_area_m2, "reference": wp_ref,
                                "error_pct": round(rel_err(resp.waterplane_area_m2, wp_ref), 4)},
        "cw": {"computed": resp.cw, "reference": cw_ref,
               "error_pct": round(rel_err(resp.cw, cw_ref), 4)},
        "wetted_surface_m2": {"computed": resp.wetted_surface_m2, "reference": ws_ref,
                               "error_pct": round(rel_err(resp.wetted_surface_m2, ws_ref), 4)},
    }

    max_err = max(v["error_pct"] for v in checks.values())
    return {
        "geometry": "box_barge",
        "L": L, "B": B, "T": T,
        "n_stations": n_stations, "n_waterlines": n_wl,
        "checks": checks,
        "max_error_pct": round(max_err, 4),
        "within_3pct": max_err < 3.0,
        "within_1pct": max_err < 1.0,
    }


# ---------------------------------------------------------------------------
# 2. Wigley hull hydrostatics (convergence study)
# ---------------------------------------------------------------------------

def check_wigley_convergence(hydro) -> dict:
    """Verify Wigley hull hydrostatics converge to analytical values.

    Standard Wigley hull: y(x,z) = (B/2) * [1 - (2x/L)^2] * [1 - (z/T)^2]
    Analytical integrals:
      Volume = (2/3) * L * B * T  =>  Cb = 2/3
      Waterplane area = (2/3) * L * B  =>  Cw = 2/3
      KB = 3T/8
    """
    L, B, T = 6.0, 0.6, 0.375
    resolutions = [11, 21, 41, 81, 161]
    rows = []

    for ns in resolutions:
        nw = max(11, ns // 2)
        offsets = hydro.wigley_offsets(length=L, beam=B, draft=T,
                                        n_stations=ns, n_waterlines=nw)
        resp = hydro.compute_hydrostatics(offsets, rho=RHO)

        cb_ref = 2.0 / 3.0
        cw_ref = 2.0 / 3.0
        kb_ref = 3.0 * T / 8.0

        def rel_err(c, r):
            return abs(c - r) / abs(r) * 100.0 if r != 0 else 0.0

        rows.append({
            "n_stations": ns,
            "n_waterlines": nw,
            "cb": round(resp.cb, 8),
            "cb_error_pct": round(rel_err(resp.cb, cb_ref), 4),
            "cw": round(resp.cw, 8),
            "cw_error_pct": round(rel_err(resp.cw, cw_ref), 4),
            "kb_m": round(resp.kb_m, 8),
            "kb_error_pct": round(rel_err(resp.kb_m, kb_ref), 4),
            "volume_m3": round(resp.volume_m3, 6),
            "volume_ref": round((2.0 / 3.0) * L * B * T, 6),
        })

    # Check convergence: errors should decrease with resolution
    cb_converges = rows[-1]["cb_error_pct"] < rows[0]["cb_error_pct"]
    cw_converges = rows[-1]["cw_error_pct"] < rows[0]["cw_error_pct"]

    return {
        "geometry": "wigley_parabolic",
        "L": L, "B": B, "T": T,
        "analytical": {"cb": 2.0 / 3.0, "cw": 2.0 / 3.0, "kb_m": 3.0 * T / 8.0},
        "convergence_study": rows,
        "cb_converges": cb_converges,
        "cw_converges": cw_converges,
        "final_cb_error_pct": rows[-1]["cb_error_pct"],
        "final_cw_error_pct": rows[-1]["cw_error_pct"],
        "final_kb_error_pct": rows[-1]["kb_error_pct"],
    }


# ---------------------------------------------------------------------------
# 3. GZ cross-validation: wall-sided vs numeric box barge
# ---------------------------------------------------------------------------

def check_gz_cross_validation(hull_geo) -> dict:
    """Cross-validate wall-sided GZ formula against independent numeric solver.

    For a box barge with B=20m, T=8m, depth=12m, KG=6m:
      GM = KB + BM - KG = T/2 + B^2/(12T) - KG = 4 + 33.33 - 6 = 31.33m
      BM = B^2/(12T) = 400/96 = 4.167m
      KB = T/2 = 4m
      GM = 4 + 4.167 - 6 = 2.167m
    """
    B, T, depth, KG = 20.0, 8.0, 12.0, 6.0
    kb = T / 2.0
    bm = B ** 2 / (12.0 * T)
    gm = kb + bm - KG

    heel_angles = [0, 2, 5, 8, 10, 12, 15, 18, 20]
    rows = []
    for phi in heel_angles:
        gz_ws = hull_geo.wall_sided_gz(gm, bm, math.radians(phi))
        gz_num = hull_geo.box_gz_numeric(B, T, depth, KG, phi, iters=100)
        err = abs(gz_ws - gz_num) / max(abs(gz_ws), 1e-10) * 100 if abs(gz_ws) > 1e-6 else abs(gz_ws - gz_num) * 100
        rows.append({
            "heel_deg": phi,
            "gz_wall_sided_m": round(gz_ws, 6),
            "gz_numeric_m": round(gz_num, 6),
            "error_pct": round(err, 4),
        })

    limit = math.degrees(hull_geo.wall_sided_limit_rad(B, T, depth))
    return {
        "geometry": "box_barge",
        "B": B, "T": T, "depth": depth, "KG": KG,
        "KB": round(kb, 4), "BM": round(bm, 4), "GM": round(gm, 4),
        "wall_sided_limit_deg": round(limit, 2),
        "cross_validation": rows,
        "max_gz_error_pct": round(max(r["error_pct"] for r in rows), 4),
        "within_1pct": all(r["error_pct"] < 1.0 for r in rows if abs(r["gz_wall_sided_m"]) > 1e-6),
    }


# ---------------------------------------------------------------------------
# 4. Wetted-surface estimator audit
# ---------------------------------------------------------------------------

def check_wetted_surface_audit(hull_geo) -> dict:
    """Audit wetted-surface estimators against closed-form benchmarks."""
    cases = [
        ("box", "方驳", 100.0, 20.0, 8.0, 1.0),
        ("cylinder", "卧式圆柱", 100.0, 20.0, 20.0, math.pi / 4),
        ("sphere", "球体", 20.0, 20.0, 20.0, math.pi / 6),
        ("capsule", "胶囊体", 80.0, 20.0, 20.0, None),
    ]

    rows = []
    for form, label, L, B, T, cb in cases:
        s_exact = hull_geo.exact_wetted_surface(form, L, B, T)
        s_legacy = hull_geo.legacy_wetted_surface(L, B, T, cb if cb else 0.5)
        s_holtrop = hull_geo.holtrop_wetted_surface(L, B, T, cb if cb else 0.5)

        err_legacy = (s_legacy / s_exact - 1.0) * 100.0 if s_exact > 0 else 0
        err_holtrop = (s_holtrop / s_exact - 1.0) * 100.0 if s_exact > 0 else 0

        rows.append({
            "form": form,
            "label": label,
            "L": L, "B": B, "T": T,
            "cb": round(cb, 6) if cb else "N/A",
            "s_exact_m2": round(s_exact, 4),
            "s_legacy_m2": round(s_legacy, 4),
            "s_holtrop_m2": round(s_holtrop, 4),
            "legacy_error_pct": round(err_legacy, 3),
            "holtrop_error_pct": round(err_holtrop, 3),
        })

    return {"cases": rows}


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def make_figures(result: dict, figdir: Path) -> list:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    figdir.mkdir(parents=True, exist_ok=True)
    figs = []

    # Fig 1: Wigley convergence
    wig = result.get("wigley_convergence", {})
    if wig.get("convergence_study"):
        rows = wig["convergence_study"]
        fig, ax = plt.subplots(figsize=(7, 4.2))
        xs = [r["n_stations"] for r in rows]
        ax.semilogy(xs, [r["cb_error_pct"] for r in rows], "o-", label="Cb error", color="#c0392b")
        ax.semilogy(xs, [r["cw_error_pct"] for r in rows], "s-", label="Cw error", color="#2980b9")
        ax.semilogy(xs, [r["kb_error_pct"] for r in rows], "^-", label="KB error", color="#27ae60")
        ax.axhline(3.0, color="gray", ls="--", lw=1, label="3% threshold")
        ax.axhline(1.0, color="gray", ls=":", lw=1, label="1% threshold")
        ax.set_xlabel("Number of stations")
        ax.set_ylabel("Relative error [%] (log scale)")
        ax.set_title("M1821: Wigley hull hydrostatics convergence to analytical solution")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        p = figdir / "m1821_wigley_convergence.png"
        fig.tight_layout()
        fig.savefig(p, dpi=130)
        plt.close(fig)
        figs.append(p)

    # Fig 2: GZ cross-validation
    gz = result.get("gz_cross_validation", {})
    if gz.get("cross_validation"):
        rows = gz["cross_validation"]
        fig, ax = plt.subplots(figsize=(7, 4.2))
        xs = [r["heel_deg"] for r in rows]
        ax.plot(xs, [r["gz_wall_sided_m"] for r in rows], "o-", label="Wall-sided (closed form)", color="#c0392b")
        ax.plot(xs, [r["gz_numeric_m"] for r in rows], "s--", label="Numeric (polygon clipping)", color="#2980b9")
        limit = gz.get("wall_sided_limit_deg", 25)
        ax.axvspan(0, limit, color="#27ae60", alpha=0.12, label=f"Valid range (phi <= {limit:.1f} deg)")
        ax.set_xlabel("Heel angle [deg]")
        ax.set_ylabel("GZ [m]")
        ax.set_title("M1821: GZ cross-validation — wall-sided vs numeric box-barge solver")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        p = figdir / "m1821_gz_cross_validation.png"
        fig.tight_layout()
        fig.savefig(p, dpi=130)
        plt.close(fig)
        figs.append(p)

    # Fig 3: Wetted-surface estimator bias
    ws = result.get("wetted_surface_audit", {})
    if ws.get("cases"):
        rows = ws["cases"]
        labels = [f"{r['label']} (Cb={r['cb']})" for r in rows]
        errs_legacy = [r["legacy_error_pct"] for r in rows]
        errs_holtrop = [r["holtrop_error_pct"] for r in rows]
        x = range(len(labels))
        fig, ax = plt.subplots(figsize=(8, 4.2))
        w = 0.35
        ax.bar([i - w / 2 for i in x], errs_legacy, w, label="Legacy empirical", color="#c0392b")
        ax.bar([i + w / 2 for i in x], errs_holtrop, w, label="Holtrop-Mennen 1982", color="#2980b9")
        ax.axhline(0, color="k", lw=1)
        ax.axhline(3.0, color="gray", ls="--", lw=1, label="±3% threshold")
        ax.axhline(-3.0, color="gray", ls="--", lw=1)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylabel("Wetted-surface error vs closed form [%]")
        ax.set_title("M1821: Wetted-surface estimator bias audit")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)
        p = figdir / "m1821_wetted_surface_audit.png"
        fig.tight_layout()
        fig.savefig(p, dpi=130)
        plt.close(fig)
        figs.append(p)

    return figs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None)
    parser.add_argument("--figdir", default=None)
    args = parser.parse_args()

    hydro = _load_hydrostatics_module()
    hull_geo = _load_hull_geometry_module()

    result = {
        "mission": "m1821",
        "title": "Hydrostatics integration verification against closed-form benchmarks",
        "box_barge": check_box_barge(hydro),
        "wigley_convergence": check_wigley_convergence(hydro),
        "gz_cross_validation": check_gz_cross_validation(hull_geo),
        "wetted_surface_audit": check_wetted_surface_audit(hull_geo),
    }

    outdir = REPO / "artifacts" / "verif"
    outdir.mkdir(parents=True, exist_ok=True)

    if args.json:
        outpath = Path(args.json)
    else:
        outpath = outdir / "hydrostatics_verification.json"
    outpath.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[OK] JSON -> {outpath}")

    figdir = Path(args.figdir) if args.figdir else outdir
    figs = make_figures(result, figdir)
    for f in figs:
        print(f"[OK] Figure -> {f}")

    # Summary
    bb = result["box_barge"]
    wg = result["wigley_convergence"]
    gz = result["gz_cross_validation"]
    print(f"\n=== Summary ===")
    print(f"Box barge max error: {bb['max_error_pct']:.4f}%  (within 3%: {bb['within_3pct']})")
    print(f"Wigley Cb error (finest): {wg['final_cb_error_pct']:.4f}%")
    print(f"Wigley Cw error (finest): {wg['final_cw_error_pct']:.4f}%")
    print(f"Wigley KB error (finest): {wg['final_kb_error_pct']:.4f}%")
    print(f"GZ cross-val max error: {gz['max_gz_error_pct']:.4f}%  (within 1%: {gz['within_1pct']})")


if __name__ == "__main__":
    main()
