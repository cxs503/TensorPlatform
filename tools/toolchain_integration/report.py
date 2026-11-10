"""Reporting: artifact JSON, dependency graph, figures, markdown.

Everything the report states is *derived from the run objects* -- no number is
retyped.  The artifact JSON is the machine-readable evidence (digests, timings,
criteria, evidence levels); the markdown and figures are views of the same data,
so a reader can trace any printed value back to the artifact field it came from.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import provenance
from .dag import PipelineRun
from .perf import OverheadReport
from .schema import PipelineSpec

TOOLCHAIN_SCHEMA = "tp.toolchain.integration/v1"


# ---------------------------------------------------------------------------
# structural views
# ---------------------------------------------------------------------------


def dependency_graph(spec: PipelineSpec) -> Dict[str, Any]:
    """Nodes, edges and adapter contracts as plain data."""
    from .registry import get as get_adapter

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    for node in spec.nodes:
        adapter = get_adapter(node.adapter)
        nodes.append({
            "id": node.node_id,
            "adapter": adapter.kind,
            "evidence": adapter.evidence,
            "implementation": adapter.implementation,
            "inputs": [p.to_dict() for p in adapter.inputs],
            "outputs": [p.to_dict() for p in adapter.outputs],
        })
        for port, binding in node.inputs.items():
            if binding.kind == "ref":
                edges.append({
                    "from": f"{binding.node}.{binding.port}",
                    "to": f"{node.node_id}.{port}",
                })
    return {"nodes": nodes, "edges": edges}


def render_dependency_graph_md(spec: PipelineSpec) -> str:
    graph = dependency_graph(spec)
    lines = [f"# Module dependency graph — `{spec.name}`", ""]
    lines.append("```mermaid")
    lines.append("flowchart LR")
    for n in graph["nodes"]:
        lines.append(f"  {n['id']}[\"{n['id']}<br/>{n['adapter']}\"]")
    for e in graph["edges"]:
        src = e["from"].split(".")[0]
        dst = e["to"].split(".")[0]
        lines.append(f"  {src} -->|{e['from'].split('.', 1)[1]}| {dst}")
    lines.append("```")
    lines.append("")
    lines.append("| node | adapter | evidence | backing implementation |")
    lines.append("|---|---|---|---|")
    for n in graph["nodes"]:
        lines.append(
            f"| `{n['id']}` | `{n['adapter']}` | {n['evidence']} | `{n['implementation']}` |"
        )
    lines.append("")
    lines.append("Ports (tag = coarse type checked before execution):")
    lines.append("")
    lines.append("| node | inputs | outputs |")
    lines.append("|---|---|---|")
    for n in graph["nodes"]:
        ins = ", ".join(f"{p['name']}:{p['tag']}{'' if p['required'] else '?'}" for p in n["inputs"]) or "—"
        outs = ", ".join(f"{p['name']}:{p['tag']}" for p in n["outputs"]) or "—"
        lines.append(f"| `{n['id']}` | {ins} | {outs} |")
    lines.append("")
    return "\n".join(lines)


def render_api_md() -> str:
    from .registry import catalogue

    cat = catalogue()
    lines = ["# Adapter API", "",
             "Every adapter is a pure function of `(params, inputs)`; no adapter keeps "
             "state between runs, so a stage can be replayed and re-timed in isolation.", ""]
    for kind, spec in cat.items():
        lines.append(f"## `{kind}` — {spec['summary']}")
        lines.append("")
        lines.append(f"- evidence: **{spec['evidence']}**")
        lines.append(f"- implementation: `{spec['implementation']}`")
        lines.append(f"- accepts arbitrary input ports: `{spec['accepts_any_inputs']}`")
        lines.append("")
        lines.append("| direction | port | tag | required |")
        lines.append("|---|---|---|---|")
        for p in spec["inputs"]:
            lines.append(f"| in | `{p['name']}` | `{p['tag']}` | {p['required']} |")
        for p in spec["outputs"]:
            lines.append(f"| out | `{p['name']}` | `{p['tag']}` | {p['required']} |")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# artifact
# ---------------------------------------------------------------------------


def build_artifact(
    spec: PipelineSpec,
    run: PipelineRun,
    overhead: Optional[OverheadReport] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    rollup = None
    if "rollup" in run.values:
        rollup = run.values["rollup"]["status"].value
    return {
        "schema": TOOLCHAIN_SCHEMA,
        "spec": {
            "name": spec.name,
            "digest": run.spec_digest,
            "n_nodes": len(spec.nodes),
            "metadata": spec.metadata,
        },
        "run": run.to_dict(),
        "criteria": rollup or {},
        "dependency_graph": dependency_graph(spec),
        "overhead": overhead.to_dict() if overhead is not None else None,
        "provenance": {
            "chain_digest": run.chain_digest,
            "node_chain": {r.node_id: r.chain_digest for r in run.records},
            "input_digests": {r.node_id: r.input_digest for r in run.records},
        },
        "extra": dict(extra or {}),
    }


def write_json(path: str | Path, data: Mapping[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def figure_timings(overhead: OverheadReport, path: str | Path) -> Path:
    plt = _plt()
    names = [s.node_id for s in overhead.stages]
    solo = [s.standalone_median_s * 1e3 for s in overhead.stages]
    integ = [s.integrated_median_s * 1e3 for s in overhead.stages]

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.4))
    ax = axes[0]
    idx = range(len(names))
    ax.bar([i - 0.2 for i in idx], integ, width=0.4, label="integrated (in DAG)")
    ax.bar([i + 0.2 for i in idx], solo, width=0.4, label="standalone replay")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("median wall time [ms]")
    ax.set_title("Per-stage cost: integrated vs standalone")
    ax.legend(fontsize=8)

    ax = axes[1]
    labels = ["stage work", "harness", "total"]
    stage_ms = overhead.node_sum_median_s * 1e3
    orch_ms = overhead.orchestration_s * 1e3
    ax.bar(labels, [stage_ms, orch_ms, stage_ms + orch_ms],
           color=["#3b6ea5", "#d1834a", "#8c8c8c"])
    ax.set_ylabel("median wall time [ms]")
    ax.set_title(
        f"Integration overhead = {100 * overhead.orchestration_ratio:.2f}% "
        f"(gate: < 20%)"
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def figure_calibration(run: PipelineRun, path: str | Path) -> Path:
    plt = _plt()
    rep = run.value("feasibility_prior.report").value
    bins = rep["bins"]
    pred = [b["predicted"] for b in bins]
    obs = [b["observed"] for b in bins]
    cnt = [b["count"] for b in bins]

    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
    ax.plot(pred, obs, "o-", color="#3b6ea5", label="fitted prior (bins)")
    ax.set_xlabel("predicted feasibility probability")
    ax.set_ylabel("observed feasible fraction")
    ax.set_title(
        f"Prior calibration: Brier={rep['brier']:.4f} "
        f"(base {rep['brier_reference']:.4f}), ECE={rep['ece']:.4f}, "
        f"AUROC={rep['auroc']:.3f}, n={rep['n']}"
    )
    for x, y, c in zip(pred, obs, cnt):
        ax.annotate(f"{int(c)}", (x, y), textcoords="offset points", xytext=(4, -8), fontsize=7)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def figure_uq_band(run: PipelineRun, path: str | Path) -> Path:
    plt = _plt()
    stats = run.value("uncertainty.stats").value
    nominal = run.value("uncertainty.nominal").value
    labels = ["q05", "q50", "q95"]
    est = [stats["q05"], stats["q50"], stats["q95"]]
    s = stats["std"]
    # bootstrap / order-statistic 95 % widths, taken from the same run record
    widths = [0.0, stats["ci_width_bootstrap_q95"], 0.0]
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    ax.errorbar(range(3), est, yerr=[[0, 0, 0], widths], fmt="s", color="#3b6ea5",
                capsize=5, label="propagated quantile (95 % CI)")
    ax.axhline(nominal, color="#c0392b", lw=1.2,
               label=f"optimiser nominal = {nominal:.3e}")
    ax.axhline(stats["mean"], color="#2e7d32", ls=":", lw=1.1,
               label=f"ensemble mean = {stats['mean']:.3e}")
    ax.set_xticks(range(3))
    ax.set_xticklabels(labels)
    ax.set_ylabel("response value")
    ax.set_title(
        f"UQ band around the design point (n={int(stats['n_evals'])}, "
        f"std={s:.3e})"
    )
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def png_base64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "PASS" if v else "FAIL"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def render_report_md(
    spec: PipelineSpec,
    run: PipelineRun,
    overhead: Optional[OverheadReport],
    extra_checks: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    lines = [f"# Toolchain integration run — `{spec.name}`", ""]
    lines.append(f"- spec digest: `{run.spec_digest}`")
    lines.append(f"- run chain digest: `{run.chain_digest}`")
    lines.append(f"- run status: **{run.status}**, weakest evidence: **{run.evidence}**")
    lines.append(f"- total wall: {run.wall_s:.3f} s over {len(run.records)} nodes")
    lines.append("")
    lines.append("## Stage table")
    lines.append("")
    lines.append("| # | node | adapter | status | evidence | wall [s] | notes |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(run.records, 1):
        note = r.error or "; ".join(r.notes)
        lines.append(
            f"| {i} | `{r.node_id}` | `{r.adapter}` | {r.status} | {r.evidence} | "
            f"{r.wall_s:.4f} | {note} |"
        )
    lines.append("")
    rollup = run.values.get("rollup")
    if rollup:
        st = rollup["status"].value
        lines.append("## Acceptance criteria")
        lines.append("")
        lines.append(
            f"verdict **{st['verdict']}** — {st['n_pass']} pass / {st['n_fail']} fail / "
            f"{st['n_withheld']} withheld / {st['n_info']} informational"
        )
        lines.append("")
        lines.append("| criterion | input | op | threshold | value | status |")
        lines.append("|---|---|---|---|---|---|")
        for c in st["criteria"]:
            lines.append(
                f"| {c['name']} | `{c['input']}` | {c['op']} | "
                f"{_fmt(c.get('threshold'))} | {_fmt(c.get('value'))} | {c['status']} |"
            )
        lines.append("")
    if overhead is not None:
        lines.append("## Integration cost")
        lines.append("")
        lines.append(
            f"- stage work (median of per-stage medians): {overhead.node_sum_median_s:.4f} s"
        )
        lines.append(f"- harness cost (same run totals minus stage walls): {overhead.orchestration_s:.4f} s")
        lines.append(
            f"- **integration overhead = {100 * overhead.orchestration_ratio:.2f} %** of stage work"
        )
        lines.append(
            f"- cross-run comparison vs standalone replay: {100 * overhead.overhead_ratio:.2f} %"
        )
        lines.append(f"- reproducible across repeats: {overhead.determinism}")
        lines.append("")
    if extra_checks:
        lines.append("## Regression checks")
        lines.append("")
        lines.append("| check | result | detail |")
        lines.append("|---|---|---|")
        for c in extra_checks:
            lines.append(f"| {c.get('name')} | {c.get('result')} | {c.get('detail', '')} |")
        lines.append("")
    return "\n".join(lines)


def write_bundle(
    out_dir: str | Path,
    spec: PipelineSpec,
    run: PipelineRun,
    overhead: Optional[OverheadReport] = None,
    extra_checks: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Path]:
    """Write artifact.json + markdown + graph + figures into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    paths["artifact"] = write_json(out / "artifact.json",
                                   build_artifact(spec, run, overhead,
                                                  extra={"checks": list(extra_checks or [])}))
    paths["report"] = out / "REPORT.md"
    paths["report"].write_text(render_report_md(spec, run, overhead, extra_checks), encoding="utf-8")
    paths["dependencies"] = out / "DEPENDENCY_GRAPH.md"
    paths["dependencies"].write_text(render_dependency_graph_md(spec), encoding="utf-8")
    paths["api"] = out / "API.md"
    paths["api"].write_text(render_api_md(), encoding="utf-8")
    if overhead is not None:
        paths["fig_timings"] = figure_timings(overhead, out / "fig_timings.png")
    if "feasibility_prior" in run.values:
        paths["fig_calibration"] = figure_calibration(run, out / "fig_calibration.png")
    if "uncertainty" in run.values and "nominal" in run.values["uncertainty"]:
        paths["fig_uq"] = figure_uq_band(run, out / "fig_uq_band.png")
    return paths