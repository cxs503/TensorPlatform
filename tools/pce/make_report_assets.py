"""Turn UQ case ledgers into report-grade figures and markdown tables.

The generator is *domain free*: every label it prints comes out of a ledger
(case title, input names, numbers).  Domain prose is supplied separately as a
markdown file with placeholders, so the narrative lives in data (``docs/``)
while this tool stays a pure formatter.

Usage::

    python -m tools.pce.make_report_assets \
        --ledger-dir artifacts/pce_m9833 \
        --out-dir artifacts/pce_m9833 \
        --prose docs/pce_m9833/report_prose.md \
        --report docs/pce_m9833/REPORT.md

Placeholders understood in the prose file::

    {{FIG_SOBOL}} {{FIG_BAND}} {{FIG_RANK}} {{FIG_SPEEDUP}}
    {{TABLE_SOBOL}} {{TABLE_MOMENTS}} {{TABLE_BAND}} {{TABLE_RANK}}
    {{TABLE_ACCELERATION}} {{TABLE_SWEEP_MC}} {{TABLE_SWEEP_PCE}}
    {{TABLE_CRITERIA}} {{PROVENANCE}}

Every number in the generated markdown is read back from a ledger: nothing is
typed by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

DEFAULT_LEDGER_DIR = Path("artifacts/pce_m9833")
DEFAULT_OUT_DIR = Path("artifacts/pce_m9833")


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def _sci(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (x != x)):
        return "n/a"
    if x == 0.0:
        return "0"
    if abs(x) >= 1e-3 and abs(x) < 1e5:
        return f"{x:.{digits + 1}f}"
    return f"{x:.{digits}e}"


def _pct(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (x != x)):
        return "n/a"
    return f"{100.0 * x:.{digits}f}%"


def _num(x: float, digits: int = 1) -> str:
    return f"{x:,.{digits}f}"


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def _style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "savefig.dpi": 130,
            "font.size": 9,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def fig_sobol(ledger: Dict[str, Any], path: Path) -> None:
    """First/total order indices: reference vs surrogate, per input."""
    names = list(ledger["pce"]["sobol_first"])
    ref = ledger["reference"].get("sobol") or {}
    s1 = [ledger["pce"]["sobol_first"][n] for n in names]
    st = [ledger["pce"]["sobol_total"][n] for n in names]
    x = np.arange(len(names))
    w = 0.2
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.bar(x - 1.5 * w, s1, w, label="PCE $S_i$ (first)", color="#1f77b4")
    ax.bar(x - 0.5 * w, st, w, label="PCE $S_{T,i}$ (total)", color="#1f77b4", alpha=0.45)
    if ref:
        r1 = [ref["first"].get(n, np.nan) for n in names]
        rt = [ref["total"].get(n, np.nan) for n in names]
        ax.bar(x + 0.5 * w, r1, w, label="closed-form $S_i$", color="#d62728", alpha=0.8)
        ax.bar(x + 1.5 * w, rt, w, label="closed-form $S_{T,i}$", color="#d62728", alpha=0.45)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Sobol' index [-]")
    ax.set_title(ledger["case_id"] + ": PCE vs closed-form variance decomposition")
    ax.legend(fontsize=7, ncol=2, frameon=False)
    if ref:
        pair = ref.get("second") or {}
        if pair:
            key, value = next(iter(pair.items()))
            pce_pair = (ledger["pce"].get("sobol_second") or {}).get(key)
            if pce_pair is not None:
                ax.annotate(
                    f"pair {key}: PCE {pce_pair:.4f} vs closed-form {value:.4f}",
                    xy=(0.02, 0.95), xycoords="axes fraction", fontsize=7, va="top",
                )
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_rank(ledger: Dict[str, Any], path: Path) -> None:
    """Parameter ranking by total-order index (the screening answer)."""
    names = list(ledger["pce"]["sobol_total"])
    st = np.array([ledger["pce"]["sobol_total"][n] for n in names])
    s1 = np.array([ledger["pce"]["sobol_first"][n] for n in names])
    order = np.argsort(st)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    y = np.arange(len(names))
    ax.barh(y, st[order], color="#1f77b4", label="$S_{T,i}$ total")
    ax.barh(y, s1[order], color="#ff7f0e", height=0.45, label="$S_i$ first")
    ax.set_yticks(y)
    ax.set_yticklabels([names[j] for j in order])
    ax.set_xlabel("variance share [-]")
    ax.set_title(ledger["case_id"] + ": parameter ranking")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_band(ledger: Dict[str, Any], path: Path) -> None:
    """Response distribution with the surrogate and Monte Carlo bands."""
    ref = ledger["reference"]
    sample = np.asarray(ref.get("response_sample") or [], dtype=float)
    band = ledger["pce"]["band"]
    mc = ledger["monte_carlo"]
    probs = ledger["band_probs"]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    if sample.size:
        ax.hist(sample, bins=60, color="#bbbbbb", alpha=0.75, density=True,
                label=f"reference sample (n={ref['n']})")
        lo, hi = np.quantile(sample, probs[0]), np.quantile(sample, probs[-1])
        ax.axvspan(lo, hi, color="#d62728", alpha=0.10,
                   label=f"reference {100 * (probs[-1] - probs[0]):.0f}% band")
    for value, style_, label in (
        (band["lower"], dict(color="#1f77b4", lw=2, ls="--"), "surrogate band edge"),
        (band["upper"], dict(color="#1f77b4", lw=2, ls="--"), None),
        (band["median"], dict(color="#1f77b4", lw=1.2, ls=":"), "surrogate median"),
    ):
        ax.axvline(value, **style_, label=label)
    for value in (mc["quantiles"][f"p{100 * probs[0]:g}"], mc["quantiles"][f"p{100 * probs[-1]:g}"]):
        ax.axvline(value, color="#2ca02c", lw=1.2, ls="-.",
                   label="Monte Carlo band edge")
    ax.set_xlabel("response")
    ax.set_ylabel("probability density")
    ax.set_title(
        f"{ledger['case_id']}: {100 * (probs[-1] - probs[0]):.0f}% uncertainty band "
        f"(surrogate width {_num(band['width'], 0)}, reference {_num(ref['band'], 0)})"
    )
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_speedup(ledger: Dict[str, Any], path: Path) -> None:
    """Error vs cost, and the wall-clock speed-up as a function of model cost."""
    sweep = ledger.get("sweep", {})
    mc = sweep.get("mc_ladder", [])
    pce = [r for r in sweep.get("pce_ladder", []) if "error" not in r]
    accel = ledger["acceleration"]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.5))

    ax = axes[0]
    if mc:
        ax.loglog([r["n_model_evaluations"] for r in mc],
                  [max(r["rel_err_std"], 1e-12) for r in mc],
                  "o-", color="#d62728", label="Monte Carlo: rel. error of std")
        ax.loglog([r["n_model_evaluations"] for r in mc],
                  [max(r["rel_err_band"], 1e-12) for r in mc],
                  "s--", color="#d62728", alpha=0.5, label="Monte Carlo: rel. error of band")
    if pce:
        ax.loglog([r["n_model_evaluations"] for r in pce],
                  [max(r["rel_err_std"], 1e-12) for r in pce],
                  "o-", color="#1f77b4", label="PCE: rel. error of std")
        ax.loglog([r["n_model_evaluations"] for r in pce],
                  [max(r["rel_err_band"], 1e-12) for r in pce],
                  "s--", color="#1f77b4", alpha=0.5, label="PCE: rel. error of band")
    tol = ledger["criteria"].get("moment_rel_err_std_vs_reference", {}).get("threshold")
    if tol:
        ax.axhline(tol, color="k", lw=1, ls=":", label=f"acceptance {_pct(tol,0)}")
    ax.set_xlabel("model evaluations")
    ax.set_ylabel("relative error vs reference")
    ax.set_title("accuracy per model evaluation")
    ax.legend(fontsize=6.5, frameon=False)

    ax = axes[1]
    t = np.logspace(-6, 1, 120)
    n_mc = accel["n_model_evaluations_mc"]
    n_pce = accel["n_model_evaluations_pce"]
    overhead = accel["pce_fixed_overhead_seconds"]
    speed = n_mc * t / (n_pce * t + overhead)
    ax.loglog(t, speed, color="#1f77b4", lw=2, label="projected wall-clock speed-up")
    target = accel["target_wall_clock_speedup"]
    ax.axhline(target, color="k", lw=1, ls=":", label=f"target {_num(target, 0)}x")
    ax.axvline(accel["seconds_per_model_evaluation"], color="#d62728", lw=1, ls="--",
               label="measured model cost")
    if np.isfinite(accel.get("breakeven_seconds_per_evaluation", np.inf)):
        ax.axvline(accel["breakeven_seconds_per_evaluation"], color="#2ca02c", lw=1, ls="-.",
                   label="break-even cost")
    ax.set_xlabel("black-box cost per evaluation [s]")
    ax.set_ylabel("wall-clock speed-up vs Monte Carlo")
    ax.set_title(f"speed-up = {accel['speedup_model_evaluations']:.1f}x in evaluations")
    ax.legend(fontsize=6.5, frameon=False, loc="lower right")

    fig.suptitle(ledger["case_id"] + ": Monte Carlo acceleration", fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def table_sobol(ledger: Dict[str, Any]) -> str:
    ref = ledger["reference"].get("sobol") or {}
    errs = ledger["errors"].get("sobol") or {}
    names = list(ledger["pce"]["sobol_first"])
    lines = [
        "| input | $S_i$ closed form | $S_i$ PCE | abs err | rel err | "
        "$S_{T,i}$ closed form | $S_{T,i}$ PCE | abs err | rel err |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n in names:
        lines.append(
            "| {n} | {r1} | {p1} | {e1} | {re1} | {rt} | {pt} | {et} | {ret} |".format(
                n=n,
                r1=_sci(ref.get("first", {}).get(n, float("nan")), 4),
                p1=_sci(ledger["pce"]["sobol_first"][n], 4),
                e1=_sci(errs.get("abs_err_first", {}).get(n, float("nan")), 3),
                re1=_pct(errs.get("rel_err_first", {}).get(n, float("nan")), 3),
                rt=_sci(ref.get("total", {}).get(n, float("nan")), 4),
                pt=_sci(ledger["pce"]["sobol_total"][n], 4),
                et=_sci(errs.get("abs_err_total", {}).get(n, float("nan")), 3),
                ret=_pct(errs.get("rel_err_total", {}).get(n, float("nan")), 3),
            )
        )
    pair = (ledger["pce"].get("sobol_second") or {})
    ref_pair = ref.get("second") or {}
    if pair or ref_pair:
        lines.append("")
        lines.append("| pair index | closed form | PCE | abs err |")
        lines.append("|---|---|---|---|")
        for key, value in ref_pair.items():
            got = pair.get(key, float("nan"))
            lines.append(f"| $S_{{{key}}}$ | {_sci(value, 4)} | {_sci(got, 4)} | {_sci(abs(got - value), 3)} |")
    if "rel_err_variance" in errs:
        lines.append("")
        lines.append(
            f"Variance: closed form {_sci(ref.get('variance'), 6)} vs PCE "
            f"{_sci(ledger['pce']['variance'], 6)} (relative error {_pct(errs['rel_err_variance'], 4)})."
        )
    return "\n".join(lines)


def table_moments(ledger: Dict[str, Any]) -> str:
    pce = ledger["pce"]
    mc = ledger["monte_carlo"]
    ref = ledger["reference"]
    errs = ledger["errors"]
    rows = [
        ("mean", pce["mean"], mc["mean"], ref["mean"],
         errs["vs_reference"]["rel_err_mean"], errs["mc_vs_reference"]["rel_err_mean"]),
        ("std", pce["std"], mc["std"], ref["std"],
         errs["vs_reference"]["rel_err_std"], errs["mc_vs_reference"]["rel_err_std"]),
    ]
    lines = [
        "| statistic | PCE | Monte Carlo (budget) | reference | rel err PCE vs ref | rel err MC vs ref |",
        "|---|---|---|---|---|---|",
    ]
    for label, a, b, c, e1, e2 in rows:
        lines.append(
            f"| {label} | {_num(a, 1)} | {_num(b, 1)} | {_num(c, 1)} | {_pct(e1, 4)} | {_pct(e2, 4)} |"
        )
    return "\n".join(lines)


def table_band(ledger: Dict[str, Any]) -> str:
    pce_band = ledger["pce"]["band"]
    mc = ledger["monte_carlo"]
    ref = ledger["reference"]
    probs = ledger["band_probs"]
    keys = [f"p{100 * p:g}" for p in probs]
    lines = [
        "| route | " + " | ".join(keys) + " | width | rel err width |",
        "|---" * (len(keys) + 3) + "|",
        "| surrogate (PCE) | "
        + " | ".join(_sci(v, 6) for v in pce_band["quantiles"])
        + f" | {_sci(pce_band['width'], 6)} | "
        f"{_pct(ledger['errors']['vs_reference']['rel_err_band'], 4)} |",
        "| Monte Carlo (budget) | "
        + " | ".join(_sci(mc["quantiles"][k], 6) for k in keys)
        + f" | {_sci(mc['band'], 6)} | {_pct(ledger['errors']['mc_vs_reference']['rel_err_band'], 4)} |",
        "| reference | "
        + " | ".join(_sci(ref["quantiles"][k], 6) for k in keys)
        + f" | {_sci(ref['band'], 6)} | — |",
    ]
    return "\n".join(lines)


def table_rank(ledger: Dict[str, Any]) -> str:
    names = list(ledger["pce"]["sobol_total"])
    rows = sorted(
        (
            (
                n,
                ledger["pce"]["sobol_first"][n],
                ledger["pce"]["sobol_total"][n],
                ledger["pce"]["sobol_total"][n] - ledger["pce"]["sobol_first"][n],
            )
            for n in names
        ),
        key=lambda r: -r[2],
    )
    lines = ["| rank | input | $S_i$ | $S_{T,i}$ | $S_{T,i}-S_i$ (interaction) |", "|---|---|---|---|---|"]
    for rank, (n, s1, st, inter) in enumerate(rows, start=1):
        lines.append(f"| {rank} | {n} | {_sci(s1, 4)} | {_sci(st, 4)} | {_sci(inter, 4)} |")
    total_first = sum(r[1] for r in rows)
    total_total = sum(r[2] for r in rows)
    lines.append("")
    lines.append(
        f"Sum of first-order indices {_sci(total_first, 4)}; sum of total-order indices "
        f"{_sci(total_total, 4)} (an additive response has both equal to 1)."
    )
    return "\n".join(lines)


def table_acceleration(ledger: Dict[str, Any]) -> str:
    a = ledger["acceleration"]
    lines = [
        "| metric | value |",
        "|---|---|",
        f"| model evaluations, surrogate | {a['n_model_evaluations_pce']} |",
        f"| model evaluations, Monte Carlo | {a['n_model_evaluations_mc']} |",
        f"| **speed-up in model evaluations** | **{_num(a['speedup_model_evaluations'], 2)}x** |",
        f"| wall clock, surrogate pipeline | {a['wall_seconds_pce']:.4f} s |",
        f"| wall clock, Monte Carlo pipeline | {a['wall_seconds_mc']:.4f} s |",
        f"| measured wall-clock speed-up | {_num(a['speedup_wall_clock'], 2)}x |",
        f"| measured cost per black-box call | {_sci(a['seconds_per_model_evaluation'], 2)} s |",
        f"| surrogate fixed overhead | {_sci(a['pce_fixed_overhead_seconds'], 2)} s |",
        f"| wall-clock speed-up at 1 s per call | {_num(a['projected_speedup_at_1s_per_evaluation'], 2)}x |",
        f"| break-even cost for {_num(a['target_wall_clock_speedup'], 0)}x wall clock "
        f"| {_sci(a.get('breakeven_seconds_per_evaluation'), 2)} s |",
    ]
    return "\n".join(lines)


def table_sweep(ledger: Dict[str, Any], which: str) -> str:
    sweep = ledger.get("sweep", {})
    rows = sweep.get("mc_ladder" if which == "mc" else "pce_ladder", [])
    if not rows:
        return "_no sweep recorded_"
    if which == "mc":
        lines = [
            "| model evaluations | rel err std | rel err band | wall clock [s] |",
            "|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r['n_model_evaluations']} | {_pct(r['rel_err_std'], 3)} | "
                f"{_pct(r['rel_err_band'], 3)} | {r['wall_seconds']:.4f} |"
            )
    else:
        lines = [
            "| degree | samples | terms | model evaluations | rel err std | rel err band "
            "| LOO rel err | max abs err Sobol' | wall clock [s] |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            if "error" in r:
                lines.append(
                    f"| {r['degree']} | {r['n_samples']} | — | — | — | — | — | — | design rejected: {r['error']} |"
                )
                continue
            lines.append(
                f"| {r['degree']} | {r['n_samples']} | {r['n_terms']} | {r['n_model_evaluations']} | "
                f"{_pct(r['rel_err_std'], 3)} | {_pct(r['rel_err_band'], 3)} | "
                f"{_pct(r['loo_relative_error'], 3)} | "
                f"{_sci(r.get('max_abs_err_sobol', float('nan')), 2)} | {r['wall_seconds']:.4f} |"
            )
    return "\n".join(lines)


def table_inputs(ledger: Dict[str, Any]) -> str:
    """Marginal laws exactly as the case file injected them."""
    lines = ["| input | law | parameters | coefficient of variation |", "|---|---|---|---|"]
    for m in ledger["inputs"]:
        if m["family"] == "uniform":
            params = f"lower {_sci(m['lower'], 4)}, upper {_sci(m['upper'], 4)}"
        else:
            params = f"mean {_sci(m['mean'], 4)}, std {_sci(m['std'], 4)}"
        cv = _pct(abs(m["std"] / m["mean"]), 3) if m.get("mean") else "—"
        lines.append(f"| {m['name']} | {m['family']} | {params} | {cv} |")
    return "\n".join(lines)


def table_verdict(ledger: Dict[str, Any]) -> str:
    """Explicit per-quantity error verdict (the report's error-judgement section)."""
    errs = ledger["errors"]
    lines = ["| quantity | baseline | relative error | question | answer |", "|---|---|---|---|---|"]
    rows = [
        ("mean of the response", "reference sample", errs["vs_reference"]["rel_err_mean"], 0.03),
        ("standard deviation", "reference sample", errs["vs_reference"]["rel_err_std"], 0.03),
        ("band width", "reference sample", errs["vs_reference"]["rel_err_band"], 0.03),
        ("standard deviation", "Monte Carlo at the stated budget",
         errs["vs_mc_budget"]["rel_err_std"], 0.03),
        ("band width", "Monte Carlo at the stated budget",
         errs["vs_mc_budget"]["rel_err_band"], 0.03),
    ]
    for label, baseline, value, tol in rows:
        answer = "**yes**" if value <= tol else "**no**"
        lines.append(
            f"| {label} | {baseline} | {_pct(value, 4)} | below {_pct(tol, 0)}? | {answer} |"
        )
    sobol = errs.get("sobol")
    if sobol:
        lines.append(
            f"| first-order Sobol' indices | closed form | max abs err "
            f"{_sci(sobol['max_abs_err_first'], 3)} (max rel {_pct(sobol['max_rel_err_first'], 3)}) "
            f"| below {_pct(0.05, 0)}? | **{'yes' if sobol['max_abs_err_first'] <= 0.05 else 'no'}** |"
        )
        lines.append(
            f"| total-order Sobol' indices | closed form | max abs err "
            f"{_sci(sobol['max_abs_err_total'], 3)} (max rel {_pct(sobol['max_rel_err_total'], 3)}) "
            f"| below {_pct(0.05, 0)}? | **{'yes' if sobol['max_abs_err_total'] <= 0.05 else 'no'}** |"
        )
        if "rel_err_variance" in sobol:
            lines.append(
                f"| variance | closed form | {_pct(sobol['rel_err_variance'], 4)} "
                f"| below {_pct(0.05, 0)}? | "
                f"**{'yes' if sobol['rel_err_variance'] <= 0.05 else 'no'}** |"
            )
    mc_vs_ref = errs["mc_vs_reference"]
    lines.append(
        f"| (baseline quality) Monte Carlo std | reference sample | "
        f"{_pct(mc_vs_ref['rel_err_std'], 4)} | sampling error of the baseline itself | — |"
    )
    return "\n".join(lines)


def table_criteria(ledger: Dict[str, Any]) -> str:
    lines = ["| criterion | value | threshold | verdict |", "|---|---|---|---|"]
    for key, c in sorted(ledger["criteria"].items()):
        verdict = "PASS" if c["pass"] else "FAIL"
        lines.append(
            f"| {key} | {_sci(c['value'], 4)} | {c['mode']} {_sci(c['threshold'], 4)} | **{verdict}** |"
        )
    lines.append("")
    lines.append(
        "All criteria pass: **{}**.".format("yes" if ledger["all_criteria_pass"] else "no")
    )
    return "\n".join(lines)


def provenance(ledgers: Sequence[Dict[str, Any]]) -> str:
    lines = ["| item | value |", "|---|---|"]
    for led in ledgers:
        lines.append(f"| case | `{led['case_id']}` |")
        lines.append(f"| case file | `{led['case_path']}` |")
        lines.append(f"| case sha256 | `{led['case_sha256']}` |")
        lines.append(f"| ledger generated at | {led['generated_at']} |")
        lines.append(f"| model | `{led['model']['path']}` (file `{led['model']['file']}`) |")
    led0 = ledgers[0]
    lines.append(f"| git commit | `{led0['git_sha']}` |")
    for key, value in led0["stack"].items():
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build_assets(
    ledger_dir: Path = DEFAULT_LEDGER_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    prose: Optional[Path] = None,
    report: Optional[Path] = None,
) -> Dict[str, Any]:
    _style()
    ledger_dir = Path(ledger_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledgers = []
    for path in sorted(ledger_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        led = json.loads(path.read_text())
        if "case_id" in led and "criteria" in led:
            ledgers.append(led)
    if not ledgers:
        raise SystemExit(f"no case ledgers found in {ledger_dir}")

    # the closed-form-anchored ledger feeds the Sobol' table/figure, the
    # application ledger (no closed-form anchor) feeds the band/acceleration
    with_reference = [led for led in ledgers if led["reference"].get("sobol")]
    without_reference = [led for led in ledgers if not led["reference"].get("sobol")]
    sob = with_reference[0] if with_reference else ledgers[0]
    candidates = without_reference or ledgers
    sampled = [led for led in candidates if led["reference"].get("response_sample")]
    band = sampled[0] if sampled else candidates[0]

    artefacts: Dict[str, str] = {}
    f1 = out_dir / "fig_sobol_vs_closed_form.png"
    fig_sobol(sob, f1)
    f2 = out_dir / "fig_response_band.png"
    fig_band(band, f2)
    f3 = out_dir / "fig_parameter_ranking.png"
    fig_rank(band, f3)
    f4 = out_dir / "fig_speedup_curve.png"
    fig_speedup(band, f4)
    artefacts["figs"] = [str(f1), str(f2), str(f3), str(f4)]

    blocks = {
        "TABLE_SOBOL": table_sobol(sob),
        "TABLE_MOMENTS": table_moments(band),
        "TABLE_BAND": table_band(band),
        "TABLE_RANK": table_rank(band),
        "TABLE_ACCELERATION": table_acceleration(band),
        "TABLE_SWEEP_MC": table_sweep(band, "mc"),
        "TABLE_SWEEP_PCE": table_sweep(band, "pce"),
        "TABLE_CRITERIA": table_criteria(band) + "\n\n" + table_criteria(sob),
        "TABLE_VERDICT_SOBOL": table_verdict(sob),
        "TABLE_VERDICT_BAND": table_verdict(band),
        "TABLE_INPUTS_ISHIGAMI": table_inputs(sob),
        "TABLE_INPUTS_BAND": table_inputs(band),
        "PROVENANCE": provenance(ledgers),
        "FIG_SOBOL": f"![PCE vs closed-form Sobol' indices]({f1.name})",
        "FIG_BAND": f"![uncertainty band]({f2.name})",
        "FIG_RANK": f"![parameter ranking]({f3.name})",
        "FIG_SPEEDUP": f"![speed-up curves]({f4.name})",
        "VALUE_SPEEDUP_EVALS": f"{band['acceleration']['speedup_model_evaluations']:.1f}x",
        "VALUE_SPEEDUP_WALL": f"{band['acceleration']['speedup_wall_clock']:.1f}x",
        "VALUE_BAND_ERR": _pct(band["errors"]["vs_reference"]["rel_err_band"], 2),
        "VALUE_STD_ERR": _pct(band["errors"]["vs_reference"]["rel_err_std"], 3),
        "VALUE_MC_STD_ERR": _pct(band["errors"]["vs_mc_budget"]["rel_err_std"], 3),
        "VALUE_SOBOL_MAX_ABS": _sci(sob["errors"]["sobol"]["max_abs_err_first"], 2),
        "VALUE_SOBOL_MAX_ABS_TOTAL": _sci(sob["errors"]["sobol"]["max_abs_err_total"], 2),
        "VALUE_VARIANCE_ERR": _pct(sob["errors"]["sobol"]["rel_err_variance"], 4),
        "VALUE_PCE_EVALS": f"{band['acceleration']['n_model_evaluations_pce']}",
        "VALUE_MC_EVALS": f"{band['acceleration']['n_model_evaluations_mc']}",
    }

    out: Dict[str, Any] = {"artefacts": artefacts, "blocks": sorted(blocks)}
    if prose and report:
        text = Path(prose).read_text()
        for key, value in blocks.items():
            text = text.replace("{{" + key + "}}", value)
        missing = re.findall(r"\{\{([A-Z_]+)\}\}", text)
        if missing:
            raise SystemExit(f"unresolved placeholders in prose: {sorted(set(missing))}")
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(text)
        out["report"] = str(report)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger-dir", default=str(DEFAULT_LEDGER_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--prose", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    res = build_assets(
        ledger_dir=Path(args.ledger_dir),
        out_dir=Path(args.out_dir),
        prose=Path(args.prose) if args.prose else None,
        report=Path(args.report) if args.report else None,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())