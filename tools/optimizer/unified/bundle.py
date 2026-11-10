"""Evidence bundle: audit + pytest + figures in one reproducible command.

    python -m tools.optimizer.unified.bundle --outdir artifacts/p304w2_n10_optimizer

Steps
-----
1. run the criteria audit (:mod:`.audit`) -> JSON evidence under ``outdir``;
2. run this module's pytest suite, parse "N passed" and record it in the C7
   criterion (the audit cannot know it on its own);
3. render the four figures used by the report:
   convergence histories, accuracy vs published optima, KKT residuals and the
   cross-solver deviation bars.

Nothing here invents numbers: every plotted value is read back from the JSON
artifacts written in step 1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .audit import run_audit  # noqa: E402

NODE_ID = "P304W2-N10"


def _read(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_pytest(repo_root: Path, timeout: int = 900) -> Dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", "tests/test_unified_optimizer_dsl.py",
           "tests/test_unified_optimizer_adapters.py", "tests/test_unified_optimizer_result.py",
           "-q", "--tb=short"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=timeout)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    match = re.search(r"(\d+) passed", proc.stdout)
    failed = re.search(r"(\d+) failed", proc.stdout)
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "passed": int(match.group(1)) if match else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "summary_line": tail,
    }


def _plot_convergence(results_dir: Path, index: List[Dict[str, Any]], out: Path) -> Path:
    panels = ["g06", "g08", "g09"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, pid in zip(axes, panels):
        rows = [r for r in index if r["problem_id"] == pid and r["status"] == "converged"]
        ref = next((r.get("reference_objective") for r in rows if r.get("reference_objective") is not None), None)
        for row in rows:
            doc = _read(results_dir / Path(row["artifact"]).name)
            hist = doc["history"]
            if not hist:
                continue
            iters = [h["iteration"] for h in hist]
            vals = [abs(h["objective"] - (doc.get("reference_objective") or 0.0)) + 1e-16 for h in hist]
            ax.semilogy(iters, vals, lw=1.2, label=f"{row['engine']}:{row['algorithm']}")
        ax.set_title(f"{pid}  (reference f*={ref:.6g})")
        ax.set_xlabel("evaluation")
        ax.set_ylabel("|f - f*|")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("Convergence histories recorded by the unified interface (same history array for every engine)")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def _plot_accuracy(index: List[Dict[str, Any]], out: Path) -> Path:
    problems = sorted({r["problem_id"] for r in index})
    fig, ax = plt.subplots(figsize=(11, 4.6))
    width = 0.8 / max(len(problems), 1)
    labels = [f"{r['engine']}:{r['algorithm']}" for r in index if r["problem_id"] == problems[0]]
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(labels), 1)))
    for k, pid in enumerate(problems):
        rows = [r for r in index if r["problem_id"] == pid]
        for j, row in enumerate(rows):
            err = row["objective_rel_error"]
            if err is None:
                continue
            x = k + (j - len(rows) / 2) * width + width / 2
            ax.bar(
                x,
                max(abs(err), 1e-16),
                width=width * 0.9,
                color=colors[j % len(colors)],
                edgecolor="k",
                linewidth=0.3,
            )
    ax.set_yscale("log")
    ax.axhline(1e-2, color="r", ls="--", lw=1, label="1% acceptance")
    ax.axhline(1e-3, color="orange", ls=":", lw=1, label="0.1% (C3 threshold)")
    ax.set_xticks(range(len(problems)))
    ax.set_xticklabels(problems, rotation=20, fontsize=8)
    ax.set_ylabel("|f - f*| / max(|f*|, 1)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[j % len(colors)]) for j in range(len(labels))]
    ax.legend(handles + [
        plt.Line2D([], [], color="r", ls="--", label="1% acceptance"),
        plt.Line2D([], [], color="orange", ls=":", label="0.1% (C3)"),
    ], labels + ["1% acceptance", "0.1% (C3)"], fontsize=7, ncol=3)
    ax.set_title("Relative error of the achieved objective against the published optimum")
    ax.grid(True, axis="y", which="both", alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def _plot_kkt(index: List[Dict[str, Any]], out: Path) -> Path:
    rows = [r for r in index if r["status"] == "converged" and r["kkt_residual"] is not None]
    problems = sorted({r["problem_id"] for r in rows})
    fig, ax = plt.subplots(figsize=(11, 4.4))
    width = 0.8 / 4
    colors = plt.cm.Set2(np.linspace(0, 1, 4))
    for k, pid in enumerate(problems):
        prows = [r for r in rows if r["problem_id"] == pid]
        for j, row in enumerate(prows):
            x = k + (j - len(prows) / 2) * width + width / 2
            ax.bar(x, max(row["kkt_residual"], 1e-17), width=width * 0.9,
                   color=colors[j % len(colors)], edgecolor="k", linewidth=0.3)
            ax.annotate(f"{row['engine'][:2]}-{row['algorithm'][:5]}", (x, max(row["kkt_residual"], 1e-17)),
                        rotation=90, fontsize=5.5, ha="center", va="bottom")
    ax.set_yscale("log")
    ax.axhline(1e-4, color="r", ls="--", lw=1.2, label="KKT acceptance 1e-4")
    ax.set_xticks(range(len(problems)))
    ax.set_xticklabels(problems, rotation=20, fontsize=8)
    ax.set_ylabel("engine-independent KKT residual")
    ax.set_title("First-order optimality at the returned point (stationarity + feasibility + complementarity)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", which="both", alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def _plot_agreement(crosscheck_doc: Dict[str, Any], out: Path) -> Path:
    problems = list(crosscheck_doc)
    devs = [max(crosscheck_doc[p]["max_pairwise_rel_deviation"], 1e-17) for p in problems]
    counts = [crosscheck_doc[p]["converged_count"] for p in problems]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    bars = ax.bar(range(len(problems)), devs, color="#4c78a8", edgecolor="k", linewidth=0.4)
    for i, (b, c) in enumerate(zip(bars, counts)):
        ax.annotate(f"{c} solvers\nconverged", (i, devs[i]), textcoords="offset points", xytext=(0, 4),
                    ha="center", fontsize=7)
    ax.set_yscale("log")
    ax.axhline(0.01, color="r", ls="--", lw=1.2, label="C1 acceptance 1%")
    ax.set_xticks(range(len(problems)))
    ax.set_xticklabels(problems, rotation=20, fontsize=8)
    ax.set_ylabel("max pairwise relative deviation of f*")
    ax.set_title("Cross-engine agreement on the same problem definition (identical DSL input)")
    ax.legend()
    ax.grid(True, axis="y", which="both", alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def build_bundle(outdir: str | Path = "artifacts/p304w2_n10_optimizer", run_pytest: bool = True) -> Dict[str, Any]:
    outdir = Path(outdir)
    repo_root = Path(__file__).resolve().parents[3]

    summary = run_audit(outdir)

    pytest_info = _run_pytest(repo_root) if run_pytest else {"skipped": True}
    for crit in summary["criteria"]:
        if crit["id"] == "C7":
            crit["observed"]["pytest_result"] = pytest_info
            if pytest_info.get("skipped"):
                crit["verdict"] = "PENDING"
            elif pytest_info["passed"] >= 12 and pytest_info["failed"] == 0:
                crit["verdict"] = "PASS"
            else:
                crit["verdict"] = "FAIL"

    figs_dir = outdir / "figures"
    index = _read(outdir / "results_index.json")
    cross_doc = _read(outdir / "crosscheck.json")
    figures = [
        {
            "name": "fig1_convergence_histories.png",
            "path": _plot_convergence(outdir / "results", index, figs_dir / "fig1_convergence_histories.png"),
            "caption": "收敛史：三种引擎记录的同一 history 数组（|f-f*| vs 评估次数，对数纵轴）",
        },
        {
            "name": "fig2_accuracy_vs_reference.png",
            "path": _plot_accuracy(index, figs_dir / "fig2_accuracy_vs_reference.png"),
            "caption": "相对误差 vs 已发表最优解；红虚线 1%（验收），橙点线 0.1%（C3 门槛）",
        },
        {
            "name": "fig3_kkt_residuals.png",
            "path": _plot_kkt(index, figs_dir / "fig3_kkt_residuals.png"),
            "caption": "平台独立计算的 KKT 残差（驻点条件+原始可行+对偶可行+互补），红虚线 1e-4 验收",
        },
        {
            "name": "fig4_cross_engine_agreement.png",
            "path": _plot_agreement(cross_doc, figs_dir / "fig4_cross_engine_agreement.png"),
            "caption": "同一 DSL 问题在多引擎间的最优解偏差（对数）；本机可用引擎 scipy/nlopt，pyoptsparse 行保留为 unavailable；红虚线为 C1 的 1% 门槛",
        },
    ]
    summary["figures"] = [{"name": f["name"], "path": str(f["path"].relative_to(outdir)), "caption": f["caption"]} for f in figures]
    summary["pytest"] = pytest_info

    with open(outdir / "audit_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="build the evidence bundle (audit + tests + figures)")
    parser.add_argument("--outdir", default="artifacts/p304w2_n10_optimizer")
    parser.add_argument("--no-pytest", action="store_true")
    args = parser.parse_args(argv)
    summary = build_bundle(args.outdir, run_pytest=not args.no_pytest)
    for crit in summary["criteria"]:
        print(f"{crit['id']}: {crit['verdict']}")
    print(f"pytest: {summary['pytest']}")
    print(f"figures: {[f['path'] for f in summary['figures']]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())