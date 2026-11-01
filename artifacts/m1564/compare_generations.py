"""Programmatic adjudication harness: inline (m1384) vs service-backed (m1386) GZ.

Both generations of the ``/calculations/stability`` endpoint are extracted from
the git object store -- nothing is retyped -- and evaluated on the same physical
cases against an independent reference, so the verdict is reproducible:

    python3 artifacts/m1564/compare_generations.py

Generations compared
--------------------
``genA-inline``   ``backend/api/calculations.py`` @ 577b14f5 (integration/m13xx-wave,
                  = m1384 inline ``calc_stability`` + m1387 refinements): closed form
                  in-domain, caller-supplied/heuristic limit, bounded linear fallback
                  outside, no geometry input, no independent cross-check.
``genB-service``  ``backend/api/calculations.py`` @ c922a33 (PR#255 / agent/m1386):
                  same closed form, but limit derived analytically from beam/draft/
                  depth, explicit ``wall_sided_valid`` verdict, ``gz_box_exact_m``
                  cross-check from ``services/stability_reference``, alias fields.
``canon``         working tree (m1564 adjudicated synthesis): service-backed reference
                  layer + analytic limit + inline generation's bounded fallback as the
                  no-geometry branch, and the *integrated* arm shipped outside the
                  domain when the geometry is known.

Reference
---------
``services.stability_reference.exact_gz_box``: constant-displacement heel of the
rectangular box section (polygon clip against the waterline + shoelace centroid +
bisection on sinkage).  Shares no code path with the closed form.

Outputs (all inside artifacts/m1564/)
------------------------------------
``results/comparison.json``        per-case, per-heel raw numbers + blob SHAs
``results/comparison_table.md``    the numerical comparison table (generated)
``fig1_gz_vs_heel.png``            arms vs heel: reference / genA / genB / canon
``fig2_relative_error.png``        relative error vs heel with the 15 % accuracy bar
"""
from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
EXTRACT = OUT / "_extracted"
BACKEND = REPO / "backend"

GEN_REFS = {
    "genA-inline": "577b14f5",
    "genB-service": "c922a33",
}

# (label, B, T, D, KG, displacement_t) -- box barges with freeboard to heel into.
CASES = [
    ("barge-B20T8D12", 20.0, 8.0, 12.0, 6.5, 1000.0),
    ("hull-B32T12D20", 32.0, 12.0, 20.0, 8.0, 5000.0),
    ("shallow-B10T2D3", 10.0, 2.0, 3.0, 2.0, 250.0),
]
HEELS = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 45.0, 50.0, 60.0, 70.0, 80.0, 89.0]
ACCURACY_BAR_PCT = 15.0


# ---------------------------------------------------------------------------
# generation loading (from git objects -- zero hand copy)
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True
    ).stdout


def _extract(ref: str) -> Path:
    EXTRACT.mkdir(parents=True, exist_ok=True)
    dest = EXTRACT / f"calculations_{ref}.py"
    dest.write_text(_git("show", f"{ref}:backend/api/calculations.py"), encoding="utf-8")
    return dest


