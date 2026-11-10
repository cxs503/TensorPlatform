"""Figures for the second-implementation maturity run (matplotlib, Agg backend).

Every figure is drawn from ``artifacts/evidence_second_impl/run_report.json`` —
i.e. from numbers that already went through SQL — so a figure cannot disagree
with the report.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPORT_REL = "artifacts/evidence_second_impl/run_report.json"
FIG_DIR_REL = "artifacts/evidence_second_impl/figures"


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_detection_matrix(report: Mapping[str, Any], out: Path) -> Path:
    rows = report["ledger"]["sql"]["detection_matrix"]
    kinds = sorted({r["kind"] for r in rows})
    injections = sorted({r["injection"] for r in rows})
    grid = [[0.0 for _ in injections] for _ in kinds]
    labels = [["" for _ in injections] for _ in kinds]
    for r in rows:
        i, j = kinds.index(r["kind"]), injections.index(r["injection"])
        total = r["n_evaluated"] or 1
        grid[i][j] = r["n_caught"] / total
        labels[i][j] = f"{r['n_caught']}/{r['n_evaluated']}"

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(injections)), injections, rotation=15)
    ax.set_yticks(range(len(kinds)), kinds)
    for i in range(len(kinds)):
        for j in range(len(injections)):
            ax.text(j, i, labels[i][j] or "-", ha="center", va="center", fontsize=10,
                    color="black")
    ax.set_xlabel("injection class")
    ax.set_ylabel("metamorphic relation")
    ax.set_title("Injection detection per relation family (caught / evaluated)")
    fig.colorbar(im, ax=ax, label="detection fraction")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_residual_separation(report: Mapping[str, Any], out: Path) -> Path:
    clean = report["bench"]["clean"]
    inj = report["ledger"]["sql"]["injection_residuals"]
    tol_line = 1.0  # ratio == 1 is the pass/fail boundary by definition

    groups: List[tuple[str, List[float], str]] = [
        ("clean (n=%d)" % len(clean),
         [max(c["max_residual_ratio"], 1e-18) for c in clean], "#2b8cbe"),
    ]
    for kind in sorted({r["injection"] for r in inj}):
        vals = [max(r["max_residual_ratio"], 1e-18) for r in inj if r["injection"] == kind]
        groups.append((kind, vals, "#d7301f"))

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for idx, (name, vals, color) in enumerate(groups):
        xs = [idx + (i - len(vals) / 2) * 0.06 for i in range(len(vals))]
        ax.scatter(xs, vals, color=color, alpha=0.8, label=name, s=26)
    ax.axhline(tol_line, color="black", linestyle="--", linewidth=1.0)
    ax.text(len(groups) - 0.5, tol_line * 1.6, "tolerance boundary (ratio = 1)",
            ha="right", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(range(len(groups)), [g[0] for g in groups], rotation=12)
    ax.set_ylabel("max residual / tolerance  (log scale)")
    ax.set_title("Gate separation: honest residuals vs injected forgery classes")
    ax.legend(fontsize=8, loc="center left")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_independence(report: Mapping[str, Any], out: Path) -> Path:
    sql = report["ledger"]["sql"]
    counts = sql["grade_counts"]
    before: Dict[str, int] = {}
    after: Dict[str, int] = {}
    for row in counts:
        before[row["graded_grade"]] = before.get(row["graded_grade"], 0) + row["n"]
        after[row["regraded_grade"]] = after.get(row["regraded_grade"], 0) + row["n"]

    grades = ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]
    xs = range(len(grades))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.0),
                                   gridspec_kw={"width_ratios": [1.1, 1.0]})
    w = 0.38
    ax1.bar([x - w / 2 for x in xs], [before.get(g, 0) for g in grades], width=w,
            label="claimed (audit)", color="#9ecae1")
    ax1.bar([x + w / 2 for x in xs], [after.get(g, 0) for g in grades], width=w,
            label="after independence re-grade", color="#3182bd")
    ax1.set_xticks(list(xs), grades, rotation=20)
    ax1.set_ylabel("capabilities")
    ax1.set_title("Grade distribution before / after independence rule")
    ax1.legend(fontsize=8)
    for x, g in zip(xs, grades):
        if after.get(g):
            ax1.text(x + w / 2, after.get(g, 0) + 0.05, str(after[g]), ha="center", fontsize=8)

    sources = report["independence"]["by_source"]
    names = sorted(sources)
    ax2.barh(names, [sources[n] for n in names], color="#41ab5d")
    for i, n in enumerate(names):
        ax2.text(sources[n] + 0.05, i, str(sources[n]), va="center", fontsize=8)
    ax2.set_xlabel("declarations")
    ax2.set_title("Declared reference-source types")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def _rel(path: Path) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def build_all(report_path: Path | str = ROOT / REPORT_REL,
              out_dir: Path | str = ROOT / FIG_DIR_REL) -> Dict[str, str]:
    report = _load(Path(report_path))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "detection_matrix": fig_detection_matrix(report, out / "fig1_detection_matrix.png"),
        "residual_separation": fig_residual_separation(report, out / "fig2_residual_separation.png"),
        "independence": fig_independence(report, out / "fig3_independence_regrade.png"),
    }
    (out / "figures.json").write_text(
        json.dumps({k: _rel(v) for k, v in paths.items()},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="build second-implementation figures")
    ap.add_argument("--report", default=str(ROOT / REPORT_REL))
    ap.add_argument("--out", default=str(ROOT / FIG_DIR_REL))
    args = ap.parse_args(argv)
    print(json.dumps(build_all(args.report, args.out), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())