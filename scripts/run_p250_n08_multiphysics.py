#!/usr/bin/env python3
"""P250-N08 runner: multi-physics coupling verification framework.

Builds the reference case set, runs the five anchors, the scheme study and the
defect-injection study, renders the four figures and writes one artifact that the
report and the pytest gate both read.  Every number in the report comes from
``artifacts/p250_n08/verification.json`` — nothing is typed by hand.

Usage::

    PYTHONPATH=. python scripts/run_p250_n08_multiphysics.py [--outdir DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np

from tools.coupling.multiphysics import (
    THRESHOLDS,
    Block,
    InterfaceCase,
    acceptance_record,
    analytic_cell_values,
    analytic_solution,
    budget_anchor,
    convergence_metrics,
    interface_anchor,
    run_coupled,
)
from tools.coupling.multiphysics_figures import render_figures

ROOT = Path(__file__).resolve().parents[1]
LENGTHS = (1.0, 0.8, 1.2, 1.0)
SOURCES_VOLUME = (0.05, 0.02, 0.03, 0.05)
SOURCES_FACE = (0.05, -0.03, 0.04)

# A3: three coefficient contrasts on the same geometry -----------------------
CONTRAST_CASES = {
    "matched": (1.0, 0.25, 4.0, 0.75),
    "contrast_40": (1.0, 4.0, 0.25, 0.1),
    "reversed": (0.1, 0.25, 4.0, 1.0),
}

# A2: one coupled run per conserved quantity (labels only; the framework never
# interprets them, the coefficients are what differ) -------------------------
QUANTITY_CASES = {
    "mass": {"coefficients": (1.0, 0.25, 4.0, 0.75), "sources": (0.05, 0.02, 0.03, 0.05)},
    "momentum": {"coefficients": (0.5, 2.0, 0.75, 1.5), "sources": (0.02, -0.04, 0.06, -0.03)},
    "energy": {"coefficients": (2.0, 0.4, 1.25, 0.9), "sources": (0.08, 0.01, -0.05, 0.04)},
}

SENSITIVITY_PARAMETERS = (
    ("coefficient_k1", "coefficient", 0),
    ("coefficient_k3", "coefficient", 2),
    ("u_left", "u_left", 0),
    ("u_right", "u_right", 0),
    ("source_face_s1", "source_interface", 0),
)

DAMPING = 0.5
DEFECT_SCALES = (1.0, 1.0005, 1.002, 1.005, 1.02, 1.05)


def _case(name: str, coefficients, sources, quantity: str = "conserved", **kw) -> InterfaceCase:
    blocks = tuple(
        Block(length=length, coefficient=coef, source=src, n_cells=12, grading=1.04)
        for length, coef, src in zip(LENGTHS, coefficients, sources)
    )
    return InterfaceCase(
        name=name,
        blocks=blocks,
        interface_sources=SOURCES_FACE,
        u_left=-1.0,
        u_right=1.0,
        quantity=quantity,
        tolerance=1e-12,
        max_iterations=400,
        relaxation=DAMPING,
        **kw,
    )


def build_cases() -> Dict[str, Any]:
    interface = [
        _case(name, coef, SOURCES_VOLUME) for name, coef in CONTRAST_CASES.items()
    ]
    budget = [
        _case(f"budget_{name}", spec["coefficients"], spec["sources"], quantity=name)
        for name, spec in QUANTITY_CASES.items()
    ]
    sensitivity = _case("sensitivity", CONTRAST_CASES["matched"], SOURCES_VOLUME)
    decoupling = _case("decoupling", CONTRAST_CASES["matched"], SOURCES_VOLUME)
    return {
        "interface": interface,
        "budget": budget,
        "sensitivity": sensitivity,
        "decoupling": decoupling,
    }


def build_payload(cases: Dict[str, Any]) -> Dict[str, Any]:
    record = acceptance_record(
        interface_cases=cases["interface"],
        budget_cases=cases["budget"],
        sensitivity_case=cases["sensitivity"],
        sensitivity_parameters=SENSITIVITY_PARAMETERS,
        decoupling_case=cases["decoupling"],
        scheme="newton",
    )

    # ---- figure 1 payload: fields + face positions -------------------------
    case0 = cases["interface"][0]
    run0 = run_coupled(case0, scheme="newton")
    analytic = analytic_solution(case0)
    analytic_cells = analytic_cell_values(case0, analytic)
    blocks_payload = []
    offset = 0.0
    for block, sol, ref in zip(case0.blocks, run0.blocks, analytic_cells):
        blocks_payload.append(
            {
                "length": float(block.length),
                "centers": (offset + sol.centers).tolist(),
                "values": sol.values.tolist(),
                "analytic": ref.tolist(),
            }
        )
        offset += float(block.length)
    positions = np.cumsum([0.0] + [b.length for b in case0.blocks])[1:-1]
    interfaces_payload = [
        {"index": int(i), "position": float(pos)} for i, pos in enumerate(positions)
    ]

    # ---- A3 scheme study on one case --------------------------------------
    scheme_study = {}
    scheme_metrics = {}
    for scheme in ("newton", "jacobi", "scalar"):
        run = run_coupled(case0, scheme=scheme)
        scheme_study[scheme] = [float(v) for v in run.imbalance_series]
        scheme_metrics[scheme] = convergence_metrics(run)
        scheme_metrics[scheme]["scheme"] = scheme

    convergence_payload = {
        "damping": DAMPING,
        "predicted_spectral_ratio": abs(1.0 - DAMPING),
        "cases": {},
        "scheme_study": scheme_study,
        "scheme_metrics": scheme_metrics,
    }
    for case in cases["interface"]:
        run = run_coupled(case, scheme="newton")
        convergence_payload["cases"][case.name] = {
            "newton": [float(v) for v in run.residual_series],
            "imbalance": [float(v) for v in run.imbalance_series],
            "iterations": int(run.iterations),
            "metrics": convergence_metrics(run),
        }

    # ---- defect injection study -------------------------------------------
    defect = {"scales": [], "interface_rel_error": [], "budget_rel_error": []}
    for scale in DEFECT_SCALES:
        defect["scales"].append(float(scale))
        defect["interface_rel_error"].append(float(interface_anchor(run0, scale)["max_rel_error"]))
        defect["budget_rel_error"].append(float(budget_anchor(run0, scale)["exchanged_rel_error"]))

    a1 = record["A1_interface_flux"]
    interface_view = {
        "faces": a1["cases"][0]["faces"],
        "n_faces": a1["cases"][0]["n_faces"],
        "max_rel_error": a1["max_rel_error"],
        "threshold": a1["threshold"],
        "max_rel_error_vs_analytic": max(c["max_rel_error_vs_analytic"] for c in a1["cases"]),
    }

    return {
        "record": record,
        "fields": blocks_payload,
        "interfaces": interfaces_payload,
        "interface": interface_view,
        "budget": record["A2_global_budget"],
        "sensitivity": record["A4_error_propagation"],
        "convergence": convergence_payload,
        "defect": defect,
        "cases": {
            "contrast": {k: list(v) for k, v in CONTRAST_CASES.items()},
            "quantities": QUANTITY_CASES,
            "lengths": list(LENGTHS),
            "sources_volume": list(SOURCES_VOLUME),
            "sources_face": list(SOURCES_FACE),
            "damping": DAMPING,
        },
    }


def figure_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the figure module needs (kept explicit so a figure cannot drift)."""
    return {
        "fields": [{"blocks": payload["fields"], "interfaces": payload["interfaces"]}],
        "interface": payload["interface"],
        "budget": payload["budget"],
        "defect": payload["defect"],
        "convergence": payload["convergence"],
        "sensitivity": payload["sensitivity"],
    }


