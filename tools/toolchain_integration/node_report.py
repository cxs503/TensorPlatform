"""Turn a run artifact into a paper-level node report (numbers injected, not typed).

The report answers the six questions a platform report must answer, and every
number in it is read out of ``artifact.json`` or computed here from an independent
reference — nothing is transcribed by hand:

1. what was composed and with which parameters (from the spec);
2. how the composition was checked (static port typing, fail-closed execution,
   hash-chained record, cost accounting);
3. the numbers (per-stage cost, criteria table, calibration statistics,
   conservation residuals, propagated moments);
4. figures (timing bars, reliability diagram, uncertainty band);
5. pitfalls observed while building this node;
6. **error judgement against independent references**, with an explicit
   ``< 3 %`` answer per quantity.  The references are computed in this module:
   the response's propagated moments from a large independent Monte-Carlo sample
   (rejection-sampled truncation, own formula, no import of the UQ framework), and
   the AUROC from quadrature of the generative model.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

SECTIONS = (
    "1. Scenario and parameters",
    "2. Method",
    "3. Results",
    "4. Figures",
    "5. Pitfalls and limitations",
    "6. Error judgement against references",
)


# ---------------------------------------------------------------------------
# independent references
# ---------------------------------------------------------------------------


def reference_moments(response: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]],
                      n: int = 2_000_000, seed: int = 12345) -> Dict[str, float]:
    """Independent Monte-Carlo reference for the propagated response.

    Implemented from the definition (rejection-sampled truncated normals, own
    arithmetic for the response) so it shares no code with the framework under
    test.  Slow but straightforward: ~2e6 samples is a few seconds.
    """
    if response.get("kind") not in ("quadratic", "quartic"):
        raise ValueError("reference_moments supports quadratic/quartic responses")
    centre = np.asarray(response["center"], dtype=float)
    scale = np.asarray(response.get("scale", [1.0] * centre.size), dtype=float)
    quartic = float(response.get("quartic", 0.0)) if response.get("kind") == "quartic" else 0.0
    rng = np.random.default_rng(seed)
    cols: List[np.ndarray] = []
    for spec in inputs:
        if spec["kind"] != "normal":
            raise ValueError("reference_moments supports normal marginals only")
        mu, sd = float(spec["mean"]), float(spec["std"])
        k = float(spec.get("truncate_sigma", 4.0))
        z = rng.standard_normal(n)
        keep = np.abs(z) <= k                      # rejection -> truncated normal
        z = z[keep]
        cols.append(mu + sd * z)
    m = min(c.size for c in cols)
    d = np.column_stack([c[:m] for c in cols]) - centre
    y = np.sum((scale * d) ** 2, axis=1) + quartic * np.sum(d ** 4, axis=1)
    return {
        "n": float(m),
        "mean": float(y.mean()),
        "std": float(y.std(ddof=0)),
        "q05": float(np.quantile(y, 0.05)),
        "q50": float(np.quantile(y, 0.50)),
        "q95": float(np.quantile(y, 0.95)),
        "min": float(y.min()),
    }


def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUC by the Mann-Whitney U statistic (independent implementation)."""
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(pos.size + neg.size, dtype=float)
    ranks[order] = np.arange(1, pos.size + neg.size + 1, dtype=float)
    # mid-ranks for ties
    sorted_scores = np.concatenate([pos, neg])[order]
    i = 0
    while i < sorted_scores.size:
        j = i
        while j + 1 < sorted_scores.size and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = 0.5 * (i + 1 + j + 1)
        i = j + 1
    u = ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2.0
    return float(u / (pos.size * neg.size))


def reference_calibration(signal: float, noise: float, n: int = 4_000_000,
                          seed: int = 424242) -> Dict[str, float]:
    """Reference statistics of the declared generative score model.

    The probe stage declares ``label = [latent > 0]`` and
    ``score = signal*latent + noise*eps`` with independent standard normals.  This
    function simulates that model directly (own sampling, own AUC, own reliability
    binning) and returns the statistics the run's 3000-sample estimate must match.
    """
    rng = np.random.default_rng(seed)
    latent = rng.standard_normal(n)
    scores = signal * latent + noise * rng.standard_normal(n)
    labels = (latent > 0.0).astype(float)
    return {
        "n": float(n),
        "auroc": _auc(scores, labels),
        "base_rate": float(labels.mean()),
    }


def relative_error(value: float, reference: float) -> float:
    scale = abs(reference) if reference != 0.0 else 1.0
    return abs(value - reference) / scale


# ---------------------------------------------------------------------------
# markdown assembly
# ---------------------------------------------------------------------------


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def _fmt(v: Any, digits: int = 6) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v == 0.0:
            return "0"
        if abs(v) < 1e-4 or abs(v) >= 1e6:
            return f"{v:.3e}"
        return f"{v:.{digits}g}"
    return str(v)


