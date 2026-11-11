"""报告编排：快照 → 候选 → 聚类 → 账本 → 标注/阈值冻结 → ``redundancy_report.json``。

设计约束（对应验收判据）
------------------------
* **(d) 幂等**：报告只由「快照 + 标注集 + 覆盖文件 + 配置」派生，全程无 wall clock、
  无随机数（抽签用 sha256），序列化用 :func:`collect.dumps_deterministic`（键排序）。
  连跑 N 次 sha256 必须一致（测试里跑 10 次）。
* **(e) 零领域语义**：本模块不含域词；报告里的域词只可能出现在**被审计数据**（节点名/目标）
  中，以及 ``domain_word_hits``（对**代码**路径调 :mod:`tools.audit.domain_leak_scan` 的结果）。
* **(f) 账本可复算**：``budget_recalc_error`` 取 :func:`ledger.recompute_cluster_sums` 的
  逐簇逐字段最大相对误差。

报告 JSON 的机器可读字段
-------------------------
``metrics`` 下五个键与战役验收判据一一对应：
``strong_threshold_f1`` / ``true_duplicate_recall`` / ``negative_false_positive`` /
``domain_word_hits`` / ``budget_recalc_error``，另附 ``f1_min_viable`` 判定与 ``verdict``。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from . import cluster as CL
from . import index as IX
from . import labeling as LB
from . import ledger as LG
from .collect import load_snapshot, read_json, sha256_of, write_json
from .config import RedundancyConfig, repo_root


# ── 单步：相似度 → 边 ───────────────────────────────────────────────────
def build_edges(snapshot: Mapping[str, Any], cfg: RedundancyConfig) -> dict[str, Any]:
    nodes = snapshot["nodes"]
    vectors = IX.build_vectors(nodes, cfg)
    postings = IX.build_postings(vectors, cfg)
    candidates = IX.candidate_pairs(vectors, postings, cfg)
    floor = min(float(x) for x in cfg.get("thresholds.sweep_grid"))
    scored = IX.score_pairs(vectors, candidates, cfg, floor=floor)
    edges = [CL.Edge.of(vectors[i].key, vectors[j].key, r) for i, j, r in scored]
    return {
        "vectors": vectors,
        "candidates": candidates,
        "edges": edges,
        "similarity_pool": [(e.a, e.b, e.score) for e in edges],
        "stats": {
            "nodes_in_snapshot": len(nodes),
            "nodes_indexed": len(vectors),
            "nodes_skipped_short": len(nodes) - len(vectors),
            "shingles_inverted": len(postings),
            "candidate_pairs": len(candidates),
            "pairs_above_floor": len(edges),
            "floor": floor,
        },
    }


def profile_at_threshold(edges: Sequence[Any], nodes_by_id: Mapping[str, Any],
                         cfg: RedundancyConfig, costs: Mapping[str, Any],
                         threshold: float) -> dict[str, Any]:
    """给定阈值下的治理剖面：簇数、覆盖节点数、冗余成本（决策用，非判据）。

    这是「阈值敏感性」的操作侧：F1 在标注集上可能是平的，但簇规模/成本随阈值单调变化，
    阈值选择应由「成本-覆盖面」曲线决定，而不是只看 F1。
    """
    strong = [e for e in edges if e.score >= float(threshold)]
    clusters = CL.build_clusters(strong, nodes_by_id, cfg, costs)
    for c in clusters:
        c["cost"] = LG.cluster_cost(c["members"], cfg)
    portfolio = LG.portfolio(costs, clusters, cfg)
    return {
        "threshold": round(float(threshold), 6),
        "strong_edges": len(strong),
        "clusters": len(clusters),
        "nodes_in_clusters": sum(c["size"] for c in clusters),
        "max_cluster_size": max((c["size"] for c in clusters), default=0),
        "redundant_wall_seconds": portfolio["redundant"]["wall_seconds_actual"],
        "redundant_share": portfolio["redundant_share"]["wall_seconds_actual"],
        "archive_actions": sum(1 for c in clusters for m in c["members"] if m["action"] == "archive"),
        "merge_actions": sum(1 for c in clusters for m in c["members"] if m["action"] == "merge"),
    }


def exact_scorer(snapshot: Mapping[str, Any], cfg: RedundancyConfig):
    """精确相似度函数（标注集/组召回用）：直算，不经近似最近邻，带缓存。"""
    from tools.proposal_gate.textsim import shingles, sim_from_sets

    st = int(cfg.get("similarity.shingle_size"))
    gate = cfg.gate_config()
    grams: dict[str, frozenset[str]] = {str(n["node_id"]): shingles(str(n.get("text") or ""), st)
                                        for n in snapshot["nodes"]}
    cache: dict[tuple[str, str], float] = {}

    def score(a: str, b: str) -> float:
        key = (a, b) if a <= b else (b, a)
        if key in cache:
            return cache[key]
        ga, gb = grams.get(key[0]), grams.get(key[1])
        value = 0.0 if not ga or not gb else float(sim_from_sets(ga, gb, gate).score)
        cache[key] = value
        return value

    return score


# ── 域词扫描（判据 e） ─────────────────────────────────────────────────
def domain_scan(cfg: RedundancyConfig) -> dict[str, Any]:
    from tools.audit.domain_leak_scan import scan

    paths = [str(p) for p in cfg.get("audit.domain_leak_paths")]
    doc = scan(paths=paths)
    return {
        "hit_count": doc["hit_count"],
        "ok": doc["ok"],
        "files_scanned": doc["files_scanned"],
        "targets": doc["targets"],
        "hits": doc["hits"][:20],
        "denylist": doc["denylist"],
        "word_count": doc["word_count"],
    }


def group_detection(groups: Sequence[Mapping[str, Any]],
                    clusters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """组级召回：预登记真重复组的**全部成员**是否被并进同一个强冗余簇。

    这是治理语义下的召回（簇=一次治理动作的作用域），与「组内每一对的词面分都≥阈值」的
    对级召回不是一回事；两者都报，判据用组级（对应「4/4 组」的字面口径），对级作为
    诚实披露的成熟度缺口。
    """
    cluster_of: dict[str, str] = {}
    for c in clusters:
        for m in c["members"]:
            cluster_of[str(m["node_id"])] = c["cluster_id"]
    rows = []
    detected = 0
    for g in groups:
        members = [str(m) for m in g["members"]]
        cids = {cluster_of.get(m) for m in members}
        ok = len(cids) == 1 and None not in cids
        detected += 1 if ok else 0
        rows.append({"members": members, "clusters": sorted(x for x in cids if x),
                     "detected": ok})
    return {"groups": rows, "groups_total": len(rows), "groups_detected": detected,
            "recall": round(detected / len(rows), 6) if rows else 0.0}


def audit_findings(snapshot: Mapping[str, Any], parity: Mapping[str, Any]) -> dict[str, Any]:
    """审计发现（三条都是真库结构/数据缺陷，附可复核计数）。"""
    nodes = snapshot["nodes"]
    by_ext: dict[str, set[str]] = {}
    for n in nodes:
        ext = n.get("ext_id")
        if ext:
            by_ext.setdefault(ext, set()).add(str(n.get("name") or ""))
    reused = {k: v for k, v in by_ext.items() if len(v) >= 2}
    plan_budget_zero = sum(
        1 for p in snapshot["plans"]
        if all(float((p.get("budget_actual") or {}).get(f) or 0.0) == 0.0
               for f in ("max_tokens", "max_tool_calls", "max_wall_seconds"))
    )
    return {
        "F1_campaign_nodes_no_budget_actual": {
            "claim": "campaign_nodes 表无 budget_actual 列（节点级预算证据不存在）",
            "evidence": "节点端点字段实测：" + ",".join(sorted(nodes[0].keys())) if nodes else "",
            "consequence": "账本改用 node→mission wall-clock + mission.budget",
        },
        "F2_plan_budget_actual_all_zero": {
            "claim": "campaign_plans.budget_actual 恒零（从不回写）",
            "plans": len(snapshot["plans"]),
            "plans_with_zero_budget_actual": plan_budget_zero,
        },
        "F3_ext_id_not_globally_unique": {
            "claim": "ext_id 跨计划被复用为「波次-序号」标签，同名 ext_id 可指向完全不同的任务",
            "distinct_ext_ids": len(by_ext),
            "ext_ids_mapping_to_multiple_names": len(reused),
            "example": ({"ext_id": sorted(reused)[0], "names": sorted(reused[sorted(reused)[0]])}
                        if reused else None),
            "consequence": "判重不得使用 ext_id 相同作为充分条件（本审计用节点名归一化 + 词面相似度）",
        },
        "F5_same_name_can_be_different_task": {
            "claim": "节点名在跨计划时会被复用：同名不同题真实存在（本报告的同名组里有 1 组因此混入"
                     "了异题节点，其对内相似度仅 0.575）",
            "consequence": "预登记真重复组改用「名字面重播（名+正文逐字相同）」，并在报告里同时给出"
                           "同名组口径的检测率作为对照",
        },
        "F4_negative_side_trivial": {
            "claim": "语料中词面相似对的负面侧几乎为空：难负例最高分仅 %.3f，"
                     "0.70 以上的对经抽样复核全为真重复 ⇒ 阈值难度全在召回侧（同题改写）"
                     % float(parity.get("hard_negative_max_score") or 0.0),
            "hard_negative_max_score": parity.get("hard_negative_max_score"),
        },
    }


# ── 改写压力集（人工逐对读过的同题改写重复） ───────────────────────────
def paraphrase_stress(
    stress_pairs: Sequence[Mapping[str, Any]],
    similarity_pool: Sequence[tuple[str, str, float]],
    grid: Sequence[float],
) -> dict[str, Any]:
    score_of = {(a, b) if a <= b else (b, a): s for a, b, s in similarity_pool}
    rows = []
    for p in stress_pairs:
        a, b = str(p["a"]), str(p["b"])
        key = (a, b) if a <= b else (b, a)
        rows.append({"a": key[0], "b": key[1], "score": round(score_of.get(key, 0.0), 6),
                     "why": p.get("why", "")})
    recall = {}
    for t in grid:
        hit = sum(1 for r in rows if r["score"] >= float(t))
        recall[f"{float(t):.2f}"] = {"recall": round(hit / len(rows), 6) if rows else 0.0,
                                     "caught": hit, "total": len(rows)}
    return {"pairs": rows, "recall_by_threshold": recall}


def load_stress(cfg: RedundancyConfig) -> list[dict[str, Any]]:
    p = repo_root() / "artifacts" / "redundancy_audit" / "paraphrase_stress.json"
    if not p.exists():
        return []
    return list(read_json(p).get("pairs") or [])


# ── 主流程 ──────────────────────────────────────────────────────────────
def build_report(cfg: RedundancyConfig, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return build_all(cfg, snapshot)[0]


def build_all(cfg: RedundancyConfig, snapshot: Mapping[str, Any] | None = None
              ) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = snapshot or load_snapshot(cfg)
    snapshot_digest = sha256_of(snapshot)

    built = build_edges(snapshot, cfg)
    strong, weak = CL.split_edges(built["edges"], cfg)
    nodes_by_id = {str(n["node_id"]): n for n in snapshot["nodes"]}
    costs = LG.build_costs(snapshot["nodes"], snapshot["missions"], cfg)
    clusters = CL.build_clusters(strong, nodes_by_id, cfg, costs)
    for c in clusters:
        c["cost"] = LG.cluster_cost(c["members"], cfg)
    recompute = LG.recompute_cluster_sums(clusters, costs, cfg)
    portfolio = LG.portfolio(costs, clusters, cfg)

    score_fn = exact_scorer(snapshot, cfg)
    labels_doc = LB.build_labels(snapshot, score_fn, cfg, overrides=LB.load_overrides(cfg))
    grid = [float(t) for t in cfg.get("thresholds.sweep_grid")]
    sweep_rows = LB.sweep(labels_doc["pairs"], grid)
    frozen = LB.freeze_threshold(sweep_rows)
    sensitivity_rows = LB.sweep(labels_doc["pairs"], [float(t) for t in cfg.get("thresholds.sensitivity")])
    strong_t = float(cfg.get("thresholds.strong"))
    at_strong = LB.metrics_at(labels_doc["pairs"], strong_t)
    groups = LB.group_recall(labels_doc["preregistered_true_groups"], score_fn,
                             frozen["frozen_threshold"])
    detected = group_detection(labels_doc["preregistered_true_groups"], clusters)
    detected_name = group_detection(labels_doc.get("preregistered_name_identity_groups") or [],
                                    clusters)
    # 判据字面是「强阈值下 4/4 组召回 100%」：同时报强阈值口径
    groups_at_strong = LB.group_recall(labels_doc["preregistered_true_groups"], score_fn, strong_t)

    profile = [profile_at_threshold(built["edges"], nodes_by_id, cfg, costs, t)
               for t in [float(x) for x in cfg.get("thresholds.sensitivity")] + [strong_t]]
    # 去重（strong 阈值可能已在 sensitivity 列表里）
    seen_t = set()
    profile = [p for p in profile if not (p["threshold"] in seen_t or seen_t.add(p["threshold"]))]

    weak_t = float(cfg.get("thresholds.weak"))
    seeds = [IX.sample_recall_check(snapshot["nodes"], cfg, buckets=6, seed=s, floor=weak_t)
             for s in range(3)]
    ref_total = sum(int(x["reference_pairs"]) for x in seeds)
    hit_total = sum(int(x["hit_pairs"]) for x in seeds)
    ann_check = {
        "method": "稳定哈希分桶（6 桶×3 桶号 ≈ 1200 节点）逐桶暴力枚举求真值，与倒排候选集对账",
        "runs": seeds,
        "reference_pairs": ref_total,
        "hit_pairs": hit_total,
        "recall": (hit_total / ref_total) if ref_total else 1.0,
        "same_plan": IX.plan_recall_check(snapshot["nodes"], cfg, floor=weak_t),
    }
    scan = domain_scan(cfg)
    stress = paraphrase_stress(load_stress(cfg), built["similarity_pool"], grid)

    f1_min_accept = float(cfg.get("labels.f1_min_accept"))
    f1_min_viable = float(cfg.get("labels.f1_min_viable"))
    hard_fp_max = float(cfg.get("labels.hard_negative_fp_max"))
    recall_min = float(cfg.get("labels.true_group_recall_min"))

    metrics = {
        "strong_threshold_f1": at_strong["f1"],
        "true_duplicate_recall": detected["recall"],
        "negative_false_positive": at_strong["hard_negative_fp_rate"],
        "domain_word_hits": scan["hit_count"],
        "budget_recalc_error": recompute["max_relative_error"],
        # 附加审计量（不替代判据，但诚实披露成熟度）
        "frozen_threshold_f1": {r["threshold"]: r["f1"]
                                for r in frozen["sweep_at_freeze"]}[frozen["frozen_threshold"]],
        "paraphrase_recall_at_strong": stress["recall_by_threshold"].get(f"{strong_t:.2f}", {}).get("recall"),
        "ann_recall_sampled": ann_check["recall"],
        "ann_recall_same_plan": ann_check["same_plan"]["recall"],
    }
    criteria = {
        "strong_threshold_f1": {"value": metrics["strong_threshold_f1"], "op": ">=",
                                "threshold": f1_min_accept,
                                "ok": metrics["strong_threshold_f1"] >= f1_min_accept},
        "true_duplicate_recall": {"value": metrics["true_duplicate_recall"], "op": "==",
                                  "threshold": recall_min,
                                  "ok": metrics["true_duplicate_recall"] >= recall_min},
        "negative_false_positive": {"value": metrics["negative_false_positive"], "op": "<=",
                                    "threshold": hard_fp_max,
                                    "ok": metrics["negative_false_positive"] <= hard_fp_max},
        "domain_word_hits": {"value": metrics["domain_word_hits"], "op": "==", "threshold": 0.0,
                             "ok": metrics["domain_word_hits"] == 0},
        "budget_recalc_error": {"value": metrics["budget_recalc_error"], "op": "==", "threshold": 0.0,
                                "ok": metrics["budget_recalc_error"] == 0.0},
    }
    verdict = "PASS" if all(c["ok"] for c in criteria.values()) else (
        "HONEST_FAIL" if metrics["strong_threshold_f1"] < f1_min_viable else "PARTIAL")

    findings = audit_findings(snapshot, {"hard_negative_max_score":
                                        max((p["score"] for p in labels_doc["pairs"]
                                             if p["group"] == "B"), default=0.0)})
    report = {
        "schema": "redundancy-audit-report/1",
        "node": {
            "ext_id": "p393w2-n02-platform-redundancy-audit",
            "mission_id": 13922,
            "repo": "TensorPlatform",
            "module": "tools/redundancy_audit",
        },
        "inputs": {
            "snapshot": str(cfg.get("paths.snapshot")),
            "snapshot_sha256": snapshot_digest,
            "labels_sha256": sha256_of(labels_doc),
            "config_sha256": sha256_of(cfg.data),
            "config_path": str(cfg.path),
            "overrides_applied": labels_doc["counts"]["overrides_applied"],
        },
        "coverage": snapshot["coverage"],
        "similarity": {
            "shingle_size": int(cfg.get("similarity.shingle_size")),
            "containment_weight": float(cfg.get("similarity.containment_weight")),
            "min_length_ratio": float(cfg.get("similarity.min_length_ratio")),
            "engine": "tools.proposal_gate.textsim（复用，唯一相似度口径）",
            "index": built["stats"],
            "ann_recall_check": ann_check,
        },
        "thresholds": {
            "strong": strong_t,
            "profile": profile,
            "weak": float(cfg.get("thresholds.weak")),
            "frozen": frozen,
            "sensitivity": sensitivity_rows,
            "grid": grid,
            "tie_break": str(cfg.get("thresholds.tie_break")),
        },
        "labels": {
            "counts": labels_doc["counts"],
            "rules": labels_doc["labeling_rules"],
            "circularity_note": labels_doc["circularity_note"],
            "at_strong_threshold": at_strong,
            "metrics_at_frozen": LB.metrics_at(labels_doc["pairs"], frozen["frozen_threshold"]),
        },
        "preregistered_true_groups": {
            "definition_primary": "字面重播组：节点名+目标正文逐字相同的节点集合（最强结构证据，无语义判断）",
            "detection": detected,
            "name_identity_detection": detected_name,
            "pair_level_at_strong": groups_at_strong,
            "pair_level_at_frozen": groups,
            "definition": "组级召回=组内全部成员被并进同一强冗余簇；对级召回=组内每一对 score≥阈值",
        },
        "paraphrase_stress": stress,
        "clusters": {
            "strong_edge_count": len(strong),
            "weak_edge_count": len(weak),
            "cluster_count": len(clusters),
            "clusters": clusters,
            "weak_pairs": [e.as_dict() for e in weak],
        },
        "ledger": {
            "portfolio": portfolio,
            "recompute": recompute,
            "policy": {"keeper_rule": str(cfg.get("policy.keeper_rule")),
                       "non_keeper_rule": str(cfg.get("policy.non_keeper_rule"))},
        },
        "findings": findings,
        "metrics": metrics,
        "criteria": criteria,
        "verdict": verdict,
        "audit_chain": {
            "steps": [
                "1 快照（只读 GET，sha256 钉死）",
                "2 索引（字符 4-gram 倒排 + 长度过滤）",
                "3 近似最近邻 + 上界剪枝 → 精确打分的候选对",
                "4 阈值聚类（强边连通分量 / 弱边待复核）",
                "5 成本归集（node→mission wall-clock）+ 冗余成本",
                "6 100 对标注集 → 阈值扫描 → 冻结",
                "7 判据对账（F1 / 组召回 / 难负例误报 / 域词 / 账本复算）",
            ],
            "recompute_commands": [
                "python -m tools.redundancy_audit.collect   # 重采快照（需 API 凭据）",
                "python -m tools.redundancy_audit.report --config config/redundancy_audit.json",
                "python tools/audit/domain_leak_scan.py --paths tools/redundancy_audit tests/test_redundancy_audit.py config/redundancy_audit.json",
                "pytest tests/test_redundancy_audit.py -q",
            ],
        },
    }
    return report, labels_doc


def report_path(cfg: RedundancyConfig):
    return cfg.path_of("paths.report")


def write_report(cfg: RedundancyConfig, report: Mapping[str, Any]) -> str:
    return write_json(report_path(cfg), report)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="生成冗余审计报告（确定性）")
    ap.add_argument("--config", default=None)
    ap.add_argument("--write-labels", action="store_true", help="同时落盘标注集与预登记文件")
    args = ap.parse_args(argv)
    cfg = RedundancyConfig.load(args.config)
    report, labels_doc = build_all(cfg)
    if args.write_labels:
        labels_digest = LB.write_labels(cfg, labels_doc)
        prereg = {
            "schema": "redundancy-audit-prereg/1",
            "snapshot_sha256": report["inputs"]["snapshot_sha256"],
            "labels_sha256": labels_digest,
            "labeling_rules": labels_doc["labeling_rules"],
            "preregistered_true_groups": labels_doc["preregistered_true_groups"],
            "preregistered_name_identity_groups": labels_doc.get("preregistered_name_identity_groups"),
            "sweep_grid": report["thresholds"]["grid"],
            "sensitivity_grid": [float(x) for x in cfg.get("thresholds.sensitivity")],
            "selection_metric": "F1（标注集 100 对）",
            "tie_break": str(cfg.get("thresholds.tie_break")),
            "criteria": {"f1_min_accept": float(cfg.get("labels.f1_min_accept")),
                         "f1_min_viable": float(cfg.get("labels.f1_min_viable")),
                         "hard_negative_fp_max": float(cfg.get("labels.hard_negative_fp_max")),
                         "true_group_recall_min": float(cfg.get("labels.true_group_recall_min"))},
            "declared_before_sweep": "规则、网格与 tie-break 在 config/redundancy_audit.json 里先于首次"
                                     "扫描写定；但审计者与实现者为同一 agent、无第二方见证（局限已披露）",
        }
        write_json(cfg.path_of("paths.prereg"), prereg)
    digest = write_report(cfg, report)
    print(json.dumps(
        {
            "report": str(report_path(cfg)),
            "sha256": digest,
            "verdict": report["verdict"],
            "metrics": report["metrics"],
            "cluster_count": report["clusters"]["cluster_count"],
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())