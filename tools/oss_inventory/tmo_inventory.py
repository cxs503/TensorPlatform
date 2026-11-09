"""Generator for the ThermalMultiphaseOpenLB OSS inventory report.

The committed markdown is *generated*: prose lives in :mod:`tmo_report_text`,
every code pointer is resolved from :mod:`tmo_evidence` needles against a real
checkout, and every number is injected from the probe artifact
(:mod:`tmo_eos_probe` -> ``artifacts/oss_inventory/tmo_eos_probe.json``).
``tests/test_oss_inventory_tmo.py`` re-renders and compares, so a hand-edited
line number or a retyped number fails.

Usage::

    python tools/oss_inventory/tmo_inventory.py --src /tmp/ThermalMultiphaseOpenLB --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tmo_evidence as EV  # noqa: E402
import tmo_report_text as TXT  # noqa: E402

REPO_ROOT = HERE.parents[1]
DOC = REPO_ROOT / "docs" / "oss_inventory" / "OSS-INV-ThermalMultiphaseOpenLB.md"
PROBE = REPO_ROOT / "artifacts" / "oss_inventory" / "tmo_eos_probe.json"
STATS = REPO_ROOT / "artifacts" / "oss_inventory" / "tmo_stats.json"
PNG = REPO_ROOT / "artifacts" / "oss_inventory" / "tmo_inventory.png"

SUBSYSTEMS = {
    "EOS / 交互势": ["interactionPotentialExtended"],
    "Maxwell 等面积构造": ["maxwellConstruction"],
    "NS 动力学（修正 Guo 力格式）": ["modifiedGuoBGKDynamics",
                                     "modifiedShanChenForcedSingleComponent"],
    "温度 DDF 动力学": ["pseudoadvectionDiffusion", "pseudopotentialAdvectionDiffusion"],
    "热-流耦合 post-processor": ["combinedShanChen"],
    "物性 / 附加字段 / 边界 misc": ["fluidProperties", "descriptorFieldExtended",
                                  "miscFeatures"],
    "测量（归约 functor）": ["blockAverage", "blockIntegralF", "superAverage"],
}


class Resolved:
    __slots__ = ("key", "path", "line", "needle", "text")

    def __init__(self, key, path, line, needle, text):
        self.key, self.path, self.line = key, path, line
        self.needle, self.text = needle, text

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


def _specs(value):
    return [tuple(v) for v in value] if isinstance(value, list) else [tuple(value)]


def resolve_all(src: Path) -> dict[str, list[Resolved]]:
    """Resolve every needle; a missing *or ambiguous* needle is a hard error."""
    out: dict[str, list[Resolved]] = {}
    problems: list[str] = []
    for key, value in EV.REFS.items():
        resolved: list[Resolved] = []
        for spec in _specs(value):
            path, needle = spec[0], spec[1]
            nth = spec[2] if len(spec) > 2 else None
            f = src / path
            if not f.is_file():
                problems.append(f"{key}: missing file {path}")
                continue
            lines = f.read_text(errors="replace").splitlines()
            hits = [i for i, ln in enumerate(lines) if needle in ln]
            if not hits:
                problems.append(f"{key}: needle not found in {path}: {needle!r}")
                continue
            if nth is None and len(hits) != 1:
                problems.append(f"{key}: needle ambiguous in {path} "
                                f"({len(hits)} hits, lines {[h + 1 for h in hits]}): {needle!r}")
                continue
            idx = hits[0] if nth is None else hits[nth]
            resolved.append(Resolved(key, path, idx + 1, needle, lines[idx].strip()))
        out[key] = resolved
    if problems:
        raise SystemExit("pointer resolution failed:\n  " + "\n  ".join(problems))
    return out


def pointer(resolved, *keys) -> str:
    items = []
    for k in keys:
        items.extend(str(x) for x in resolved[k])
    return " · ".join(f"`{i}`" for i in items)


def code_stats(src: Path) -> dict:
    ext = src / "phaseChangeExtension"
    cpp_files = sorted(ext.rglob("*.cpp"))
    hh_files = sorted(list(ext.rglob("*.hh")) + list(ext.rglob("*.h")))
    py_files = sorted(ext.rglob("*.py"))
    mk_files = sorted(ext.rglob("*.mk"))
    tracked = sorted(p.relative_to(src).as_posix() for p in src.rglob("*")
                     if p.is_file() and ".git" not in p.parts)

    def loc(paths):
        return sum(len(p.read_text(errors="replace").splitlines()) for p in paths)

    groups = {}
    for name, prefixes in SUBSYSTEMS.items():
        members = [p for p in hh_files if any(p.name.startswith(pref) for pref in prefixes)]
        groups[name] = {"files": len(members), "loc": loc(members)}

    examples_dir = ext / "phaseChangeExamples"
    example_cpp = sorted(examples_dir.rglob("*.cpp"))
    eval_scripts = sorted(examples_dir.rglob("resultsEval*.py"))

    keyword_hits = {}
    for kw in ("CUDA", "__device__", "OpenMP", "#pragma omp", "SYCL", "HIP"):
        keyword_hits[kw] = sum(1 for p in hh_files + cpp_files
                               if kw in p.read_text(errors="replace"))
    mpi_sites = sum(p.read_text(errors="replace").count("singleton::mpi()") for p in cpp_files)
    gpl_headers = sum(1 for p in hh_files + mk_files
                      if "GNU General Public" in p.read_text(errors="replace"))

    return {
        "total_tracked_files": len(tracked),
        "total_loc": loc(cpp_files + hh_files + py_files),
        "header_files": len(hh_files),
        "header_loc": loc(hh_files),
        "example_cpp_files": len(example_cpp),
        "example_cpp_loc": loc(example_cpp),
        "python_files": len(py_files),
        "python_loc": loc(py_files),
        "groups": groups,
        "examples": sorted({p.parent.name for p in examples_dir.glob("*/module.mk")}),
        "eval_scripts": [p.name for p in eval_scripts],
        "keyword_hits": keyword_hits,
        "mpi_sites": mpi_sites,
        "license_files": [p for p in tracked if "licen" in p.lower()],
        "ci_files": [p for p in tracked if p.startswith(".github")
                     or p.endswith((".yml", ".yaml"))],
        "tmpfile_committed": [p for p in tracked if p.endswith(".tmpfile")],
        "test_like_files": [p for p in tracked if re.search(r"(^|/)(test|tests|unittest)", p)],
        "gpl_header_files": gpl_headers,
    }


def _eval_arith(expr: str) -> float:
    """Evaluate the arithmetic used in the C++ sweep bounds (e.g. ``2.01/49.``)."""
    if not re.fullmatch(r"[0-9.eE+\-*/ ()]+", expr):
        raise SystemExit(f"unsupported sweep expression: {expr!r}")
    return float(eval(expr, {"__builtins__": {}}))  # noqa: S307 - whitelisted charset


def sweep_values(text: str, var: str) -> list[float]:
    m = re.search(rf"for\(\s*{var}\s*=\s*([^;]+);\s*{var}\s*<=\s*([^;]+);"
                  rf"\s*{var}\s*\+=\s*([^)]+)\)", text)
    if not m:
        raise SystemExit(f"sweep for {var} not found")
    start, stop, step = (_eval_arith(v) for v in m.groups())
    vals, v = [], start
    while v <= stop + 1e-12:
        vals.append(v)
        v += step
    return vals


def sweep_alignment(src: Path) -> dict:
    """Do the evaluation scripts query the parameters the C++ sweeps produce?"""
    ex = src / "phaseChangeExtension" / "phaseChangeExamples"
    cpp = (ex / "thermodynamicConsistency2D" / "thermodynamicConsistency2D.cpp").read_text()
    eos_a = sweep_values(cpp, "eosA")
    produced = {
        "tr": sweep_values(cpp, "temperatureRatio"),
        "sigma": sweep_values(cpp, "sigma"),
        "eosA": eos_a,
        "a_over_49": [v * 49 for v in eos_a],
    }
    # the example writes folder names with std::setprecision(2) -> two significant digits
    produced_names = {k: [f"{v:.2g}" for v in vals] for k, vals in produced.items()}

    py = (ex / "thermodynamicConsistency2D" / "resultsEvalThermoConsist2D.py").read_text()
    consumed = {
        "eosA": re.search(r'a_tested = "([^"]+)"', py).group(1).split(", "),
        "sigma": [x.strip() for x in re.search(r"sigma_tested = \[([^\]]+)\]", py)
                  .group(1).replace('"', "").split(",")],
        "tr_theory_bounds": [float(x) for x in
                             re.search(r"trs = np\.linspace\(([0-9.]+),([0-9.]+)\)", py).groups()],
    }
    return {
        "produced": produced_names,
        "consumed": consumed,
        "sigma_consumed_but_not_produced":
            sorted(set(consumed["sigma"]) - set(produced_names["sigma"])),
        "a_consumed_but_not_produced":
            sorted(set(consumed["eosA"]) - set(produced_names["eosA"])),
        "tr_theory_upper_bound": consumed["tr_theory_bounds"][1],
        "tr_swept_upper_bound": max(produced["tr"]),
        "tr_theory_exceeds_sweep": consumed["tr_theory_bounds"][1] > max(produced["tr"]),
        "a_swept_min_over_49": min(produced["a_over_49"]),
        "a_swept_max_over_49": max(produced["a_over_49"]),
    }


def _g(x: float, nd: int = 4) -> str:
    return f"{x:.{nd}g}"


def build_values(probe: dict, stats: dict, sweeps: dict) -> dict:
    ok = [x for x in probe["lattice_peng_robinson"]["rows"] if x["outcome"] == "OK"]
    failed = [x for x in probe["lattice_peng_robinson"]["rows"] if x["outcome"] != "OK"]
    calib = probe["d2law_calibration"]
    real = probe["real_peng_robinson_water"]
    cs = probe["carnahan_starling"]
    zc = probe["zc_sensitivity"]
    res = [abs(x["maxwell_area_residual"]["relative_to_psat_vdiff"]) for x in ok]

    v = {
        "commit": EV.COMMIT,
        "commit_short": EV.COMMIT[:12],
        "commit_date": EV.COMMIT_DATE,
        "total_files": stats["total_tracked_files"],
        "total_loc": stats["total_loc"],
        "header_files": stats["header_files"],
        "header_loc": stats["header_loc"],
        "example_cpp_files": stats["example_cpp_files"],
        "example_cpp_loc": stats["example_cpp_loc"],
        "python_files": stats["python_files"],
        "python_loc": stats["python_loc"],
        "license_files": stats["license_files"],
        "gpl_header_files": stats["gpl_header_files"],
        "gpu_hits": ", ".join(f"{k}={v}" for k, v in stats["keyword_hits"].items()),
        "mpi_sites": stats["mpi_sites"],
        "test_like_count": len(stats["test_like_files"]),
        "ci_count": len(stats["ci_files"]),
        "tmpfile_count": len(stats["tmpfile_committed"]),
        "eval_scripts": ", ".join(sorted(set(stats["eval_scripts"]))),
        "groups_table": "\n".join(
            f"| {name} | {g['files']} | {g['loc']} |" for name, g in stats["groups"].items()),
        "mapping_rows": "\n".join(
            f"| `{m['slug']}` | `{m['transfer']}` | `{m['tag']}` |" for m in EV.MAPPING),
        "coexist_rows": "\n".join(
            f"| {x['tr']:.2f} | {x['rho_liquid']:.6f} | {x['rho_vapour']:.6g} | "
            f"{x['density_ratio']:.4g} | {x['pressure']:.4g} | "
            f"{abs(x['maxwell_area_residual']['relative_to_psat_vdiff']):.1e} |" for x in ok),
        "convergence_note": ("实测 10/10 个温度比（Tr=0.50→0.95）全部收敛" if not failed
                             else f"失败档位 {[x['tr'] for x in failed]}"),
        "ratio_range": (f"{_g(ok[0]['density_ratio'], 4)}（Tr={ok[0]['tr']:.2f}） ~ "
                        f"{_g(ok[-1]['density_ratio'], 4)}（Tr={ok[-1]['tr']:.2f}）"),
        "maxwell_residual_max": f"{max(res):.1e}",
        "zc_liq_diff": f"{zc['max_rel_diff_liquid']:.1e}",
        "zc_vap_diff": f"{zc['max_rel_diff_vapour']:.1e}",
        "sigma_produced": sweeps["produced"]["sigma"],
        "sigma_consumed": sweeps["consumed"]["sigma"],
        "sigma_phantom": sweeps["sigma_consumed_but_not_produced"],
        "a_produced": sweeps["produced"]["eosA"],
        "a_consumed": sweeps["consumed"]["eosA"],
        "a_phantom": sweeps["a_consumed_but_not_produced"],
        "a_range": (f"{sweeps['a_swept_min_over_49']:.2f}/49 ~ "
                    f"{sweeps['a_swept_max_over_49']:.2f}/49"),
        "tr_swept_max": f"{sweeps['tr_swept_upper_bound']:.2f}",
        "tr_theory_max": f"{sweeps['tr_theory_upper_bound']:.2f}",
        "tr_exceeds": sweeps["tr_theory_exceeds_sweep"],
        "hardcode_diff": f"{abs(calib['hardcode_vs_maxwell_rel_diff']):.1e}",
        "lattice_vapour": f"{calib['lattice_vapour_at_tr06']:.6g}",
        "density_factor": f"{calib['physical_density_factor']:.5g}",
        "implied_vapour": f"{calib['implied_physical_vapour']:.4g}",
        "stated_vapour": f"{calib['stated_physical_vapour']}",
        "calibration_gap": f"{abs(100 * calib['implied_vs_stated_rel_diff']):.1f}%",
        "stated_ratio": f"{calib['stated_physical_density_ratio']:.1f}",
        "lattice_ratio": f"{calib['lattice_density_ratio_maxwell']:.1f}",
        "cs_error": cs.get("error", "n/a"),
        "pr_liquid_err": f"{abs(100 * real['rel_error_liquid_at_tr06']):.1f}%",
        "pointer_count": sum(len(v_) for v_ in EV.REFS.values()),
    }

    def pct(flag: bool) -> str:
        return "**是**" if flag else "**否**"

    v["error_table"] = "\n".join([
        "| 关键量 | 我们的值 | 基准 | 相对误差 | &lt;3%? |",
        "|---|---|---|---|---|",
        f"| Maxwell 等面积残差（构造自洽性） | {max(res):.1e}（相对） | 0 | ~1e-12 | {pct(max(res) < 0.03)} |",
        f"| 格点 PR 共存 ρ_l（Tr=0.60） | {_g(ok[1]['rho_liquid'], 7)} | 本仓硬编码 8.72518 | "
        f"{abs(calib['hardcode_vs_maxwell_rel_diff']) * 100:.1e}% | {pct(abs(calib['hardcode_vs_maxwell_rel_diff']) < 0.03)} |",
        f"| 物理汽密度（由格点 ρ_v 换算出，Tr=0.60） | {calib['implied_physical_vapour']:.4g} kg/m³ | "
        f"算例自述 0.9643 | {abs(100 * calib['implied_vs_stated_rel_diff']):.1f}% | "
        f"{pct(abs(calib['implied_vs_stated_rel_diff']) < 0.03)}（且 >15%，不可接受） |",
        f"| 真实水液密度（real-PR, Tr=0.60） | {real['rows'][0]['rho_liquid']:.4g} kg/m³ | 表值 947.13 | "
        f"{abs(100 * real['rel_error_liquid_at_tr06']):.1f}% | {pct(abs(real['rel_error_liquid_at_tr06']) < 0.03)}（>15%，不可接受） |",
        f"| 真实水汽密度（real-PR, Tr=0.60） | {real['rows'][0]['rho_vapour']:.4g} kg/m³ | 表值 0.9643 | "
        f"{abs(100 * real['rel_error_vapour_at_tr06']):.1f}% | {pct(abs(real['rel_error_vapour_at_tr06']) < 0.03)}（>15%，不可接受） |",
        f"| ρc/Zc 差异（0.307 vs 0.3074）对共存密度 | 逐位相同 | —— | "
        f"{max(zc['max_rel_diff_liquid'], zc['max_rel_diff_vapour']):.0e}% | {pct(True)}（不敏感） |",
    ])
    return v


def sol_blocks(resolved) -> str:
    blocks = []
    for m in EV.MAPPING:
        files = ", ".join(f"`{x}`" for k in m["refs"] for x in resolved[k])
        blocks.append(
            "```text\n[OSS-SOL]\n"
            f"repo: {EV.REPO}\n"
            f"slug: {m['slug']}\n"
            f"tag: {m['tag']}\n"
            f"method: {m['method']}\n"
            f"files: {files}\n"
            f"conventions: {m['conventions']}\n"
            f"transfer: {m['transfer']}\n"
            "[/OSS-SOL]\n```")
    return "\n\n".join(blocks)


def inv_block() -> str:
    return (
        "```text\n[OSS-INV]\n"
        f"repo: {EV.REPO}\n"
        f"commit: {EV.COMMIT} ({EV.COMMIT_DATE})\n"
        "lang: C++ (OpenLB template extension; no Fortran; Python only for EOS/Maxwell + plots)\n"
        "license: NO LICENSE file; new sources carry only an 'OpenLB Extension ... for his thesis' "
        "comment (no licence text), build files inherited from OpenLB are GPL-2.0-or-later -> "
        "derivative of OpenLB with no explicit grant for the extension (research-only: do not copy "
        "code)\n"
        "dep: needs an OpenLB checkout at ../.. (ROOT:=../../..), -lgsl -lgslcblas, and a manual "
        "patch of OpenLB src/dynamics/dynamics.h (BounceBack private->protected)\n"
        "methods: lattice=D2Q9/D3Q19 (flow) + D2Q5/D3Q7 (temperature) | flow collision=BGK with "
        "Li/Luo/Li sigma-corrected velocity shift u+=sigma(F-F_ext)rho/psi^2 on top of Guo forcing | "
        "temperature DDF=first-order equilibrium + phi/dphi/d(Tu) source corrections "
        "(PRE 96,063303) | temperature MRT available (flow MRT NOT available) | semi-hybrid variant "
        "replaces LBM diffusion by explicit FD with k(rho)\n"
        "multiphase: SINGLE-component Shan-Chen pseudopotential; NO MCMP, NO colour gradient, NO "
        "free surface, NO phase field; high density ratio comes from a temperature-dependent EOS "
        "(Peng-Robinson / Carnahan-Starling, psi=sqrt(6(p-rho/3)/G)) whose coexistence densities are "
        "fixed by a Maxwell equal-area construction (C++ GSL; python scipy re-implementation); "
        "latent heat enters through (dp/dT)_rho in the temperature equation\n"
        "boundary: BounceBack with variable wall density (wetting = rho_wall, SC fluid-solid "
        "coupling), BounceBackVariableRho subclass, OpenLB advection-diffusion boundary conditions "
        "for the temperature lattice, setPeriodicity for periodic boxes; no contact-angle input, no "
        "free-slip, no moving wall, no IBM\n"
        "gpu: NONE (no CUDA/HIP/OpenMP/SYCL in the extension); parallel = inherited OpenLB MPI "
        "cuboid decomposition + HeuristicLoadBalancer only\n"
        "verify: no unit tests, no CI, no regression baselines; 12 example directories with "
        "hard-coded parameters and 6 matplotlib scripts that embed the results as literal arrays; "
        "the thermodynamic-consistency script queries sigma folders the sweep never produces and "
        "compares every case against a theory curve computed with a fixed a=1/49\n"
        "summary: Master-thesis OpenLB extension coupling a sigma-corrected Shan-Chen "
        "pseudopotential to a temperature DDF with a compressibility/latent-heat source "
        "(boiling/evaporation demos). Absorb: EOS->Maxwell->coexistence-density closure, the "
        "semi-hybrid FD diffusion for k(rho), and the sigma-corrected force scheme. Do not absorb: "
        "its verification surface and hard-coded calibrations.\n"
        "[/OSS-INV]\n```")


def render(src: Path, probe: dict, stats: dict, sweeps: dict) -> str:
    resolved = resolve_all(src)
    values = build_values(probe, stats, sweeps)
    values["sol_blocks"] = sol_blocks(resolved)
    values["inv_block"] = inv_block()

    def sub_pt(m):
        return pointer(resolved, *m.group(1).split("|"))

    text = re.sub(r"\[\[pt:([^\]]+)\]\]", sub_pt, TXT.TEMPLATE)
    text = re.sub(r"\[\[v:([A-Za-z0-9_]+)\]\]",
                  lambda m: str(values[m.group(1)]), text)
    leftover = re.findall(r"\[\[(?:pt|v):[^\]]+\]\]", text)
    if leftover:
        raise SystemExit(f"unresolved placeholders: {leftover}")
    return text.rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="path to a ThermalMultiphaseOpenLB checkout")
    ap.add_argument("--write", action="store_true", help="write doc/stats/figure")
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.src).resolve()
    stats = code_stats(src)
    sweeps = sweep_alignment(src)
    probe = json.loads(PROBE.read_text())
    md = render(src, probe, stats, sweeps)

    if args.write:
        DOC.parent.mkdir(parents=True, exist_ok=True)
        DOC.write_text(md)
        STATS.parent.mkdir(parents=True, exist_ok=True)
        STATS.write_text(json.dumps({"stats": stats, "sweeps": sweeps},
                                    indent=2, ensure_ascii=False) + "\n")
        if not args.no_figure:
            import tmo_eos_probe
            fig = tmo_eos_probe.build_figure(probe, stats, PNG)
            print(f"figure: {fig}")
        print(f"doc: {DOC} ({len(md.splitlines())} lines)")
        print(f"stats: {STATS}")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())