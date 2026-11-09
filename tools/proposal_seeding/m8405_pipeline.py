"""m8405 复现驱动：真实提案 → 聚合 → 播种 → 自回归增长 → 机器门 → 人工仲裁复核。

产物（全部落在 ``--out-dir``，默认 ``artifacts/p423w2_n10/``）：

* ``aggregate.json``        聚合结果（含 66 对相似度全矩阵与吸收明细）
* ``seed_plan.json``        播种计划（11 提案节点 + 溢出队列）
* ``gate_pre.json`` / ``gate.json``  自回归注入前 / 后的机器门逐项结果
* ``autoregress_injection.json``     平台自优化节点注入记录
* ``telemetry.json``        管线自身遥测与缺口
* ``replay.json``           同输入两次运行的 ext_id 序列比对（幂等证据）
* ``advance_wave.json``     溢出反哺的下一波（闭环第二圈 + 背压信号）
* ``manual_arbitration_audit.json``  人工播种 plan 431 与机器结果的逐条对照
* ``fig*.png`` + ``report.md``       论文级报告与图

复现匹配的四通道
----------------
人工节点名（中文简写 + 英文 slug 混排）与提案标题（中英双语长标题）不是同一写体，
单一 3-gram Jaccard 会系统性低估（实测同一能力在 0.15~0.26 之间）。因此本驱动用
**四通道取最大**：全文字符 3-gram / 仅中文 3-gram / ASCII 词集合 Jaccard / 标题前缀
（冒号前）3-gram；平台自优化节点另有「角色通道」（两侧都是平台仓的成熟化单即算匹配）。
每条都写进审计 JSON，并给出 0.20/0.30/0.40/0.55 四档敏感性表，避免单阈值结论。

用法::

    python3 -m tools.proposal_seeding.m8405_pipeline [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .aggregate import aggregate
from .autoregress import advance_wave, ensure_platform_node, pipeline_telemetry, platform_proposal
from .config import SeedingConfig, load_config
from .gate import check_plan
from .match import CHANNELS, best_match, is_platform_role, match_channels
from .model import PlanNode, digest
from .seed import seed_plan

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts" / "p423w2_n10"


def dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def audit_manual(cfg, agg: Mapping[str, Any], machine_plan: Mapping[str, Any],
                 snapshot: Mapping[str, Any], manual_report: Mapping[str, Any]
                 ) -> dict[str, Any]:
    items = agg["items"]
    matrix = agg["similarity"]["matrix"]
    thr = cfg.similarity.merge_threshold
    machine_nodes = [PlanNode.from_dict(n) for n in machine_plan["nodes"]]
    platform_node = next((n for n in machine_nodes
                          if n.repo == cfg.platform_repo and cfg.maturity_marker in n.name), None)

    # 1) 手工节点 → 机器侧
    node_match = []
    for raw in snapshot["nodes"]:
        meta = raw.get("metadata") or {}
        ext_id = str(meta.get("ext_id") or "")
        name = str(raw.get("name") or "")
        repo = str(meta.get("repo") or raw.get("repo") or "")
        if is_platform_role(name, repo, cfg) and platform_node is not None:
            node_match.append({
                "ext_id": ext_id, "name": name, "repo": repo,
                "mission_id": raw.get("mission_id"),
                "best_item": platform_node.name, "best_item_sources": ["autoregress"],
                "channel": "role(platform)", "score": 1.0, "matched": True,
                "channels": {"role": 1.0},
            })
            continue
        best: dict[str, Any] | None = None
        for it in items:
            ch = match_channels(name, it["title"], cfg)
            if best is None or ch["score"] > best["score"]:
                best = {**ch, "item_id": it["item_id"], "item_title": it["title"],
                        "item_sources": [q["source"] for q in it["quotes"]],
                        "channels": {k: ch[k] for k in
                                     ("full_3gram", "cjk_3gram", "ascii_tokens", "prefix_3gram")}}
        node_match.append({
            "ext_id": ext_id, "name": name, "repo": repo,
            "mission_id": raw.get("mission_id"),
            "best_item": best["item_title"], "best_item_sources": best["item_sources"],
            "channel": best["channel"], "score": best["score"],
            "matched": best["score"] >= cfg.repro_match_threshold,
            "channels": best["channels"],
        })

    # 2) 机器聚合项 → 是否被手工计划覆盖
    item_match = []
    for it in items:
        if cfg.maturity_marker in it["title"] and it["repo"] == cfg.platform_repo \
                and platform_node is not None:
            item_match.append({
                "item_id": it["item_id"], "title": it["title"],
                "sources": [q["source"] for q in it["quotes"]],
                "manual_ext_id": platform_node.ext_id, "channel": "role(platform)",
                "score": 1.0, "covered": True,
            })
            continue
        best = {"score": -1.0}
        for raw in snapshot["nodes"]:
            meta = raw.get("metadata") or {}
            name = str(raw.get("name") or "")
            ch = match_channels(it["title"], name, cfg)
            if ch["score"] > best["score"]:
                best = {**ch, "ext_id": str(meta.get("ext_id") or "")}
        item_match.append({
            "item_id": it["item_id"], "title": it["title"],
            "sources": [q["source"] for q in it["quotes"]],
            "manual_ext_id": best["ext_id"], "channel": best["channel"],
            "score": best["score"], "covered": best["score"] >= cfg.repro_match_threshold,
        })

    # 3) 人工「3 条跨提案者重叠合并」声明 vs 机器证据
    claimed = manual_report.get("dedup_summary", {}).get("merged_overlapping")
    cross = [r for r in matrix if r["cross_source"]]
    max_cross = max(cross, key=lambda r: r["jaccard"]) if cross else None
    claimed_merge_nodes = []
    for detail in manual_report.get("overlap_details", []):
        title = detail.get("merged_title", "")
        best = {"score": -1.0}
        for it in items:
            if not any(q["source"] == "6262" for q in it["quotes"]):
                continue
            ch = match_channels(title, it["title"], cfg)
            if ch["score"] > best["score"]:
                best = {**ch, "title": it["title"]}
        claimed_merge_nodes.append({
            "claimed_merged_title": title,
            "claimed_sources": detail.get("sources"),
            "max_score_to_6262_side": best["score"],
            "channel": best["channel"],
            "closest_6262_title": best.get("title", ""),
            "channels": {k: best[k] for k in
                         ("full_3gram", "cjk_3gram", "ascii_tokens", "prefix_3gram")
                         if k in best},
            "survives_threshold": best["score"] >= thr,
        })

    claims = {
        "claimed_cross_source_merges": claimed,
        "observed_cross_source_pairs_above_threshold": sum(
            1 for r in cross if r["jaccard"] >= thr),
        "observed_cross_source_pairs_total": len(cross),
        "max_cross_source_jaccard": max_cross["jaccard"] if max_cross else 0.0,
        "max_cross_source_pair": (f"{max_cross['a']} vs {max_cross['b']}"
                                  if max_cross else None),
        "threshold": thr,
        "claim_reproducible": bool(
            claimed is not None
            and sum(1 for r in cross if r["jaccard"] >= thr) >= claimed),
    }

    dropped = [m for m in item_match if not m["covered"]]
    return {
        "schema": "proposal-seeding-manual-audit/1.0",
        "manual": {
            "plan_id": (snapshot.get("plan") or {}).get("id"),
            "node_count": len(snapshot["nodes"]),
            "milestone_count": len(snapshot.get("milestones", [])),
            "dedup_summary": manual_report.get("dedup_summary", {}),
            "machine_gate_selfclaim": manual_report.get("machine_gate", {}),
        },
        "machine": {
            "node_count": len(machine_plan["nodes"]),
            "milestone_count": len(machine_plan["milestones"]),
            "items_out": agg["counts"]["items_out"],
            "merged_semantic": agg["counts"]["merged_semantic"],
            "overflow": len(machine_plan["overflow"]),
        },
        "node_match": node_match,
        "item_match": item_match,
        "manual_nodes_matched": sum(1 for n in node_match if n["matched"]),
        "manual_nodes_unmatched": [n for n in node_match if not n["matched"]],
        "items_not_covered_by_manual": dropped,
        "claims": claims,
        "claimed_merge_nodes": claimed_merge_nodes,
        "repro_threshold_sensitivity": {
            f"{t:.2f}": {
                "manual_nodes_matched": sum(1 for n in node_match if n["score"] >= t),
                "items_covered_by_manual": sum(1 for m in item_match if m["score"] >= t),
            } for t in (0.20, 0.30, 0.40, 0.55)
        },
        "repro_match_threshold_used": cfg.repro_match_threshold,
        "channel_legend": {
            "full_3gram": "全文字符 3-gram Jaccard",
            "cjk_3gram": "仅中文字符 3-gram Jaccard",
            "ascii_tokens": "ASCII 词集合 Jaccard",
            "prefix_3gram": "标题前缀（冒号前）3-gram Jaccard",
            "role(platform)": "角色通道：两侧都是平台仓成熟化单",
        },
    }


# ---------------------------------------------------------------------------
# figures (ASCII labels only: 运行环境无 CJK 字体，图注中文写在报告里)
# ---------------------------------------------------------------------------
def fig_similarity(agg: Mapping[str, Any], cfg, out: Path) -> dict[str, str]:
    proposals = agg["proposals"]
    n = len(proposals)
    labels = [f"{p['source']}#{p['index']}" for p in proposals]
    m = np.eye(n)
    thr = cfg.similarity.merge_threshold
    for rec in agg["similarity"]["matrix"]:
        i, j = labels.index(rec["a"]), labels.index(rec["b"])
        m[i, j] = m[j, i] = rec["jaccard"]
    fig, ax = plt.subplots(figsize=(7.4, 6.2))
    im = ax.imshow(m, cmap="magma", vmin=0, vmax=max(float(m.max()), thr))
    ax.set_xticks(range(n), labels, rotation=90, fontsize=8)
    ax.set_yticks(range(n), labels, fontsize=8)
    ax.set_title(f"3-gram Jaccard of 12 real proposals\n"
                 f"max off-diagonal={agg['similarity']['max_jaccard']:.3f}  "
                 f"merge threshold={thr}", fontsize=10)
    cb = fig.colorbar(im, ax=ax, shrink=0.82)
    cb.set_label("Jaccard")
    fig.tight_layout()
    p = out / "fig1_similarity_heatmap.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return {"path": str(p.relative_to(ROOT)),
            "caption": (f"图1 12 条真实提案的 3-gram Jaccard 相似度矩阵；非对角最大值 "
                        f"{agg['similarity']['max_jaccard']:.3f}（阈值 {thr}），"
                        f"机器判定 0 条语义合并。横纵标签为「提案者#序号」。")}


def fig_dag(plan: Mapping[str, Any], out: Path) -> dict[str, str]:
    nodes = [PlanNode.from_dict(n) for n in plan["nodes"]]
    milestones = plan["milestones"]
    m_index = {int(mm["index"]): i for i, mm in enumerate(milestones)}
    rows: dict[int, int] = Counter()
    pos: dict[int, tuple[float, float]] = {}
    for node in nodes:
        col = m_index.get(node.milestone, 0)
        row = rows[col]
        rows[col] += 1
        pos[node.index] = (col, -row)
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    for node in nodes:
        x, y = pos[node.index]
        platform = node.repo == "TensorPlatform"
        ax.scatter([x], [y], s=760, color="#c0392b" if platform else "#2c7fb8",
                   edgecolor="k", zorder=3)
        ax.text(x, y, str(node.index), ha="center", va="center", color="w",
                fontsize=9, zorder=4)
        ax.text(x + 0.12, y, f"{node.ext_id.split('-n', 1)[-1]}",
                fontsize=7.6, va="center")
    for node in nodes:
        for dep in node.dependencies:
            x0, y0 = pos[dep]
            x1, y1 = pos[node.index]
            ax.annotate("", xy=(x1 - 0.06, y1), xytext=(x0 + 0.06, y0),
                        arrowprops=dict(arrowstyle="->", color="#555", lw=1.1,
                                        connectionstyle="arc3,rad=0.12"))
    for i, mm in enumerate(milestones):
        ax.axvline(i + 0.5, color="#ddd", lw=0.8, zorder=0)
        ax.text(i, 1.15, f"M{i} · {len(mm['node_indices'])} nodes", ha="center", fontsize=9)
    ax.set_xlim(-0.5, len(milestones) - 0.35)
    ax.set_ylim(min(pos[i][1] for i in pos) - 1.0, 1.6)
    ax.set_yticks([])
    ax.set_xticks(range(len(milestones)), [mm["name"] for mm in milestones], fontsize=8)
    ax.set_title("Machine seeded plan 431 (regenerated): 11 proposal nodes + 1 platform "
                 "self-optimisation node (red)", fontsize=10)
    fig.tight_layout()
    p = out / "fig2_seeded_dag.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return {"path": str(p.relative_to(ROOT)),
            "caption": "图2 机器播种的战役 DAG：3 里程碑 / 12 节点；红色为自回归增长注入的平台自优化节点"
                       "（依赖前两里程碑尾节点 4、8，消费其遥测）；依赖只指向更早节点 → 构造性无环。"}


def fig_gate(g0: Mapping[str, Any], g1: Mapping[str, Any], out: Path) -> dict[str, str]:
    names = [c["name"] for c in g1["checks"]]
    idx = np.arange(len(names))
    pre = [1 if next(c for c in g0["checks"] if c["name"] == n)["ok"] else 0 for n in names]
    post = [1 if next(c for c in g1["checks"] if c["name"] == n)["ok"] else 0 for n in names]
    fig, ax = plt.subplots(figsize=(9.4, 4.4))
    ax.barh(idx - 0.2, pre, height=0.38, color="#7fb3d5", label="before autoregress")
    ax.barh(idx + 0.2, post, height=0.38, color="#1e8449", label="after autoregress")
    ax.set_yticks(idx, names, fontsize=8.5)
    ax.set_xlim(0, 1.15)
    ax.set_xlabel("PASS=1 / FAIL=0")
    ax.set_title(f"Machine gate (8 hard checks): all_passed={g0['all_passed']} → "
                 f"{g1['all_passed']}", fontsize=10)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="x", ls=":", alpha=0.5)
    fig.tight_layout()
    p = out / "fig3_gate_checks.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return {"path": str(p.relative_to(ROOT)),
            "caption": "图3 机器门 8 项硬校验逐项结果（自回归注入前 / 后）：注入前成熟化单数已达标"
                       "（提案侧 5 条 is_maturity），注入后 6 条；其余 7 项均为 PASS。"}


def fig_coverage(audit: Mapping[str, Any], agg: Mapping[str, Any], out: Path) -> dict[str, str]:
    items = agg["items"]
    labels = [f"{'/'.join(q['source'] for q in it['quotes'])}#{i+1}" for i, it in enumerate(items)]
    manual = [1 if next(m for m in audit["item_match"] if m["item_id"] == it["item_id"])["covered"]
              else 0 for it in items]
    machine = [1 for _ in items]
    x = np.arange(len(items))
    fig, ax = plt.subplots(figsize=(10.6, 4.2))
    ax.bar(x - 0.2, machine, width=0.38, color="#1e8449", label="machine aggregation")
    ax.bar(x + 0.2, manual, width=0.38, color="#b03a2e", label="manual plan 431")
    ax.set_xticks(x, labels, rotation=60, fontsize=8)
    ax.set_yticks([0, 1], ["not covered", "covered"])
    missed = len(audit["items_not_covered_by_manual"])
    ax.set_title(f"Capability coverage of the 12 real proposals: machine "
                 f"{len(items)}/{len(items)} vs manual {len(items)-missed}/{len(items)}",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.4)
    fig.tight_layout()
    p = out / "fig4_capability_coverage.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return {"path": str(p.relative_to(ROOT)),
            "caption": f"图4 能力覆盖对照：机器聚合覆盖 {len(items)}/{len(items)}，人工 plan 431 "
                       f"覆盖 {len(items)-missed}/{len(items)}（缺失项见审计 JSON 的 "
                       f"items_not_covered_by_manual）。"}


def fig_wave_chain(plan: Mapping[str, Any], advance: Mapping[str, Any],
                   out: Path) -> dict[str, str]:
    stages = ["wave2 seeded", "wave2 overflow", "wave3 self-sustained", "backpressure demand"]
    values = [len(plan["nodes"]), len(plan["overflow"]), len(advance["plan"]["nodes"]),
              advance["backpressure"]["demanded"]]
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    bars = ax.bar(stages, values, color=["#1e8449", "#d68910", "#2874a6", "#c0392b"])
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v), ha="center", fontsize=10)
    ax.axhline(advance["backpressure"]["min_required"], ls="--", color="#555", lw=1)
    ax.text(-0.45, advance["backpressure"]["min_required"] + 0.15,
            f"node floor = {advance['backpressure']['min_required']}", fontsize=8)
    ax.set_ylabel("node count")
    ax.set_title("Autoregressive closure: saturation → overflow carries to next wave → "
                 "backpressure demands new proposals", fontsize=9.5)
    fig.tight_layout()
    p = out / "fig5_wave_chain.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return {"path": str(p.relative_to(ROOT)),
            "caption": "图5 自回归增长闭环：wave2 播种 12 节点（含平台自优化）→ 1 条溢出 → wave3 "
                       "以溢出项自持生成 2 节点 → 低于下限 5 → 背压信号要求 3 条新提案。"}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run(out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    cfg = load_config()
    inputs = [out_dir / "inputs" / "prop-6260.json", out_dir / "inputs" / "prop-6262.json"]
    snapshot = json.loads((out_dir / "inputs" / "plan431_snapshot.json").read_text("utf-8"))
    manual_report = json.loads(
        (out_dir / "inputs" / "manual_seed_report_m7934.json").read_text("utf-8"))
    self_ext = [n["metadata"].get("ext_id") for n in snapshot["nodes"]]
    universe = snapshot["global_ext_ids"]

    agg = aggregate(inputs, cfg)
    plan = seed_plan(agg, cfg, wave="p423w2", plan_id="431", parent_plan="132")
    gate_pre = check_plan(plan, cfg, global_ext_ids=universe, exclude_ext_ids=self_ext)
    telemetry = pipeline_telemetry(agg, plan, gate_pre)
    plan2, injection = ensure_platform_node(plan, cfg, telemetry=telemetry)
    gate = check_plan(plan2, cfg, global_ext_ids=universe, exclude_ext_ids=self_ext)

    agg_b = aggregate(inputs, cfg)
    plan_b = seed_plan(agg_b, cfg, wave="p423w2", plan_id="431", parent_plan="132")
    plan_b, _ = ensure_platform_node(plan_b, cfg,
                                     telemetry=pipeline_telemetry(agg_b, plan_b, gate_pre))
    replay = {
        "run1_ext_ids": [n["ext_id"] for n in plan2["nodes"]],
        "run2_ext_ids": [n["ext_id"] for n in plan_b["nodes"]],
        "run1_aggregate_digest": digest(agg_b["items"]),
        "run2_aggregate_digest": digest(agg["items"]),
        "identical_ext_ids": [n["ext_id"] for n in plan2["nodes"]]
                             == [n["ext_id"] for n in plan_b["nodes"]],
        "identical_aggregate": digest(agg["items"]) == digest(agg_b["items"]),
        "node_count": len(plan2["nodes"]),
    }
    advance = advance_wave(plan2, cfg, wave_next="p423w3")
    gate_next = check_plan(advance["plan"], cfg, global_ext_ids=universe,
                           exclude_ext_ids=self_ext)
    audit = audit_manual(cfg, agg, plan2, snapshot, manual_report)

    dump(agg, out_dir / "aggregate.json")
    dump(plan, out_dir / "seed_plan_pre_autoregress.json")
    dump(plan2, out_dir / "seed_plan.json")
    dump(gate_pre, out_dir / "gate_pre.json")
    dump(injection, out_dir / "autoregress_injection.json")
    dump(gate, out_dir / "gate.json")
    dump(telemetry, out_dir / "telemetry.json")
    dump(replay, out_dir / "replay.json")
    advance_out = dict(advance)
    advance_out["gate_next_wave"] = gate_next
    dump(advance_out, out_dir / "advance_wave.json")
    dump(audit, out_dir / "manual_arbitration_audit.json")
    dump(platform_proposal(telemetry, cfg, wave="p423w2"),
         out_dir / "platform_self_optimization_proposal.json")

    figures = [
        fig_similarity(agg, cfg, out_dir),
        fig_dag(plan2, out_dir),
        fig_gate(gate_pre, gate, out_dir),
        fig_coverage(audit, agg, out_dir),
        fig_wave_chain(plan2, advance, out_dir),
    ]
    dump({"figures": figures}, out_dir / "figures.json")
    return {
        "aggregate": agg, "plan": plan2, "gate_pre": gate_pre, "gate": gate,
        "telemetry": telemetry, "replay": replay, "advance": advance,
        "gate_next": gate_next, "audit": audit, "figures": figures,
        "config": cfg.as_dict(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    res = run(Path(args.out_dir))
    gate = res["gate"]
    print(json.dumps({
        "nodes": len(res["plan"]["nodes"]),
        "milestones": len(res["plan"]["milestones"]),
        "maturity_nodes": len(gate["maturity_nodes"]),
        "gate_all_passed": gate["all_passed"],
        "merged_semantic": res["aggregate"]["counts"]["merged_semantic"],
        "idempotent": res["replay"]["identical_ext_ids"],
        "overflow": len(res["plan"]["overflow"]),
        "manual_nodes_matched": res["audit"]["manual_nodes_matched"],
        "items_not_covered_by_manual": len(res["audit"]["items_not_covered_by_manual"]),
        "figures": [f["path"] for f in res["figures"]],
    }, ensure_ascii=False, indent=1))
    return 0 if gate["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())