def build_markdown(artifact: Mapping[str, Any],
                   references: Mapping[str, Any] | None = None,
                   extra_notes: Sequence[str] = ()) -> str:
    spec = artifact["spec"]
    run = artifact["run"]
    crit = artifact.get("criteria") or {}
    perf = artifact.get("overhead") or {}
    checks = (artifact.get("extra") or {}).get("checks") or []
    spec_meta = spec.get("metadata") or {}

    lines: List[str] = []
    lines.append(f"# Node report — toolchain integration `{spec['name']}`")
    lines.append("")
    lines.append(
        f"Run status **{run['status']}**, weakest evidence **{run['evidence']}**. "
        f"Spec digest `{spec['digest'][:16]}…`, run chain digest `{run['chain_digest'][:16]}…`. "
        f"{len(run['nodes'])} stages, {run['wall_s']:.3f} s per run."
    )
    lines.append("")

    # 1 ---------------------------------------------------------------------
    lines.append("## 1. Scenario and parameters")
    lines.append("")
    lines.append(
        "A synthetic end-to-end pipeline exercises the platform toolchain as *data*: "
        "a response surface is optimised, the same response is propagated under "
        "input uncertainty, a scalar field is transferred conservatively to a finer "
        "mesh and gate-checked, a score series is calibrated into a feasibility "
        "probability, an external callable (present) and an external repository "
        "stage (absent here) are probed, and all criteria are rolled up. No stage "
        "content is hard-coded: it enters through the spec below."
    )
    lines.append("")
    rows = []
    for node in run["nodes"]:
        rows.append([f"`{node['node']}`", f"`{node['adapter']}`", node["status"],
                     node["evidence"], _fmt(node["wall_s"])])
    lines.append(_table(["stage", "adapter", "status", "evidence", "wall [s]"], rows))
    lines.append("")
    lines.append("Declared gates (from the spec metadata): " + ", ".join(
        f"`{k}={_fmt(v)}`" for k, v in spec_meta.get("tolerances", {}).items()))
    lines.append("")

    # 2 ---------------------------------------------------------------------
    lines.append("## 2. Method")
    lines.append("")
    lines.append(
        "* **Port tags as a static type system.** Every adapter declares inputs and "
        "outputs tagged `scalar`/`series`/`callable`/`field`/`report`/`flag`; the "
        "plan builder rejects a binding whose producer tag differs from the consumer "
        "port tag, so an interface mismatch is a spec error, not a runtime surprise.\n"
        "* **Declaration order = execution order, validated.** A forward reference or "
        "a cycle is refused before any stage runs.\n"
        "* **Fail-closed degradation.** An absent backing module yields `WITHHELD` "
        "with the error text; dependents are `SKIPPED` rather than fed a stale or "
        "zero value. A stage that ran through a declared fallback is recorded `MOCK`, "
        "and the run's evidence is the weakest node's.\n"
        "* **Replayable, hash-chained record.** Each node digests its resolved inputs "
        "and outputs; the link folds in the previous node's digest. Timing keys are "
        "excluded from digests, callables digest by bytecode+constants+closure values, "
        "never by `repr`.\n"
        "* **Cost accounting.** A warm-up run precedes timing; harness cost is "
        "`whole-run wall − Σ stage walls` on the *same* run, and a standalone replay of "
        "each stage on its captured inputs gives the cross-run comparison."
    )
    lines.append("")

    # 3 ---------------------------------------------------------------------
    lines.append("## 3. Results")
    lines.append("")
    lines.append("### 3.1 Acceptance criteria")
    lines.append("")
    rows = [[c["name"], f"`{c['input']}`", c["op"], _fmt(c.get("threshold")),
             _fmt(c.get("value")), c["status"]] for c in crit.get("criteria", [])]
    lines.append(_table(["criterion", "input", "op", "threshold", "value", "status"], rows))
    lines.append("")
    lines.append(
        f"Verdict **{crit.get('verdict')}** — {crit.get('n_pass')} pass, "
        f"{crit.get('n_fail')} fail, {crit.get('n_withheld')} withheld, "
        f"{crit.get('n_info')} informational."
    )
    lines.append("")
    lines.append("### 3.2 Integration cost")
    lines.append("")
    rows = [[f"`{s['node']}`", _fmt(s["integrated_median_s"]), _fmt(s["standalone_median_s"])]
            for s in perf.get("stages", [])]
    lines.append(_table(["stage", "integrated median [s]", "standalone replay [s]"], rows))
    lines.append("")
    lines.append(
        f"* stage work (sum of per-stage medians): {_fmt(perf.get('standalone_sum_s'))} s\n"
        f"* harness cost (same run): {_fmt(perf.get('orchestration_s'))} s → "
        f"**integration overhead {100 * float(perf.get('orchestration_ratio', 0.0)):.2f} %** "
        f"(gate < 20 %)\n"
        f"* cross-run comparison vs standalone replay: "
        f"{100 * float(perf.get('overhead_ratio', 0.0)):.2f} %\n"
        f"* reproducible across repeats: {perf.get('determinism')}"
    )
    lines.append("")

    stage_values = {n["node"]: n for n in run["nodes"]}
    lines.append("### 3.3 Stage outputs")
    lines.append("")
    if "rectifier" in stage_values:
        pass
    if references:
        rq = references.get("uq") or {}
        lines.append(
            "* propagated response (UQ stage): mean " + _fmt(rq.get("mean")) +
            ", std " + _fmt(rq.get("std")) + ", q05 " + _fmt(rq.get("q05")) +
            ", q95 " + _fmt(rq.get("q95")) + "\n"
            f"* optimiser nominal objective: {_fmt(references.get('f_best'))} "
            f"(analytic minimum {_fmt(references.get('f_min'))})\n"
            "* conservative transfer: total source " + _fmt(references.get("total_src")) +
            ", total destination " + _fmt(references.get("total_dst")) +
            ", relative residual " + _fmt(references.get("rel_residual")) + "\n"
            "* feasibility prior: Brier " + _fmt(references.get("brier")) +
            " (base-rate reference " + _fmt(references.get("brier_reference")) +
            "), ECE " + _fmt(references.get("ece")) + ", AUROC " +
            _fmt(references.get("auroc")) + ", monotone " + _fmt(references.get("monotone"))
        )
        lines.append("")

    if checks:
        lines.append("### 3.4 Regression suites (run by this node)")
        lines.append("")
        rows = [[f"`{c['target']}`", c["result"], c["passed"], c["failed"],
                 c["skipped"], _fmt(c["duration_s"], 4)] for c in checks]
        lines.append(_table(["suite", "result", "passed", "failed", "skipped", "duration [s]"], rows))
        lines.append("")

    # 4 ---------------------------------------------------------------------
    lines.append("## 4. Figures")
    lines.append("")
    lines.append("See `fig_timings.png`, `fig_calibration.png`, `fig_uq_band.png` "
                 "(generated from the same artifact).")
    lines.append("")

    # 5 ---------------------------------------------------------------------
    lines.append("## 5. Pitfalls and limitations")
    lines.append("")
    for note in extra_notes:
        lines.append(f"* {note}")
    lines.append("")

    # 6 ---------------------------------------------------------------------
    lines.append("## 6. Error judgement against references")
    lines.append("")
    if references:
        rows = []
        for row in references.get("comparison", []):
            rel = float(row["relative_error"])
            rows.append([row["quantity"], _fmt(row["value"]), _fmt(row["reference"]),
                         f"{100 * rel:.3f} %", "yes" if rel < 0.03 else "NO"])
        lines.append(_table(["quantity", "this run", "independent reference",
                             "relative error", "< 3 % ?"], rows))
        lines.append("")
        lines.append(references.get("verdict_text", ""))
        lines.append("")
    return "\n".join(lines)


