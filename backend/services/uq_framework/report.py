"""Automatic, machine-parsable uncertainty report (JSON + figures).

A UQ result that cannot be *re-read* by a machine is not an audit trail, so the
report is emitted twice from the same payload:

* ``<case>_uq_report.json`` -- the whole :class:`~.result.UQResult` (config,
  propagation, numerical, model form, criteria, provenance, figure digests),
  which round-trips through ``UQResult.from_dict``;
* ``<case>_ci.png`` / ``<case>_sensitivity.png`` -- the two figures the
  contract requires (confidence interval, sensitivity), rendered with the
  Agg backend so no display is needed, plus their SHA-256 digests and sizes
  inside the JSON so a figure cannot be swapped silently.

Figure labels come only from the config/result (input names), never from a
domain vocabulary, so the module stays reusable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from .result import UQResult

__all__ = ["ReportBundle", "write_report", "figure_confidence_interval", "figure_sensitivity"]


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _digest(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "png_base64": base64.b64encode(raw).decode("ascii"),
    }


def figure_confidence_interval(result: UQResult, path: Path) -> Path:
    """Forest plot of the mean estimate with its interval and the 5-95 % range.

    No distribution is invented: every element is a number that appears in the
    result payload (``mean``, ``ci_mean``, ``quantiles``), so the figure cannot
    disagree with the JSON it ships next to.
    """
    plt = _plt()
    prop = result.propagation
    rows = []
    for key, label in (("monte_carlo", "Monte Carlo"), ("pce", "PCE")):
        leg = prop.get(key)
        if not leg:
            continue
        q = leg.get("quantiles") or {}
        rows.append((label + f" (n_evals={leg.get('n_evals')})", float(leg["mean"]),
                     leg.get("ci_mean"), q.get("p5"), q.get("p95")))
    fig, ax = plt.subplots(figsize=(7.6, 2.2 + 0.7 * max(len(rows), 1)), dpi=120)
    for i, (label, mean, ci, p5, p95) in enumerate(rows):
        if p5 is not None and p95 is not None:
            ax.plot([p5, p95], [i, i], color="0.7", lw=6, solid_capstyle="butt",
                    label="5-95 % output range" if i == 0 else None, zorder=1)
        if ci and len(ci) == 2:
            ax.plot([ci[0], ci[1]], [i, i], color="C0", lw=2.0, marker="|",
                    ms=10, zorder=3,
                    label=f"{result.headline_method and ''}CI of the mean" if i == 0 else None)
        ax.plot([mean], [i], marker="o", color="C3", ms=7, zorder=4,
                label="mean estimate" if i == 0 else None)
        ax.annotate(f"{mean:.6g}", (mean, i), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows], fontsize=8)
    ax.set_xlabel("output of interest")
    ax.set_title(f"{result.case_id}: mean, CI of the mean, and 5-95 % range "
                 f"(headline = {result.headline_method})")
    ax.grid(axis="x", alpha=0.3)
    if rows:
        ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def figure_sensitivity(result: UQResult, path: Path) -> Path:
    """Bar chart of first-order and total-order Sobol' indices."""
    plt = _plt()
    sob = result.sobol_indices or {}
    names = list(sob.get("first_order", {}).keys())
    fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=120)
    if names:
        first = [sob["first_order"][n] for n in names]
        total = [sob.get("total_order", {}).get(n, np.nan) for n in names]
        x = np.arange(len(names))
        ax.bar(x - 0.2, first, width=0.4, color="C0", label="first order")
        ax.bar(x + 0.2, total, width=0.4, color="C2", label="total order")
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=15 if len(names) > 4 else 0)
        ax.set_ylabel("Sobol' index [-]")
        closure = sob.get("closure")
        title = f"{result.case_id}: variance shares (closure {closure:.3f})" if isinstance(
            closure, (int, float)) else f"{result.case_id}: variance shares"
        ax.set_title(title)
        ax.legend(fontsize=8)
    else:
        ax.text(0.5, 0.5, "no sensitivity indices in this result",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


@dataclass
class ReportBundle:
    json_path: Path
    figures: Dict[str, Dict[str, Any]]
    payload: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "report_json": str(self.json_path),
            "figures": {k: {kk: vv for kk, vv in v.items() if kk != "png_base64"}
                        for k, v in self.figures.items()},
            "criteria": self.payload.get("criteria", {}),
        }


def write_report(result: UQResult, outdir: str | Path, make_figures: bool = True) -> ReportBundle:
    """Write ``<case>_uq_report.json`` + figures and return the bundle."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in result.case_id)
    figs: Dict[str, Dict[str, Any]] = {}
    if make_figures:
        ci_png = figure_confidence_interval(result, out / f"{slug}_ci.png")
        se_png = figure_sensitivity(result, out / f"{slug}_sensitivity.png")
        figs["confidence_interval"] = _digest(ci_png)
        figs["sensitivity"] = _digest(se_png)
    payload = result.to_dict()
    payload["figures"] = figs
    payload["report_version"] = "uq_framework/report/v1"
    payload["criteria_summary"] = {
        "all_pass": result.all_criteria_pass,
        "statuses": {k: v.get("status") for k, v in result.criteria.items()},
    }
    json_path = out / f"{slug}_uq_report.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return ReportBundle(json_path=json_path, figures=figs, payload=payload)


def load_report(path: str | Path) -> Dict[str, Any]:
    """Read a report back (used by the tests to prove machine parsability)."""
    return json.loads(Path(path).read_text())