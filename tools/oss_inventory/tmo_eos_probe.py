"""Numerical probe for the ThermalMultiphaseOpenLB OSS inventory (wave 2).

Runs the *analysed repository's own* Python equation-of-state module
(``phaseChangeExtension/EquationOfStatePython/eos.py``) out-of-tree and records
every number the inventory report quotes.  Nothing is copied into this
repository: the probe only imports the module from ``--src`` and writes a JSON
artifact, so every figure/table in the report is traceable to a command.

Usage::

    python tools/oss_inventory/tmo_eos_probe.py \
        --src /tmp/ThermalMultiphaseOpenLB \
        --out artifacts/oss_inventory/tmo_eos_probe.json \
        --fig artifacts/oss_inventory/tmo_inventory.png

The probe answers four questions that the report needs real numbers for:

1. which coexistence density pairs does the extension's Maxwell construction
   actually produce for its lattice Peng-Robinson EOS (i.e. what density ratio
   is reachable), and for which temperature ratios does it converge at all;
2. how accurate is the Maxwell construction itself (equal-area residual);
3. how far is the analytic real-fluid Peng-Robinson coexistence density from
   the fluid-property values hard coded in the example (IAPWS-ish values);
4. does the Carnahan-Starling branch of the same module run (defect check).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path

# Temperature ratios to probe.  The extension's examples use tr=0.6..0.85.
TEMP_RATIOS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

# Fluid properties hard coded in phaseChangeExamples/d2law2D/d2law2D.cpp.
D2LAW_PHYS_DENSITY_LIQUID = 947.13   # kg/m^3
D2LAW_PHYS_DENSITY_VAPOUR = 0.9643   # kg/m^3
D2LAW_LATTICE_LIQUID_HARDCODED = 8.72518  # used as `liquidDensityRatio` divisor


def load_eos(src: Path):
    eos_dir = src / "phaseChangeExtension" / "EquationOfStatePython"
    if not (eos_dir / "eos.py").is_file():
        raise SystemExit(f"eos.py not found under {eos_dir}")
    sys.path.insert(0, str(eos_dir))
    import eos  # noqa: PLC0415  (import must happen after sys.path fix)

    return eos


def maxwell_area_residual(pot, tr: float, rho_liq: float, rho_vap: float,
                          p_sat: float) -> float:
    """∫(P - p_sat) dV over [V_liq, V_vap]; zero = equal-area (Maxwell) construction."""
    from scipy.integrate import quad  # noqa: PLC0415

    pot.setTempRatio(tr)
    v_liq, v_vap = 1.0 / rho_liq, 1.0 / rho_vap
    integral, _err = quad(lambda v: pot.pressure(1.0 / v) - p_sat, v_liq, v_vap,
                          limit=400)
    scale = abs(p_sat) * (v_vap - v_liq)
    return {
        "absolute": float(integral),
        "relative_to_psat_vdiff": float(integral / scale) if scale else float("nan"),
    }


def probe_lattice_pr(eos, results: dict) -> None:
    pot = eos.LatticePengRobinson(G=-1, acentricity=0.3443, a=2. / 49.,
                                  b=2. / 21., R=1.0)
    rows = []
    for tr in TEMP_RATIOS:
        row = {"tr": tr}
        try:
            rho_liq, rho_vap, p_sat = pot.getEquilibriumDensities(tr)
        except Exception as exc:  # honest FAIL, recorded not hidden
            row.update(outcome="FAIL", error=f"{type(exc).__name__}: {exc}")
            rows.append(row)
            continue
        row.update(
            outcome="OK",
            rho_liquid=float(rho_liq),
            rho_vapour=float(rho_vap),
            pressure=float(p_sat),
            density_ratio=float(rho_liq / rho_vap),
        )
        try:
            row["maxwell_area_residual"] = maxwell_area_residual(
                pot, tr, float(rho_liq), float(rho_vap), float(p_sat))
        except Exception as exc:
            row["maxwell_area_residual"] = {"error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
    results["lattice_peng_robinson"] = {
        "eos": {"G": -1, "acentricity": 0.3443, "a": 2. / 49., "b": 2. / 21., "R": 1.0,
                "tc": float(pot.tc), "pc": float(pot.pc), "rhoc": float(pot.rhoc)},
        "rows": rows,
    }


def probe_zc_sensitivity(eos, results: dict) -> None:
    """C++ uses Zc=0.307, the Python module 0.3074 — does it change anything?"""
    pot = eos.LatticePengRobinson()
    out = []
    for zc in (0.3074, 0.307):
        pot.rhoc = pot.pc / zc / pot.R / pot.tc
        ref = pot.getEquilibriumDensities(0.7)
        out.append({"zc": zc, "rhoc": float(pot.rhoc),
                    "rho_liquid": float(ref[0]), "rho_vapour": float(ref[1])})
    results["zc_sensitivity"] = {
        "rows": out,
        "max_rel_diff_liquid": abs(out[0]["rho_liquid"] - out[1]["rho_liquid"]) / out[0]["rho_liquid"],
        "max_rel_diff_vapour": abs(out[0]["rho_vapour"] - out[1]["rho_vapour"]) / out[0]["rho_vapour"],
    }


def probe_real_pr(eos, results: dict) -> None:
    pot = eos.RealPengRobinson()
    rows = []
    for tr in (0.60, 0.70):
        rho_liq, rho_vap, p_sat = pot.getEquilibriumDensities(tr)
        rows.append({"tr": tr, "rho_liquid": float(rho_liq), "rho_vapour": float(rho_vap),
                     "pressure": float(p_sat)})
    results["real_peng_robinson_water"] = {
        "eos": {"Tc": 647.4, "Pc": 22.10e6, "R": 8314.0 / 18, "acentricity": 0.344},
        "rows": rows,
        "reference_used_by_example": {
            "tr": 0.60,
            "rho_liquid": D2LAW_PHYS_DENSITY_LIQUID,
            "rho_vapour": D2LAW_PHYS_DENSITY_VAPOUR,
        },
        "rel_error_liquid_at_tr06": (rows[0]["rho_liquid"] - D2LAW_PHYS_DENSITY_LIQUID)
        / D2LAW_PHYS_DENSITY_LIQUID,
        "rel_error_vapour_at_tr06": (rows[0]["rho_vapour"] - D2LAW_PHYS_DENSITY_VAPOUR)
        / D2LAW_PHYS_DENSITY_VAPOUR,
    }


def probe_lattice_calibration(eos, results: dict) -> None:
    """d2law2D divides the physical liquid density by a *hard coded* 8.72518."""
    rho_liq, rho_vap, _p = eos.LatticePengRobinson().getEquilibriumDensities(0.6)
    factor = D2LAW_PHYS_DENSITY_LIQUID / D2LAW_LATTICE_LIQUID_HARDCODED
    results["d2law_calibration"] = {
        "hardcoded_lattice_liquid": D2LAW_LATTICE_LIQUID_HARDCODED,
        "maxwell_lattice_liquid_at_tr06": float(rho_liq),
        "hardcode_vs_maxwell_rel_diff": (D2LAW_LATTICE_LIQUID_HARDCODED - float(rho_liq))
        / float(rho_liq),
        "physical_density_factor": float(factor),
        "lattice_vapour_at_tr06": float(rho_vap),
        "implied_physical_vapour": float(rho_vap * factor),
        "stated_physical_vapour": D2LAW_PHYS_DENSITY_VAPOUR,
        "implied_vs_stated_rel_diff": (float(rho_vap * factor) - D2LAW_PHYS_DENSITY_VAPOUR)
        / D2LAW_PHYS_DENSITY_VAPOUR,
        "lattice_density_ratio_maxwell": float(rho_liq / rho_vap),
        "stated_physical_density_ratio": D2LAW_PHYS_DENSITY_LIQUID / D2LAW_PHYS_DENSITY_VAPOUR,
    }


def probe_carnahan_starling(eos, results: dict) -> None:
    pot = eos.CarnahanStarlingInteractionPotential()
    entry = {"has_rhoc_attribute": hasattr(pot, "rhoc")}
    try:
        rho_liq, rho_vap, p_sat = pot.getEquilibriumDensities(0.8)
        entry.update(outcome="OK", rho_liquid=float(rho_liq), rho_vapour=float(rho_vap),
                     pressure=float(p_sat))
    except Exception as exc:
        entry.update(outcome="FAIL", error=f"{type(exc).__name__}: {exc}",
                     traceback=traceback.format_exc().strip().splitlines()[-3:])
    results["carnahan_starling"] = entry


def build_figure(probe: dict, stats: dict, out_png: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))

    # (a) code volume per subsystem
    ax = axes[0][0]
    groups = stats["groups"]
    names = list(groups)
    locs = [groups[n]["loc"] for n in names]
    ax.barh(names, locs, color="#3b6ea5")
    for i, v in enumerate(locs):
        ax.text(v, i, f" {v}", va="center", fontsize=8)
    ax.set_xlabel("lines of code")
    ax.set_title(f"(a) extension size: {stats['total_loc']} LOC in {stats['total_tracked_files']} files")

    # (b) coexistence densities and density ratio
    ax = axes[0][1]
    ok = [r for r in probe["lattice_peng_robinson"]["rows"] if r["outcome"] == "OK"]
    trs = [r["tr"] for r in ok]
    ax.semilogy(trs, [r["rho_liquid"] for r in ok], "o-", label=r"$\rho_{liq}$ (lattice)")
    ax.semilogy(trs, [r["rho_vapour"] for r in ok], "s-", label=r"$\rho_{vap}$ (lattice)")
    ax.set_xlabel("temperature ratio $T/T_c$")
    ax.set_ylabel("density [lattice units]")
    ax2 = ax.twinx()
    ax2.plot(trs, [r["density_ratio"] for r in ok], "^--", color="tab:green",
             label="density ratio")
    ax2.set_ylabel("density ratio $\\rho_{liq}/\\rho_{vap}$")
    ax2.set_yscale("log")
    ax.set_title("(b) Maxwell construction (lattice PR, a=2/49, b=2/21)")
    lines = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labels = ax.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax.legend(lines, labels, loc="lower left", fontsize=8)

    # (c) Maxwell equal-area residual
    ax = axes[1][0]
    res = [(r["tr"], r["maxwell_area_residual"]["relative_to_psat_vdiff"]) for r in ok
           if isinstance(r.get("maxwell_area_residual"), dict)
           and "relative_to_psat_vdiff" in r["maxwell_area_residual"]]
    if res:
        ax.semilogy([r[0] for r in res], [abs(r[1]) for r in res], "o-", color="tab:red")
    ax.set_xlabel("temperature ratio $T/T_c$")
    ax.set_ylabel("|$\\int (p-p_{sat})dV$| / ($p_{sat}\\Delta V$)")
    ax.set_title("(c) Maxwell construction self-consistency (equal-area residual)")

    # (d) real-fluid PR vs the values hard coded in the example
    ax = axes[1][1]
    real = probe["real_peng_robinson_water"]
    ref = real["reference_used_by_example"]
    row = real["rows"][0]
    labels = ["$\\rho_{liq}$", "$\\rho_{vap}$"]
    sim = [row["rho_liquid"], row["rho_vapour"]]
    ref_vals = [ref["rho_liquid"], ref["rho_vapour"]]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], sim, 0.4, label="real PR (repo module)", color="#3b6ea5")
    ax.bar([i + 0.2 for i in x], ref_vals, 0.4, label="value hard coded in d2law2D.cpp",
           color="#c05a3a")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("density [kg/m$^3$]  (Tr=0.6 water)")
    ax.set_title("(d) EOS vs. tabulated fluid property")
    for i, (s, r) in enumerate(zip(sim, ref_vals)):
        ax.text(i - 0.2, s, f"{s:.3g}", ha="center", va="bottom", fontsize=8)
        ax.text(i + 0.2, r, f"{r:.3g}", ha="center", va="bottom", fontsize=8)
    ax.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="path to a ThermalMultiphaseOpenLB checkout")
    ap.add_argument("--out", default="artifacts/oss_inventory/tmo_eos_probe.json")
    ap.add_argument("--fig", default="artifacts/oss_inventory/tmo_inventory.png")
    ap.add_argument("--stats", default=None,
                    help="stats JSON from tmo_inventory.py --stats (needed for the figure)")
    args = ap.parse_args(argv)

    src = Path(args.src).resolve()
    eos = load_eos(src)

    results: dict = {"source_root": str(src), "python": sys.version.split()[0]}
    probe_lattice_pr(eos, results)
    probe_zc_sensitivity(eos, results)
    probe_real_pr(eos, results)
    probe_lattice_calibration(eos, results)
    probe_carnahan_starling(eos, results)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")

    if args.stats:
        stats = json.loads(Path(args.stats).read_text())
        fig = build_figure(results, stats, Path(args.fig))
        print(f"figure: {fig}")

    ok = [r for r in results["lattice_peng_robinson"]["rows"] if r["outcome"] == "OK"]
    print(f"probe: {len(ok)}/{len(TEMP_RATIOS)} temperature ratios converged -> {out}")
    for r in ok:
        print(f"  Tr={r['tr']:.2f}  rho_l={r['rho_liquid']:.6g}  rho_v={r['rho_vapour']:.6g}"
              f"  ratio={r['density_ratio']:.4g}")
    print(f"  CS branch: {results['carnahan_starling']['outcome']}"
          f" {results['carnahan_starling'].get('error', '')}")
    print(f"  real PR Tr=0.6 rho_l={results['real_peng_robinson_water']['rows'][0]['rho_liquid']:.4g}"
          f" kg/m3 vs hard coded {D2LAW_PHYS_DENSITY_LIQUID}"
          f" ({100 * results['real_peng_robinson_water']['rel_error_liquid_at_tr06']:+.1f}%)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())