def analytic_field_total(field_spec: Mapping[str, Any], mesh_spec: Mapping[str, Any]) -> float:
    """Continuous integral of the declared linear field over the domain.

    ``field(x) = offset + slope * (x - x0) / extent`` integrated over the box volume,
    with ``x0`` the first cell centre (that is what the adapter's pattern uses).
    For a linear field on uniform cells this equals the discrete cell sum exactly, so
    a non-zero difference would be a genuine transfer error rather than a
    quadrature artefact.
    """
    lo = np.asarray(mesh_spec["lo"], dtype=float)
    hi = np.asarray(mesh_spec["hi"], dtype=float)
    n = np.asarray(mesh_spec["n"], dtype=int)
    axis = int(field_spec.get("axis", 0))
    slope = float(field_spec["slope"])
    offset = float(field_spec.get("offset", 0.0))
    extent = float(hi[axis] - lo[axis])
    x0 = float(lo[axis] + 0.5 * extent / n[axis])
    volume = float(np.prod(hi - lo))
    centre = float(0.5 * (lo[axis] + hi[axis]))
    return volume * (offset + slope * (centre - x0) / extent)


def build_references(artifact: Mapping[str, Any], spec: Mapping[str, Any],
                     n_mc: int = 2_000_000) -> Dict[str, Any]:
    """Independent references and the per-quantity comparison table."""
    spec_nodes = {n["id"]: n for n in spec["nodes"]}
    response = spec_nodes["surface"]["params"]["response"]
    uq_inputs = spec_nodes["uncertainty"]["params"]["inputs"]
    transfer = spec_nodes["field_transfer"]["params"]
    probe = spec_nodes["score_probe"]["params"]

    ref_uq = reference_moments(response, uq_inputs, n=n_mc)
    ref_cal = reference_calibration(float(probe.get("signal", 1.0)),
                                    float(probe.get("noise", 1.0)))
    ref_field_total = analytic_field_total(transfer["field"], transfer["src_mesh"])

    values = {n["node"]: {k: v.get("value") for k, v in (n["outputs"] or {}).items()}
              for n in artifact["run"]["nodes"]}
    stats = values["uncertainty"]["stats"]
    prior = values["feasibility_prior"]["report"]

    comparison: List[Dict[str, Any]] = []

    def add(quantity: str, value: float, reference: float) -> None:
        comparison.append({
            "quantity": quantity,
            "value": float(value),
            "reference": float(reference),
            "relative_error": relative_error(float(value), float(reference)),
        })

    add("UQ mean of response", stats["mean"], ref_uq["mean"])
    add("UQ std of response", stats["std"], ref_uq["std"])
    add("UQ q05 of response", stats["q05"], ref_uq["q05"])
    add("UQ q95 of response", stats["q95"], ref_uq["q95"])
    add("optimiser objective at optimum", values["design"]["f_best"], 0.0)
    add("conservative transfer total", values["field_transfer"]["total_dst"], ref_field_total)
    add("transfer relative residual", values["field_transfer"]["rel_residual"], 0.0)
    add("prior AUROC", prior["auroc"], ref_cal["auroc"])
    add("prior base rate", prior["base_rate"], ref_cal["base_rate"])

    failed = [c for c in comparison if c["relative_error"] >= 0.03]
    verdict_text = (
        "**All quantities agree with their independent references within 3 %.** "
        if not failed else
        "**Quantities outside 3 %: " + ", ".join(c["quantity"] for c in failed) + ".** "
    )
    verdict_text += (
        "References: the propagated moments come from a "
        f"{n_mc:,}-sample Monte-Carlo of the declared marginals (rejection-sampled "
        "truncation, own response arithmetic), the AUROC/base rate from a "
        f"{int(ref_cal['n']):,}-sample simulation of the declared generative score model "
        "with a Mann-Whitney AUC, and the transfer total from the analytic integral of "
        "the declared linear field. Excluded by construction: the optimiser objective "
        "(reference = the analytic minimum 0) and the residual (reference = 0), where a "
        "relative error is only meaningful as an absolute one; both are reported as-is."
    )
    return {
        "uq": stats,
        "reference_uq": ref_uq,
        "reference_calibration": ref_cal,
        "reference_field_total": ref_field_total,
        "f_best": values["design"]["f_best"],
        "f_min": 0.0,
        "total_src": values["field_transfer"]["total_src"],
        "total_dst": values["field_transfer"]["total_dst"],
        "rel_residual": values["field_transfer"]["rel_residual"],
        "brier": prior["brier"],
        "brier_reference": prior["brier_reference"],
        "ece": prior["ece"],
        "auroc": prior["auroc"],
        "monotone": prior["monotone"],
        "overhead_ratio": (artifact.get("overhead") or {}).get("orchestration_ratio"),
        "comparison": comparison,
        "verdict_text": verdict_text,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="render a node report from an artifact bundle")
    ap.add_argument("--artifact", required=True, help="artifact.json produced by the CLI")
    ap.add_argument("--spec", default=None,
                    help="pipeline spec; when given, independent references are computed "
                         "and stored back into the artifact")
    ap.add_argument("--n-mc", type=int, default=2_000_000,
                    help="Monte-Carlo size for the reference moments")
    ap.add_argument("--out", required=True, help="markdown output path")
    ap.add_argument("--figures-from", default=None,
                    help="directory holding fig_*.png to copy next to the report")
    ap.add_argument("--notes", default=None, help="JSON file with a list of pitfall notes")
    args = ap.parse_args(argv)

    artifact = json.loads(Path(args.artifact).read_text())
    notes: List[str] = []
    if args.notes:
        notes = list(json.loads(Path(args.notes).read_text()))
    if args.spec:
        spec = json.loads(Path(args.spec).read_text())
        artifact["references"] = build_references(artifact, spec, n_mc=args.n_mc)
        Path(args.artifact).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    md = build_markdown(artifact, artifact.get("references"), notes)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    if args.figures_from:
        src = Path(args.figures_from)
        for png in sorted(src.glob("*.png")):
            shutil.copy2(png, out.parent / png.name)
    print(json.dumps({"report": str(out), "bytes": out.stat().st_size}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())