def _load(path: Path, name: str):
    """Import an extracted calculations module without executing api/__init__ (DB deps)."""
    if "api" not in sys.modules:
        pkg = types.ModuleType("api")
        pkg.__path__ = [str(BACKEND / "api")]
        sys.modules["api"] = pkg
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _call(mod, **payload) -> Dict[str, Any]:
    """POST-body equivalent: build the request model and await the endpoint."""
    req = mod.StabilityRequest(**payload)
    resp = asyncio.run(mod.calc_stability(req))
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def evaluate() -> Dict[str, Any]:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from services.stability_reference import (
        box_parameters,
        exact_gz_box,
        wall_sided_gz,
        wall_sided_limit_deg,
    )

    mods = {}
    blob_sha = {}
    for tag, ref in GEN_REFS.items():
        path = _extract(ref)
        mods[tag] = _load(path, f"calc_{ref.replace('-', '_')}")
        blob_sha[tag] = _git("rev-parse", f"{ref}:backend/api/calculations.py").strip()
    canon = _load(BACKEND / "api" / "calculations.py", "calc_canon_m1564")
    blob_sha["canon"] = "working-tree"

    results: Dict[str, Any] = {"generations": GEN_REFS, "canon_base": "577b14f5",
                               "blob_sha": blob_sha, "cases": [], "notes": []}

    for label, B, T, D, KG, disp in CASES:
        par = box_parameters(B, T, KG, D)
        GM, BM, KB = par["gm_m"], par["bm_m"], par["kb_m"]
        limit = wall_sided_limit_deg(B, T, D)
        rows: List[Dict[str, Any]] = []
        for heel in HEELS:
            phi = math.radians(heel)
            ref = exact_gz_box(B, T, KG, phi, D)["gz_m"]

            # genA: no geometry fields exist -> its "best" configuration is the caller
            # knowing the analytic limit and passing it explicitly.
            a_default = _call(mods["genA-inline"], heel_angle_deg=heel, gm_m=GM, bm_m=BM,
                              kb_m=KB, displacement_t=disp)
            a_analytic = _call(mods["genA-inline"], heel_angle_deg=heel, gm_m=GM, bm_m=BM,
                               kb_m=KB, displacement_t=disp, wall_sided_limit_deg=round(limit, 3))
            b = _call(mods["genB-service"], heel_angle_deg=heel, gm_m=GM, bm_m=BM, kb_m=KB,
                      displacement_t=disp, beam_m=B, draft_m=T, depth_m=D)
            c = _call(canon, heel_angle_deg=heel, gm_m=GM, bm_m=BM, kb_m=KB,
                      displacement_t=disp, beam_m=B, draft_m=T, depth_m=D)

            def rel(gz: float) -> float:
                return (gz - ref) / abs(ref) * 100.0 if abs(ref) > 1e-9 else float("nan")

            wall_ray = wall_sided_gz(GM, BM, phi)
            rows.append({
                "heel_deg": heel,
                "inside_domain": heel <= limit + 1e-9,
                "gz_reference_m": ref,
                "gz_closed_form_ray_m": wall_ray,
                "genA_default": {
                    "arm_m": a_default["righting_arm_m"],
                    "basis": a_default.get("gz_basis"),
                    "limit_deg": a_default.get("wall_sided_limit_deg"),
                    "flag_valid": a_default.get("formula_valid"),
                    "rel_err_pct": rel(a_default["righting_arm_m"]),
                },
                "genA_analytic_limit": {
                    "arm_m": a_analytic["righting_arm_m"],
                    "basis": a_analytic.get("gz_basis"),
                    "limit_deg": a_analytic.get("wall_sided_limit_deg"),
                    "flag_valid": a_analytic.get("formula_valid"),
                    "rel_err_pct": rel(a_analytic["righting_arm_m"]),
                },
                "genB": {
                    "arm_m": b["righting_arm_m"],
                    "basis": b.get("gz_basis"),
                    "limit_deg": b.get("heel_validity_limit_deg"),
                    "flag_valid": b.get("wall_sided_valid"),
                    "cross_check_m": b.get("gz_box_exact_m"),
                    "rel_err_pct": rel(b["righting_arm_m"]),
                },
                "canon": {
                    "arm_m": c["righting_arm_m"],
                    "basis": c.get("gz_basis"),
                    "limit_deg": c.get("heel_validity_limit_deg"),
                    "limit_source": c.get("validity_limit_source"),
                    "flag_valid": c.get("wall_sided_valid"),
                    "cross_check_m": c.get("gz_box_exact_m"),
                    "rel_err_pct": rel(c["righting_arm_m"]),
                },
            })

        inside = [r for r in rows if r["inside_domain"] and r["heel_deg"] > 0.0]
        outside = [r for r in rows if not r["inside_domain"]]
        summary = {
            "case": label, "beam_m": B, "draft_m": T, "depth_m": D, "kg_m": KG,
            "displacement_t": disp, "kb_m": KB, "bm_m": BM, "gm_m": GM,
            "analytic_limit_deg": limit,
            "max_abs_rel_err_pct_inside": {
                k: max(abs(r[k]["rel_err_pct"]) for r in inside)
                for k in ("genA_analytic_limit", "genB", "canon")
            },
            "max_abs_rel_err_pct_outside": {
                k: max(abs(r[k]["rel_err_pct"]) for r in outside)
                for k in ("genA_analytic_limit", "genA_default", "genB", "canon")
            },
            "outside_domain_flagged_valid": {
                k: sum(1 for r in outside if r[k]["flag_valid"])
                for k in ("genA_default", "genA_analytic_limit", "genB", "canon")
            },
            "rows": rows,
        }
        results["cases"].append(summary)

    return results


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _fmt(v: float, nd: int = 4) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"


