"""CLI：生成依赖图谱快照、跑三条预设级联链、落验收判据报告与图。

    python -m tools.capability_graph audit --repo-root .            # 全量判据 + 工件
    python -m tools.capability_graph simulate --capability <id> --to-level MOCK
    python -m tools.capability_graph show

产物（默认 ``artifacts/capability_graph/``）：
    graph_snapshot.json        图快照（节点/边/等级/来源）
    cascade_acceptance.json    判据数字（Jaccard / 人工误差 / 延迟 / 扫描 / pytest 计数）
    alerts.jsonl               三条链模拟产生的预警（append 前会清空重建）
    figures/*.png              依赖图 + 级联衰减图
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .alerts import AlertEngine
from .builder import GraphConfig, build_snapshot, load_json
from .cascade import CascadeEvent, simulate
from .checks import (
    compare_cascade_with_manual,
    compare_edge_sets,
    mutation_sensitivity,
    scan_forbidden_tokens,
)

CONFIG_REL = Path("config/capability_graph.json")


def load_config(repo_root: Path) -> GraphConfig:
    return GraphConfig.from_file(repo_root / CONFIG_REL, repo_root)


# ── 级联链评估 ───────────────────────────────────────────────
def evaluate_chains(snapshot, cfg: GraphConfig, manual: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for chain in manual.get("chains") or []:
        result = simulate(
            snapshot,
            CascadeEvent(capability_id=chain["capability_id"], to_level=chain["to_level"]),
            cfg,
        )
        cmp_ = compare_cascade_with_manual(result.to_dict(), chain)
        expected_ids = set(chain.get("expected_affected_ids") or [])
        got_ids = set(result.affected_ids)
        cmp_["expected_affected_ids_match"] = expected_ids == got_ids
        cmp_["unexpected_affected"] = sorted(got_ids - expected_ids)
        cmp_["missing_affected"] = sorted(expected_ids - got_ids)
        cmp_["within_error_budget"] = cmp_["max_rel_error_pct"] <= float(
            cfg.acceptance.get("cascade_manual_error_max_pct", 10.0)
        )
        cmp_["severity"] = result.severity
        out.append(cmp_)
    return out


def measure_alert_latency(snapshot, cfg: GraphConfig, manual: Dict[str, Any], history: Path) -> Dict[str, Any]:
    if history.exists():
        history.unlink()
    engine = AlertEngine(snapshot, cfg, history_path=history)
    chains = manual.get("chains") or []
    for chain in chains:
        engine.apply_level_change(chain["capability_id"], chain["to_level"], persist=True)
    # 重复施加同一变化（稳态路径：缓存热了之后的延迟）
    for _ in range(3):
        for chain in chains:
            engine.apply_level_change(chain["capability_id"], chain["to_level"], persist=False)
    summary = engine.summary()
    summary["first_call_latency_ms"] = round(engine.alerts[0].latency_ms, 4) if engine.alerts else None
    summary["budget_ok"] = summary["max_latency_ms"] <= float(
        cfg.acceptance.get("alert_latency_max_ms", 1000.0)
    )
    return summary


def measure_api_latency(repo_root: Path, cfg: GraphConfig, snapshot, repeats: int = 5) -> Dict[str, Any]:
    """API 端点稳态响应时间（同进程内直接调路由函数，排除网络/鉴权噪声）。"""
    sys.path.insert(0, str(repo_root / "backend"))
    try:
        from api.capability_graph import build_engine  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境相关
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    engine = build_engine(repo_root=repo_root, cfg=cfg, snapshot=snapshot)
    timings: List[float] = []
    for i in range(repeats):
        t0 = time.perf_counter()
        payload = engine.snapshot_payload()
        timings.append((time.perf_counter() - t0) * 1000.0)
        if i == 0:
            payload_size = len(json.dumps(payload))
    budget = float(cfg.acceptance.get("api_response_max_ms", 500.0))
    return {
        "available": True,
        "repeats": repeats,
        "timings_ms": [round(t, 4) for t in timings],
        "max_ms": round(max(timings), 4),
        "mean_ms": round(sum(timings) / len(timings), 4),
        "payload_bytes": payload_size,
        "budget_ms": budget,
        "budget_ok": max(timings) <= budget,
    }


# ── 图 ──────────────────────────────────────────────────────
def _depths(snapshot) -> Dict[str, int]:
    deps = {n: snapshot.dependencies(n) for n in snapshot.nodes}
    memo: Dict[str, int] = {}

    def depth(n: str, seen: frozenset) -> int:
        if n in memo:
            return memo[n]
        if n in seen:
            return 0
        ups = [d for d in deps.get(n, []) if d in snapshot.nodes]
        val = 0 if not ups else 1 + max(depth(d, seen | {n}) for d in ups)
        memo[n] = val
        return val

    for n in snapshot.nodes:
        depth(n, frozenset())
    return memo


def render_figures(snapshot, cfg: GraphConfig, chains: List[Dict[str, Any]], out_dir: Path) -> List[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []

    # 图 1：依赖图（按依赖深度分列布局）
    depth = _depths(snapshot)
    columns: Dict[int, List[str]] = {}
    for n in sorted(snapshot.nodes):
        columns.setdefault(depth.get(n, 0), []).append(n)
    pos: Dict[str, tuple] = {}
    for col, nodes in columns.items():
        for row, n in enumerate(nodes):
            pos[n] = (col * 2.2, -row * 1.0)
    fig, ax = plt.subplots(figsize=(14, max(4.5, 0.55 * len(snapshot.nodes))))
    colour = {"REAL": "#2e7d32", "WITHHELD": "#f9a825", "MOCK": "#ef6c00",
              "STUB": "#6a1b9a", "NOT_SUPPORTED": "#b71c1c"}
    for e in snapshot.edges:
        if e.src not in pos or e.dst not in pos:
            continue
        x1, y1 = pos[e.src]
        x2, y2 = pos[e.dst]
        ax.annotate(
            "", xy=(x2, y2), xytext=(x1, y1),
            arrowprops={"arrowstyle": "->", "color": "#90a4ae", "lw": 1.1 + 1.4 * e.weight,
                        "shrinkA": 18, "shrinkB": 18},
        )
    for n, (x, y) in pos.items():
        node = snapshot.nodes[n]
        ax.scatter([x], [y], s=900, color=colour.get(node.own_level, "#607d8b"), zorder=3,
                   edgecolors="white", linewidths=1.5)
        ax.text(x, y - 0.02, node.own_level[:4], ha="center", va="center", color="white",
                fontsize=6.5, zorder=4, fontweight="bold")
        ax.text(x, y - 0.30, n, ha="center", va="top", fontsize=7, color="#263238")
    ax.set_title(
        f"capability dependency graph — {len(snapshot.nodes)} nodes / {len(snapshot.edges)} edges "
        f"(max edge weight {max((e.weight for e in snapshot.edges), default=0):.2f})",
        fontsize=11,
    )
    ax.axis("off")
    fig.tight_layout()
    p1 = out_dir / "dependency_graph.png"
    fig.savefig(p1, dpi=160)
    plt.close(fig)
    paths.append(p1)

    # 图 2：三条级联链的逐节点置信度衰减
    fig, axes = plt.subplots(1, len(chains), figsize=(5.2 * max(1, len(chains)), 4.6))
    if len(chains) == 1:
        axes = [axes]  # type: ignore[assignment]
    for ax, chain in zip(axes, chains):
        rows = [r for r in chain["rows"] if r["affected_got"] or r["affected_expected"]]
        rows.sort(key=lambda r: (r["degradation_got"], r["id"]))
        labels = [r["id"] for r in rows]
        before = [r["confidence_after_expected"] / max(1e-9, 1 - r["degradation_got"]) if r["degradation_got"] < 1 else 0.0 for r in rows]
        ax.barh([f"{l}" for l in labels], [r["confidence_after_expected"] for r in rows],
                color="#42a5f5", alpha=0.85, label="after cascade (hand-checked)")
        ax.scatter([r["confidence_after_got"] for r in rows], range(len(rows)),
                   color="#c62828", zorder=3, s=36, label="engine")
        ax.set_xlim(0, 1.05)
        ax.set_title(f"{chain['chain_id']}\nmax rel err {chain['max_rel_error_pct']:.3g}%",
                     fontsize=9)
        ax.set_xlabel("effective confidence")
        ax.legend(fontsize=7, loc="lower right")
    fig.suptitle("cascade degradation: engine vs manual analysis", fontsize=11)
    fig.tight_layout()
    p2 = out_dir / "cascade_confidence.png"
    fig.savefig(p2, dpi=160)
    plt.close(fig)
    paths.append(p2)
    return paths


# ── 子命令 ──────────────────────────────────────────────────
def run_audit(repo_root: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    cfg = load_config(repo_root)
    out = out_dir or (repo_root / str(cfg.sources.get("artifact_dir") or "artifacts/capability_graph"))
    out.mkdir(parents=True, exist_ok=True)
    t_build0 = time.perf_counter()
    snapshot = build_snapshot(cfg)
    build_ms = (time.perf_counter() - t_build0) * 1000.0

    baseline = load_json(cfg.sources["baseline_edges"])
    manual = load_json(cfg.sources["manual_chains"])
    edge_cmp = compare_edge_sets(snapshot.edges, baseline["edges"])
    declared_only = compare_edge_sets(
        [e for e in snapshot.edges if "declared" in e.sources], baseline["edges"]
    )
    chains = evaluate_chains(snapshot, cfg, manual)
    latency = measure_alert_latency(snapshot, cfg, manual, out / "alerts.jsonl")
    api_lat = measure_api_latency(repo_root, cfg, snapshot)

    acc = cfg.acceptance
    forbidden = cfg.raw.get("forbidden_tokens_scan") or {}
    scan = scan_forbidden_tokens(
        [repo_root / p for p in forbidden.get("paths", [])],
        forbidden.get("forbidden_tokens", []),
    )
    snap_path = out / "graph_snapshot.json"
    snap_path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    figures = render_figures(snapshot, cfg, chains, out / "figures")

    criteria = {
        "graph_jaccard_vs_baseline": {
            "value": edge_cmp["jaccard"],
            "threshold": float(acc.get("jaccard_min", 0.8)),
            "pass": edge_cmp["jaccard"] >= float(acc.get("jaccard_min", 0.8)),
            "baseline_edges": edge_cmp["baseline_count"],
            "baseline_edges_min": int(acc.get("baseline_edges_min", 10)),
            "baseline_edges_ok": edge_cmp["baseline_count"] >= int(acc.get("baseline_edges_min", 10)),
            "detail": edge_cmp,
            "declared_only_jaccard": declared_only["jaccard"],
            "sensitivity": mutation_sensitivity(snapshot.edges, baseline["edges"]),
        },
        "cascade_manual_agreement": {
            "max_rel_error_pct": max((c["max_rel_error_pct"] for c in chains), default=0.0),
            "threshold_pct": float(acc.get("cascade_manual_error_max_pct", 10.0)),
            "pass": all(c["within_error_budget"] and c["expected_affected_ids_match"] for c in chains),
            "chains": chains,
        },
        "alert_latency": {
            "max_ms": latency["max_latency_ms"],
            "threshold_ms": float(acc.get("alert_latency_max_ms", 1000.0)),
            "pass": latency["budget_ok"],
            "detail": latency,
        },
        "api_response": {
            "max_ms": api_lat.get("max_ms"),
            "threshold_ms": float(acc.get("api_response_max_ms", 500.0)),
            "pass": bool(api_lat.get("budget_ok")),
            "detail": api_lat,
        },
        "zero_domain_semantics": {
            "hit_count": scan["hit_count"],
            "scanned_files": scan["scanned_files"],
            "pass": scan["clean"],
            "hits": scan["hits"],
        },
    }
    report = {
        "schema_version": "1.0",
        "mission": "m12438",
        "node": "p285w2b-n03-capability-dependency-graph",
        "generated_at": snapshot.meta.get("generated_at"),
        "environment": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "build_ms": round(build_ms, 3),
        "graph": snapshot.summary(),
        "edge_comparison": edge_cmp,
        "criteria": criteria,
        "all_pass": all(v["pass"] for v in criteria.values()),
        "artifacts": {
            "snapshot": str(snap_path.relative_to(repo_root)),
            "acceptance": str((out / "cascade_acceptance.json").relative_to(repo_root)),
            "alerts": str((out / "alerts.jsonl").relative_to(repo_root)),
            "figures": [str(p.relative_to(repo_root)) for p in figures],
        },
    }
    (out / "cascade_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.capability_graph")
    parser.add_argument("--repo-root", default=".")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_audit = sub.add_parser("audit", help="全量判据 + 工件")
    p_audit.add_argument("--out")
    p_sim = sub.add_parser("simulate", help="单次事件级联")
    p_sim.add_argument("--capability", required=True)
    p_sim.add_argument("--to-level", required=True)
    p_sim.add_argument("--json", action="store_true")
    sub.add_parser("show", help="打印图摘要")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if args.cmd == "audit":
        report = run_audit(repo_root, Path(args.out) if args.out else None)
        print(json.dumps({k: {"pass": v["pass"]} for k, v in report["criteria"].items()},
                         ensure_ascii=False, indent=2))
        print("all_pass:", report["all_pass"])
        return 0 if report["all_pass"] else 1
    cfg = load_config(repo_root)
    snapshot = build_snapshot(cfg)
    if args.cmd == "show":
        print(json.dumps(snapshot.summary(), ensure_ascii=False, indent=2))
        return 0
    result = simulate(snapshot, CascadeEvent(args.capability, args.to_level), cfg)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"{args.capability}: {result.from_level} -> {result.to_level}  severity={result.severity}")
    for a in result.affected:
        print(f"  {a['id']:<28} deg={a['degradation']:.4f} "
              f"{a['level_before']}->{a['level_after']} hop={a['hop']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())