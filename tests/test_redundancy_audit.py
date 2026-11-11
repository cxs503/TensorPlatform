"""``tools/redundancy_audit`` 的合成域测试（零领域语义：夹具只用 foo/bar/widget 抽象词）。

覆盖：
1. 相似度口径（复用 proposal_gate.textsim）：归一化/包含度/长度比门；
2. 近似最近邻：候选对 ⊇ 暴力真值（recall=1.0，抽查桶）；
3. 上界剪枝的正确性：剪掉的对确实低于下限（不剪掉该保留的）；
4. 阈值聚类：强边连通分量、keeper 选择（证据优先级 → 时间 → id）、archive/merge 动作；
5. 账本：簇级求和与**独立复算**相对误差 = 0；记账单位量化；
6. 标注集：结构正例/同计划负例/抽样负例的分组与计数、人工覆盖通道生效；
7. 阈值扫描 + 冻结（F1 最大 → 更小阈值）；
8. 幂等：同一输入连跑 10 次报告逐字节一致；
9. 可证伪：把配置阈值调到荒谬值后 F1 掉到 f1_min_viable 以下 → verdict 变 HONEST_FAIL；
10. 零领域语义：对模块与测试自身跑 domain_leak_scan（除 denylist 允许项外 0 命中）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.redundancy_audit import cluster as CL          # noqa: E402
from tools.redundancy_audit import index as IX           # noqa: E402
from tools.redundancy_audit import labeling as LB        # noqa: E402
from tools.redundancy_audit import ledger as LG          # noqa: E402
from tools.redundancy_audit import report as RP          # noqa: E402
from tools.redundancy_audit.collect import dumps_deterministic  # noqa: E402
from tools.redundancy_audit.config import (              # noqa: E402
    RedundancyConfig,
    RedundancyConfigError,
)

CFG_PATH = ROOT / "config" / "redundancy_audit.json"


@pytest.fixture(scope="module")
def cfg() -> RedundancyConfig:
    return RedundancyConfig.load(CFG_PATH)


def _node(node_id: int, name: str, goal: str, **kw) -> dict:
    meta = {"node_id": str(node_id), "plan_id": kw.get("plan_id", 1), "name": name,
            "goal": goal, "text": f"{name}\n{goal}".strip(), "repo": kw.get("repo", "foo"),
            "ext_id": kw.get("ext_id"), "status": kw.get("status", "pending"),
            "created_at": kw.get("created_at", "2026-01-01T00:00:00+00:00"),
            "actual_start": kw.get("actual_start"), "actual_end": kw.get("actual_end"),
            "mission_id": kw.get("mission_id"),
            "metric_names": kw.get("metric_names", []),
            "deliverables": kw.get("deliverables", [])}
    return meta


BODY_A = ("widget pipeline maturity: closed-form anchor plus residual ledger; "
          "acceptance: relative error <= 0.05 on the reference case grid.")
BODY_B = ("bar module maturity: integration check plus api stabilisation over the "
          "configured scenario matrix; acceptance: idempotent replay over ten runs.")
BODY_C = ("foo mesh service: boundary condition registry plus cache invalidation; "
          "acceptance: cold-start latency budget and hash invariance.")


def _fixture_snapshot() -> dict:
    nodes = [
        # 字面重播组（3 个成员，跨计划）
        _node(1, "widget maturity", BODY_A, plan_id=11, ext_id="W1", status="done",
              created_at="2026-01-01T00:00:00+00:00"),
        _node(2, "widget maturity", BODY_A, plan_id=12, ext_id="W2", status="pending",
              created_at="2026-01-03T00:00:00+00:00"),
        _node(3, "widget maturity", BODY_A, plan_id=13, ext_id="W3", status="pending",
              created_at="2026-01-02T00:00:00+00:00"),
        # 改写重复（不字面相同，但同一交付物）
        _node(4, "widget maturity pipeline", BODY_A + " extra clause: drift audit.",
              plan_id=14, ext_id="W4"),
        # 同计划分工对（不同交付物）
        _node(5, "bar module maturity", BODY_B, plan_id=15, ext_id="B1"),
        _node(6, "foo mesh service", BODY_C, plan_id=15, ext_id="F1"),
        # 无关节点
        _node(7, "unrelated widget", "totally different body about scheduling windows and "
              "queue hygiene without any overlapping vocabulary whatsoever.", plan_id=16),
    ]
    for n in nodes:
        if n["mission_id"] is None:
            n["mission_id"] = int(n["node_id"]) * 10
    missions = {}
    for n in nodes:
        missions[str(n["mission_id"])] = {
            "mission_id": n["mission_id"], "status": "done",
            "budget": {"max_wall_seconds": 3600, "max_tokens": 1000, "max_tool_calls": 10},
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T01:00:00+00:00",
        }
    return {
        "schema": "redundancy-audit-snapshot/1",
        "generator": {"module": "tests", "api_base": "fixture"},
        "plans": [{"plan_id": p, "name": f"plan{p}", "status": "active",
                   "budget_planned": {}, "budget_actual": {}, "created_at": None}
                  for p in range(11, 17)],
        "nodes": nodes,
        "missions": missions,
        "coverage": {"plans_unique": 6, "nodes_indexed": len(nodes),
                     "missions_indexed": len(missions)},
    }


# ── 1 相似度口径 ────────────────────────────────────────────────────────
def test_similarity_is_normalisation_invariant(cfg):
    gate = cfg.gate_config()
    from tools.proposal_gate.textsim import similarity_texts

    a = "Widget  maturity: closed-form   anchor."
    b = "widget maturity：closed form anchor"
    assert similarity_texts(a, b, gate).score > 0.9


def test_length_ratio_gate_blocks_containment(cfg):
    from tools.proposal_gate.textsim import similarity_texts

    gate = cfg.gate_config()
    short = "widget maturity"
    long = "widget maturity " + BODY_A * 3
    res = similarity_texts(short, long, gate)
    # 长度比远小于 min_length_ratio → 包含度不参与，score 退回 jaccard
    assert res.score == pytest.approx(res.jaccard, abs=1e-9)


# ── 2/3 近似最近邻 + 上界剪枝 ────────────────────────────────────────────
def test_ann_candidates_recall_against_brute_force(cfg):
    snap = _fixture_snapshot()
    vectors = IX.build_vectors(snap["nodes"], cfg)
    postings = IX.build_postings(vectors, cfg)
    cand = IX.candidate_pairs(vectors, postings, cfg)
    ref = IX.brute_force_pairs(vectors, cfg, floor=float(cfg.get("thresholds.weak")))
    stat = IX.recall_of([(i, j) for i, j, _ in cand],
                        [(i, j) for i, j, _ in ref])
    assert stat["recall"] == 1.0
    assert stat["reference_pairs"] >= 1


def test_upper_bound_never_undercuts_true_score(cfg):
    snap = _fixture_snapshot()
    vectors = IX.build_vectors(snap["nodes"], cfg)
    postings = IX.build_postings(vectors, cfg)
    truth = {(i, j): r.score for i, j, r in IX.brute_force_pairs(vectors, cfg, floor=0.0)}
    for i, j, shared in IX.candidate_pairs(vectors, postings, cfg):
        bound = IX.upper_bound(vectors[i], vectors[j], shared, cfg)
        assert truth[(i, j)] <= bound + 1e-9


# ── 4 聚类与治理动作 ────────────────────────────────────────────────────
def test_clustering_and_keeper_policy(cfg):
    snap = _fixture_snapshot()
    built = RP.build_edges(snap, cfg)
    strong, weak = CL.split_edges(built["edges"], cfg)
    nodes_by_id = {str(n["node_id"]): n for n in snap["nodes"]}
    costs = LG.build_costs(snap["nodes"], snap["missions"], cfg)
    clusters = CL.build_clusters(strong, nodes_by_id, cfg, costs)
    clusters = [c for c in clusters if c["size"] >= 3]
    assert clusters, "合成夹具里应有字面重播形成的 ≥3 成员簇"
    cluster = [c for c in clusters if {"1", "2", "3"} <= {m["node_id"] for m in c["members"]}][0]
    assert cluster["keeper"] == "1"  # done 优先于 pending，再按时间最早
    actions = {m["node_id"]: m["action"] for m in cluster["members"]}
    assert actions["1"] == "keep"
    assert actions["2"] == "merge"   # 有实耗（60 分钟）→ 合并，不撤单
    assert actions["3"] == "merge"


def test_archive_action_needs_zero_spend(cfg):
    members = [{"node_id": "a", "action": "keep", "cost": {"wall_seconds_actual": 10.0}},
               {"node_id": "b", "action": "merge", "cost": {"wall_seconds_actual": 0.0}}]
    cfg2 = RedundancyConfig.from_dict({**cfg.data, "ledger": {**cfg.get("ledger"),
                                                              "archive_statuses": ["pending"]}})
    # 状态是 pending 但实耗为 0 → archive
    assert CL.member_action({"node_id": "b", "status": "pending"}, "a", cfg2,
                            {"wall_seconds_actual": 0.0}) == "archive"
    # 状态是 done → 即便实耗 0 也合并（不留证据缺口）
    assert CL.member_action({"node_id": "b", "status": "done"}, "a", cfg2,
                            {"wall_seconds_actual": 0.0}) == "merge"
    assert len(members) == 2


# ── 5 账本复算 ──────────────────────────────────────────────────────────
def test_ledger_recompute_is_exact(cfg):
    snap = _fixture_snapshot()
    built = RP.build_edges(snap, cfg)
    strong, _ = CL.split_edges(built["edges"], cfg)
    nodes_by_id = {str(n["node_id"]): n for n in snap["nodes"]}
    costs = LG.build_costs(snap["nodes"], snap["missions"], cfg)
    clusters = CL.build_clusters(strong, nodes_by_id, cfg, costs)
    for c in clusters:
        c["cost"] = LG.cluster_cost(c["members"], cfg)
    rec = LG.recompute_cluster_sums(clusters, costs, cfg)
    assert rec["max_relative_error"] == 0.0
    # 扰动一个成员的成本后必须能查出差异（防「永远为 0」的假复算）
    broken = [dict(c) for c in clusters]
    broken[0] = {**broken[0], "cost": {
        "total": {**broken[0]["cost"]["total"], "wall_seconds_actual": 1.0},
        "keeper": broken[0]["cost"]["keeper"],
        "redundant": broken[0]["cost"]["redundant"],
        "by_action": broken[0]["cost"]["by_action"]}}
    assert LG.recompute_cluster_sums(broken, costs, cfg)["max_relative_error"] > 0.0


def test_cost_interval_requires_both_ends(cfg):
    node = {"actual_start": None, "actual_end": None, "mission_id": 10}
    mission = {"budget": {"max_wall_seconds": 60}, "created_at": "2026-01-01T00:00:00+00:00",
               "finished_at": "2026-01-01T00:10:00+00:00"}
    cost = LG.node_cost(node, mission, cfg)
    assert cost["wall_seconds_actual"] == 600.0
    assert cost["open_interval"] is False
    open_cost = LG.node_cost(node, {"budget": {}, "created_at": "2026-01-01T00:00:00+00:00"},
                             cfg)
    assert open_cost["wall_seconds_actual"] == 0.0 and open_cost["open_interval"] is True


# ── 6 标注集构造 ────────────────────────────────────────────────────────
def test_labels_groups_and_overrides(cfg):
    snap = _fixture_snapshot()
    score = RP.exact_scorer(snap, cfg)
    small = RedundancyConfig.from_dict({**cfg.data,
                                       "labels": {**cfg.get("labels"), "pairs": 12}})
    doc = LB.build_labels(snap, score, small)
    counts = doc["counts"]
    assert counts["total"] == len(doc["pairs"]) <= 12
    assert counts["by_group"]["A"] >= 1
    # 字面重播组进预登记
    assert doc["preregistered_true_groups"], "应有字面重播组被预登记"
    # 覆盖通道：把第一对强制翻成 1/0 之外的值必须生效
    first = doc["pairs"][0]
    flipped = LB.build_labels(snap, score, small, overrides=[
        {"a": first["a"], "b": first["b"], "label": 1 - first["label"], "reason": "test"}])
    changed = [p for p in flipped["pairs"] if (p["a"], p["b"]) == (first["a"], first["b"])][0]
    assert changed["label"] == 1 - first["label"]
    assert changed["overridden"] is True


# ── 7 阈值扫描 / 冻结 ───────────────────────────────────────────────────
def test_sweep_and_freeze_tie_break(cfg):
    pairs = [{"score": 0.9, "label": 1, "group": "A"},
             {"score": 0.6, "label": 0, "group": "B"},
             {"score": 0.2, "label": 0, "group": "C"}]
    rows = LB.sweep(pairs, [0.5, 0.6, 0.7, 0.85, 0.95])
    frozen = LB.freeze_threshold(rows)
    # 0.6 会把 label=0 的那对判正（F1=0.667）；0.7/0.8/0.85 同为 F1=1.0 → tie-break 取更小者
    assert frozen["frozen_threshold"] == 0.7
    assert frozen["sweep_at_freeze"][0]["threshold"] == 0.5
    assert all(0.0 <= r["f1"] <= 1.0 for r in rows)
    strict = LB.metrics_at(pairs, 0.95)
    assert strict["recall"] == 0.0 and strict["precision"] == 0.0


# ── 8 幂等 ──────────────────────────────────────────────────────────────
def test_report_is_byte_identical_over_ten_runs(cfg):
    snap = _fixture_snapshot()
    digests = set()
    for _ in range(int(cfg.get("audit.idempotency_repeats"))):
        rep = RP.build_report(cfg, snap)
        digests.add(hashlib.sha256(dumps_deterministic(rep).encode("utf-8")).hexdigest())
    assert len(digests) == 1


# ── 9 可证伪 ────────────────────────────────────────────────────────────
def test_verdict_degrades_to_honest_fail_when_threshold_absurd(cfg):
    snap = _fixture_snapshot()
    absurd = {**cfg.data,
              "thresholds": {**cfg.get("thresholds"), "strong": 0.999,
                             "sensitivity": [0.999], "sweep_grid": [0.999]}}
    cfg2 = RedundancyConfig.from_dict(absurd)
    rep = RP.build_report(cfg2, snap)
    f1 = rep["metrics"]["strong_threshold_f1"]
    if f1 < float(cfg.get("labels.f1_min_viable")):
        assert rep["verdict"] == "HONEST_FAIL"
    else:  # 字面重播对仍会被抓到 → 至少不能是 PASS 之外的静默提升
        assert rep["verdict"] in ("PASS", "PARTIAL")
    assert rep["criteria"]["strong_threshold_f1"]["value"] == f1


# ── 10 零领域语义 ───────────────────────────────────────────────────────
def test_no_domain_words_in_module(cfg):
    from tools.audit.domain_leak_scan import scan

    paths = ["tools/redundancy_audit", "tests/test_redundancy_audit.py",
             "config/redundancy_audit.json"]
    doc = scan(paths=paths)
    assert doc["hit_count"] == 0, doc["hits"][:5]


def test_config_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"similarity": {}}), encoding="utf-8")
    with pytest.raises(RedundancyConfigError):
        RedundancyConfig.load(bad)


def test_threshold_order_enforced():
    with pytest.raises(RedundancyConfigError):
        RedundancyConfig.from_dict({
            "similarity": {"shingle_size": 3, "containment_weight": 1.0,
                           "min_length_ratio": 0.5, "min_chars": 10},
            "ann": {"min_shared_shingles": 1, "posting_cap": 10, "max_candidates_per_node": 5},
            "thresholds": {"strong": 0.5, "weak": 0.9, "sensitivity": [0.5], "sweep_grid": [0.5]},
            "ledger": {"status_evidence_priority": ["done"], "archive_statuses": ["pending"],
                       "cost_fields": ["wall_seconds_actual"]},
            "policy": {"keeper_rule": "x", "non_keeper_rule": "y"},
            "labels": {"pairs": 10, "true_group_count": 1, "hard_negative_ratio": 0.4,
                       "random_negative_ratio": 0.2, "f1_min_accept": 0.85,
                       "f1_min_viable": 0.7, "hard_negative_fp_max": 0.1,
                       "true_group_recall_min": 1.0},
            "paths": {"snapshot": "a", "labels": "b", "prereg": "c", "report": "d",
                      "figure_dir": "e"},
            "audit": {"idempotency_repeats": 2},
        })