def write_markdown(bundle: Dict[str, Any]) -> str:
    record = bundle["record"]
    lines = []
    lines.append("## P250-N08 多物理耦合验证框架 — 程序化结果摘要")
    lines.append("")
    lines.append("| 锚 | 判据 | 实测 | 阈值 | 结论 |")
    lines.append("|---|---|---|---|---|")
    a1 = record["A1_interface_flux"]
    lines.append(
        f"| A1 界面通量守恒 | 3 个耦合面误差 | {a1['max_rel_error']:.3e} "
        f"（{len(a1['cases'])} 个算例 × 3 面） | < {a1['threshold']:.0%} | "
        f"{'PASS' if a1['passes'] else 'FAIL'} |"
    )
    a2 = record["A2_global_budget"]
    per_q = ", ".join(f"{q['quantity']}={q['exchanged_rel_error']:.2e}" for q in a2["quantities"])
    lines.append(
        f"| A2 全局守恒 | 质量/动量/能量 | {a2['max_rel_error']:.3e}（{per_q}） | "
        f"< {a2['threshold']:.0%} | {'PASS' if a2['passes'] else 'FAIL'} |"
    )
    a3 = record["A3_convergence"]
    per_c = ", ".join(
        f"{c['case']}: ρ={c['spectral_ratio']:.4f}, p={c['order']:.4f}, {c['iterations']} 步"
        for c in a3["cases"]
    )
    lines.append(
        f"| A3 耦合收敛 | 3 工况收敛阶 ≥ {a3['thresholds']['order']:g} | {per_c} | "
        f"ρ<1 且 p≥1 | {'PASS' if a3['passes'] else 'FAIL'} |"
    )
    a4 = record["A4_error_propagation"]
    per_p = ", ".join(f"{r['parameter']}={r['sensitivity']:+.4f}" for r in a4["parameters"])
    lines.append(
        f"| A4 误差传播 | 5 输入参数系数 < 1 | {per_p}（max {a4['max_abs_sensitivity']:.4f}） | "
        f"< {a4['threshold']:g} | {'PASS' if a4['passes'] else 'FAIL'} |"
    )
    a5 = record["A5_negative_control"]
    lines.append(
        f"| A5 负控（解耦退化） | 与单物理域解偏差 | {a5['max_deviation']:.3e}"
        f"（注入 {a5['injected_leak']:.1%} 污染时 {a5['injected_leak_deviation']:.3e}） | "
        f"< {a5['threshold']:.1%} | {'PASS' if a5['passes'] else 'FAIL'} |"
    )
    lines.append("")
    lines.append(f"总判定：**{'PASS' if record['passes'] else 'FAIL'}**")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default=str(ROOT / "artifacts" / "p250_n08"))
    args = parser.parse_args(argv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    cases = build_cases()
    bundle = build_payload(cases)
    manifest = render_figures(figure_payload(bundle), outdir)
    bundle["figures"] = manifest
    bundle["thresholds"] = dict(THRESHOLDS)
    bundle["summary_markdown"] = write_markdown(bundle)

    artifact = outdir / "verification.json"
    artifact.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    payload = {
        "case_set": bundle["cases"],
        "thresholds": bundle["thresholds"],
        "A1": bundle["record"]["A1_interface_flux"],
        "A2": bundle["record"]["A2_global_budget"],
        "A3": bundle["record"]["A3_convergence"],
        "A4": bundle["record"]["A4_error_propagation"],
        "A5": bundle["record"]["A5_negative_control"],
        "passes": bundle["record"]["passes"],
        "defect_study": bundle["defect"],
        "scheme_study": bundle["convergence"]["scheme_metrics"],
        "figures": [
            {"name": f["name"], "caption": f["caption"], "sha256": f["sha256"]} for f in manifest
        ],
        "artifact": str(artifact.relative_to(ROOT)),
    }
    (outdir / "report_metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(bundle["summary_markdown"])
    print()
    print("figures:", ", ".join(f"{f['name']} ({f['sha256'][:12]})" for f in manifest))
    print("artifact:", artifact)
    return 0 if bundle["record"]["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