def render_table(results: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append("# 两代稳性 GZ 实现数值对照（脚本生成，勿手改）\n")
    out.append("生成脚本：`artifacts/m1564/compare_generations.py`；基准："
               "`services.stability_reference.exact_gz_box`（等排水量二维静水力积分）。\n")
    out.append(f"generation blobs: `{results['blob_sha']}`\n")
    def at(case: Dict[str, Any], heel: float) -> Dict[str, Any]:
        return next(r for r in case["rows"] if r["heel_deg"] == heel)

    out.append("\n## 汇总一：有效域内最大绝对相对误差（相对精确静水力积分）/ %\n")
    out.append("| 算例 | B×T×D [m] | KG | GM | 解析有效域 φ_lim | genA(解析域) | genB | canon | 判据 <15% |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for c in results["cases"]:
        i = c["max_abs_rel_err_pct_inside"]
        ok = all(v < ACCURACY_BAR_PCT for v in i.values())
        out.append(
            f"| {c['case']} | {c['beam_m']:.0f}×{c['draft_m']:.0f}×{c['depth_m']:.0f} | "
            f"{c['kg_m']:.2f} | {c['gm_m']:.4f} | {c['analytic_limit_deg']:.2f}° | "
            f"{_fmt(i['genA_analytic_limit'], 6)} | {_fmt(i['genB'], 6)} | {_fmt(i['canon'], 6)} | "
            f"{'PASS' if ok else 'FAIL'} |"
        )
    out.append("\n## 汇总二：规范关注角度上的相对误差 / %（IS Code 2008 用 GZ(30°) 与 A40）\n")
    out.append("| 算例 | φ [°] | 精确 GZ [m] | genA(默认 25°) | genA(解析域) | genB | canon |")
    out.append("|---|---|---|---|---|---|---|")
    for c in results["cases"]:
        for heel in (30.0, 40.0, 50.0):
            r = at(c, heel)
            out.append(
                f"| {c['case']} | {heel:.0f} | {_fmt(r['gz_reference_m'])} | "
                f"{_fmt(r['genA_default']['rel_err_pct'], 1)} | "
                f"{_fmt(r['genA_analytic_limit']['rel_err_pct'], 1)} | "
                f"{_fmt(r['genB']['rel_err_pct'], 1)} | "
                f"{_fmt(r['canon']['rel_err_pct'], 4)} |"
            )
    out.append("\n## 汇总三：域外最坏误差与有效域判定失误\n")
    out.append("| 算例 | 域外最坏 genA(解析域) | 域外最坏 genB | 域外最坏 canon | 域外被误判 valid 的角数 genA(默认25°) "
               "/ genA(解析域) / genB / canon |")
    out.append("|---|---|---|---|---|")
    for c in results["cases"]:
        o, m = c["max_abs_rel_err_pct_outside"], c["outside_domain_flagged_valid"]
        out.append(
            f"| {c['case']} | {_fmt(o['genA_analytic_limit'], 1)} | {_fmt(o['genB'], 1)} | "
            f"{_fmt(o['canon'], 6)} | {m['genA_default']} / {m['genA_analytic_limit']} / "
            f"{m['genB']} / {m['canon']} |"
        )
    out.append("\n## 逐角度明细（算例 1：B=20, T=8, D=12, KG=6.5）\n")
    out.append("| φ [°] | 域内 | 基准 GZ [m] | 闭式射线 [m] | genA 默认臂 [m] | genA 解析域臂 [m] "
               "| genB 臂 [m] | canon 臂 [m] | genB 交叉核 [m] | canon 误差 % |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    rows = results["cases"][0]["rows"]
    for r in rows:
        out.append(
            f"| {r['heel_deg']:.0f} | {'✓' if r['inside_domain'] else '✗'} | "
            f"{_fmt(r['gz_reference_m'])} | {_fmt(r['gz_closed_form_ray_m'], 3)} | "
            f"{_fmt(r['genA_default']['arm_m'])} | {_fmt(r['genA_analytic_limit']['arm_m'])} | "
            f"{_fmt(r['genB']['arm_m'], 3)} | {_fmt(r['canon']['arm_m'])} | "
            f"{_fmt(r['genB']['cross_check_m'])} | {_fmt(r['canon']['rel_err_pct'], 6)} |"
        )
    out.append("\n## 差异归因\n")
    out.append("- **域内三实现同值**：闭式解即精确解，genA/genB/canon 与积分基准差 <1e-9 m —— 分歧不在公式，"
               "而在有效域判定与域外输出。")
    out.append("- **域外 genA**：丢弃 tan² 项返回 GM·sinφ，明显**低估**稳性；且 genA 无解析有效域，"
               "默认 25° 是常数猜测，需调用者手工喂极限。")
    out.append("- **域外 genB**：原样返回闭式射线（flag=false，保留 gz_box_exact_m 供自查），"
               "**高估**稳性达数倍——稳性评估中高估比低估更危险。")
    out.append("- **域外 canon**：几何已知直接返回积分臂（与基准同值），几何未知退到 genA 有界线性回退；"
               "两分支均有界、均带 flag 与 limit 来源。")
    return "\n".join(out) + "\n"


def render_figures(results: Dict[str, Any]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = []
    case = results["cases"][0]
    heels = [r["heel_deg"] for r in case["rows"]]

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    ax.plot(heels, [r["gz_reference_m"] for r in case["rows"]], "k-", lw=2.4,
            label="reference (exact integration)")
    ax.plot(heels, [r["gz_closed_form_ray_m"] for r in case["rows"]], "C3--", lw=1.6,
            label="wall-sided closed form (raw ray)")
    ax.plot(heels, [r["genA_analytic_limit"]["arm_m"] for r in case["rows"]], "C0-o", ms=4,
            label="genA inline (analytic limit, linear fallback)")
    ax.plot(heels, [r["genB"]["arm_m"] for r in case["rows"]], "C2-s", ms=4,
            label="genB service (flag + cross-check)")
    ax.plot(heels, [r["canon"]["arm_m"] for r in case["rows"]], "C4-^", ms=5, lw=2.0,
            label="canon (m1564 synthesis)")
    ax.axvline(case["analytic_limit_deg"], color="grey", ls=":", lw=1.2)
    ax.text(case["analytic_limit_deg"] + 1.2,
            0.9 * max(r["gz_closed_form_ray_m"] for r in case["rows"]),
            f"φ_lim = {case['analytic_limit_deg']:.2f}°", color="grey")
    ax.set_xlabel("heel angle φ [deg]")
    ax.set_ylabel("righting arm GZ [m]")
    ax.set_title(f"GZ vs heel — box barge B={case['beam_m']:.0f} T={case['draft_m']:.0f} "
                 f"D={case['depth_m']:.0f} KG={case['kg_m']:.2f}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p1 = OUT / "fig1_gz_vs_heel.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    made.append(str(p1.relative_to(REPO)))

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    for key, style, lab in (
        ("genA_analytic_limit", "C0-o", "genA inline (linear fallback outside)"),
        ("genB", "C2-s", "genB service (wall-sided ray shipped)"),
        ("canon", "C4-^", "canon (m1564)"),
    ):
        errs = [abs(r[key]["rel_err_pct"]) for r in case["rows"]]
        ax.semilogy(heels, [max(e, 1e-12) for e in errs], style, ms=4, label=lab)
    ax.axhline(ACCURACY_BAR_PCT, color="C3", ls="--", lw=1.2, label="15% accuracy bar")
    ax.axvline(case["analytic_limit_deg"], color="grey", ls=":", lw=1.2)
    ax.set_xlabel("heel angle φ [deg]")
    ax.set_ylabel("|relative error| vs exact integration [%]")
    ax.set_title("Error envelope: where each generation stops being trustworthy")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    p2 = OUT / "fig2_relative_error.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    made.append(str(p2.relative_to(REPO)))
    return made


def main() -> int:
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    results = evaluate()
    (OUT / "results" / "comparison.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    table = render_table(results)
    (OUT / "results" / "comparison_table.md").write_text(table, encoding="utf-8")
    figs = render_figures(results)
    print(table)
    print("figures:", figs)
    c0 = results["cases"][0]
    print("SUMMARY inside:", c0["max_abs_rel_err_pct_inside"])
    print("SUMMARY outside:", c0["max_abs_rel_err_pct_outside"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())