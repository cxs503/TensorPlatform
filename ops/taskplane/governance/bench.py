#!/usr/bin/env python3
"""IN99-N10 治理机制基准（bench）——把四项能力的验收判据 C1..C5 全部机算一遍并落盘。

跑法：
    python3 -m ops.taskplane.governance.bench --out artifacts/in99_n10

产物：
    metrics.json                所有判据数字（报告正文零手抄的来源）
    figures/fig1_threshold_sweep.png
    figures/fig2_score_distribution.png
    figures/fig3_gate_dag_ledger.png

判据映射
--------
C1 去重门：holdout 近重复对召回率 ≥ 0.90 且误报率 ≤ 0.10（阈值在 dev 划分上选定后冻结）
C2 DAG 无环校验：含环 DAG 检出率 = 1.00（n≤4 穷举 + n=5..12 随机），且无环图零误报
C3 机器门：不合规 seed 100% 拒收，且拒收理由含对应违规码（逐条比对预期码）
C4 证据台账：登记后可查询（能力清单/按能力查）+ 审计追溯（hash 链复算，含篡改检出）
C5 负控：注入违规 seed（无环但越白名单/无成熟化/无判据）必被机器门拒收

确定性：随机部分用固定 seed 的 ``random.Random``，不取 wall clock，产出可复算。
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from . import dag as dag_mod
from . import evidence_ledger as led
from . import machine_gate as gate_mod
from . import proposal_dedup as dedup

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "governance" / "proposal_pairs.json"
THETA_GRID = [round(0.05 + 0.01 * i, 2) for i in range(0, 91)]  # 0.05 .. 0.95


# ═════════════════════════ C1 提案去重门 ═════════════════════════

def load_pairs(path: Path = FIXTURE) -> Tuple[Dict[str, str], List[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    texts = {p["id"]: p["summary"] for p in data["proposals"]}
    return texts, data["pairs"]


def split_pairs(pairs: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """确定性划分：按 (a,b) 排序后奇偶交替（dev 偶位 / holdout 奇位）。"""
    ordered = sorted(pairs, key=lambda p: (p["a"], p["b"]))
    return ordered[0::2], ordered[1::2]


def pair_scores(texts: Mapping[str, str], pairs: Sequence[dict],
                cfg: dedup.DedupConfig) -> List[dict]:
    idf = dedup.build_idf(list(texts.values()))
    out = []
    for p in pairs:
        sim = dedup.similarity(texts[p["a"]], texts[p["b"]], idf, cfg)
        out.append({**p, "score": sim.score, "sim": sim.to_dict()})
    return out


def prf(scored: Sequence[dict], theta: float) -> Dict[str, float]:
    tp = sum(1 for s in scored if s["label"] == "dup" and s["score"] >= theta)
    fn = sum(1 for s in scored if s["label"] == "dup" and s["score"] < theta)
    fp = sum(1 for s in scored if s["label"] == "indep" and s["score"] >= theta)
    tn = sum(1 for s in scored if s["label"] == "indep" and s["score"] < theta)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    return {"theta": theta, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "recall": round(recall, 4), "fpr": round(fpr, 4), "precision": round(prec, 4)}


def select_theta(dev: Sequence[dict], min_recall: float = 0.90) -> Dict[str, Any]:
    """dev 上选阈值：先满足召回下限 → 误报最小 → 取最大间隔中点（距离最近）。

    中点 = (dev 独立对最高分 + dev 近重复对最低分)/2；这一步只读 dev 标签。
    """
    rows = [prf(dev, t) for t in THETA_GRID]
    dup = [s["score"] for s in dev if s["label"] == "dup"]
    ind = [s["score"] for s in dev if s["label"] == "indep"]
    midpoint = (min(dup) + max(ind)) / 2 if dup and ind else 0.29
    ok = [r for r in rows if r["recall"] >= min_recall]
    if ok:
        best_fpr = min(r["fpr"] for r in ok)
        cand = [r for r in ok if r["fpr"] == best_fpr]
        chosen = sorted(cand, key=lambda r: (abs(r["theta"] - midpoint), r["theta"]))[0]
    else:                                      # 达不到召回下限：退化为召回优先
        best_recall = max(r["recall"] for r in rows)
        cand = [r for r in rows if r["recall"] == best_recall]
        chosen = min(cand, key=lambda r: r["fpr"])
    return {"chosen_theta": chosen["theta"], "dev": chosen, "sweep": rows}


def bench_dedup() -> Dict[str, Any]:
    texts, pairs = load_pairs()
    dev, hold = split_pairs(pairs)
    cfg = dedup.DedupConfig()
    dev_scored = pair_scores(texts, dev, cfg)
    sel = select_theta(dev_scored, min_recall=0.90)
    theta_dev = sel["chosen_theta"]
    # dev 选出的阈值即冻结阈值（与模块默认值对齐，若不同如实记录并给出双份 holdout 指标）
    frozen = replace(cfg, theta_clone=theta_dev)
    hold_scored = pair_scores(texts, hold, cfg)
    hold_scored_frozen = pair_scores(texts, hold, frozen)
    default_metrics = prf(hold_scored, cfg.theta_clone)
    frozen_metrics = prf(hold_scored_frozen, theta_dev)
    # 消融：去掉字符级两层（只用骨架 + 二元余弦）会怎样（=早期变体，作为基线对照）
    no_char = replace(cfg, w_char=0.0, w_lcs=0.0, w_skeleton=0.634, w_cosine=0.366)
    ablation = {
        "no_char_layer": {
            "weights": {"w_char": 0.0, "w_lcs": 0.0, "w_skeleton": no_char.w_skeleton,
                        "w_cosine": no_char.w_cosine},
            "dev": prf(pair_scores(texts, dev, no_char), theta_dev),
            "holdout_at_frozen_theta": prf(pair_scores(texts, hold, no_char), theta_dev),
        },
        "full_model": {
            "weights": {"w_char": cfg.w_char, "w_lcs": cfg.w_lcs,
                        "w_skeleton": cfg.w_skeleton, "w_cosine": cfg.w_cosine},
            "holdout_at_frozen_theta": prf(hold_scored, theta_dev),
        },
    }
    dup_scores = sorted([s["score"] for s in hold_scored if s["label"] == "dup"])
    ind_scores = sorted([s["score"] for s in hold_scored if s["label"] == "indep"])
    # 兄弟聚类（负控组件）：把 8 个主题的 B/V1/V2 全部当一批 → 应聚成 8 簇、每簇留 1
    batch = [{"id": pid, "summary": t} for pid, t in sorted(texts.items()) if pid.endswith(("B", "V1", "V2"))]
    clusters = dedup.detect_siblings(batch, frozen)
    topic_of = {pid: pid[:-2] if pid.endswith(("V1", "V2")) else pid[:-1] for pid in texts}
    pure = all(len({topic_of[pid] for pid in c}) == 1 for c in clusters)
    resolved = dedup.resolve_siblings(batch, frozen)
    rep_ok = (len(clusters) == 8 and pure
              and sum(1 for v in resolved if v.accepted) == 8
              and sum(1 for v in resolved if v.code == "NEAR_DUPLICATE_SIBLING") == 16)
    return {
        "config": cfg.to_dict(),
        "meta": {
            "fixture": str(FIXTURE.relative_to(ROOT)),
            "n_pairs": len(pairs), "n_dev": len(dev), "n_holdout": len(hold),
            "n_dup_total": sum(1 for p in pairs if p["label"] == "dup"),
            "n_indep_total": sum(1 for p in pairs if p["label"] == "indep"),
            "default_theta": cfg.theta_clone,
            "dev_selected_theta": theta_dev,
        },
        "dev": sel["dev"],
        "holdout_default_theta": default_metrics,
        "holdout_frozen_theta": frozen_metrics,
        "holdout_pairs": [{k: v for k, v in s.items() if k != "sim"} | {"sim": s["sim"]}
                          for s in hold_scored],
        "score_range_holdout": {
            "dup_min": dup_scores[0] if dup_scores else None,
            "dup_median": dup_scores[len(dup_scores) // 2] if dup_scores else None,
            "indep_max": ind_scores[-1] if ind_scores else None,
            "indep_median": ind_scores[len(ind_scores) // 2] if ind_scores else None,
        },
        "sweep": sel["sweep"],
        "ablation": ablation,
        "sibling_control": {
            "n_proposals": len(batch), "n_clusters": len(clusters),
            "cluster_sizes": [len(c) for c in clusters],
            "one_representative_per_topic": rep_ok,
            "clusters": clusters,
        },
        "C1_pass": frozen_metrics["recall"] >= 0.90 and frozen_metrics["fpr"] <= 0.10,
    }


# ═════════════════════════ C2 DAG 环检测 ═════════════════════════

def _has_cycle_matrix(n: int, edges: Sequence[Tuple[int, int]]) -> bool:
    """独立实现（可达矩阵 Warshall）：edges 为 (node, dep)。任何节点可达自身 = 有环。"""
    reach = [[False] * n for _ in range(n)]
    for u, v in edges:
        reach[u][v] = True
    for k in range(n):
        for i in range(n):
            if reach[i][k]:
                row_i, row_k = reach[i], reach[k]
                for j in range(n):
                    if row_k[j]:
                        row_i[j] = True
    return any(reach[i][i] for i in range(n))


def bench_dag() -> Dict[str, Any]:
    exhaustive = {}
    for n in (1, 2, 3, 4):
        possible = [(i, j) for i in range(n) for j in range(n) if i != j]
        cyclic = detected = false_pos = 0
        for mask in range(1 << len(possible)):
            edges = [possible[k] for k in range(len(possible)) if mask >> k & 1]
            nodes = [{"node_id": i + 1, "dependencies": [j + 1 for (u, j) in edges if u == i]}
                     for i in range(n)]
            truth = _has_cycle_matrix(n, edges)
            report = dag_mod.check_dag(nodes)
            if truth:
                cyclic += 1
                detected += int(not report.ok)
            else:
                false_pos += int(not report.ok)
        exhaustive[f"n={n}"] = {
            "graphs": 1 << len(possible), "n_cyclic": cyclic,
            "n_cyclic_detected": detected,
            "n_acyclic": (1 << len(possible)) - cyclic, "n_false_positive": false_pos,
            "detection_rate": round(detected / cyclic, 6) if cyclic else None,
        }

    rng = random.Random(20261108)
    random_rows = {}
    for n in (5, 6, 8, 12):
        cyclic = detected = acyclic = false_pos = 0
        for _ in range(400):
            order = list(range(1, n + 1))
            rng.shuffle(order)
            nodes = [{"node_id": i, "dependencies": []} for i in range(1, n + 1)]
            for a in range(n):
                for b in range(a + 1, n):                  # 先建随机 DAG（依赖只回指更早）
                    if rng.random() < 0.25:
                        nodes[order[a] - 1]["dependencies"].append(order[b])
            if rng.random() < 0.5:                         # 一半注入环：随机插一条回边
                u, v = rng.sample(range(1, n + 1), 2)
                if u not in nodes[v - 1]["dependencies"]:
                    nodes[v - 1]["dependencies"].append(u)
            edges = [(nd["node_id"] - 1, d - 1) for nd in nodes for d in nd["dependencies"]]
            truth = _has_cycle_matrix(n, edges)
            report = dag_mod.check_dag(nodes)
            if truth:
                cyclic += 1
                detected += int(not report.ok)
            else:
                acyclic += 1
                false_pos += int(not report.ok)
        random_rows[f"n={n}"] = {
            "samples": cyclic + acyclic, "n_cyclic": cyclic, "n_cyclic_detected": detected,
            "n_acyclic": acyclic, "n_false_positive": false_pos,
            "detection_rate": round(detected / cyclic, 6) if cyclic else None,
        }

    cyclic_total = sum(v["n_cyclic"] for v in exhaustive.values()) + \
        sum(v["n_cyclic"] for v in random_rows.values())
    detected_total = sum(v["n_cyclic_detected"] for v in exhaustive.values()) + \
        sum(v["n_cyclic_detected"] for v in random_rows.values())
    false_pos_total = sum(v["n_false_positive"] for v in exhaustive.values()) + \
        sum(v["n_false_positive"] for v in random_rows.values())
    # 环路径可读性：注入 3 环 → 必须回填出完整路径
    demo = dag_mod.check_dag([
        {"node_id": 1, "dependencies": [3]},
        {"node_id": 2, "dependencies": [1]},
        {"node_id": 3, "dependencies": [2]},
        {"node_id": 4, "dependencies": [1]},
    ])
    return {
        "exhaustive": exhaustive, "random": random_rows,
        "totals": {"n_cyclic": cyclic_total, "n_cyclic_detected": detected_total,
                   "detection_rate": round(detected_total / cyclic_total, 6) if cyclic_total else None,
                   "n_false_positive": false_pos_total},
        "cycle_path_example": demo.to_dict()["cycle_paths"],
        "C2_pass": detected_total == cyclic_total and false_pos_total == 0,
    }


# ═════════════════════════ C3/C5 机器门 ═════════════════════════

ACCEPT_CRITERIA = [
    {"id": "unit_tests", "metric": "pytest_pass_rate", "threshold": 1.0},
    {"id": "reproducible", "metric": "sha256_match", "threshold": 1.0},
    {"id": "no_violation", "metric": "scan_violations", "threshold": 0},
]


def compliant_seed() -> Dict[str, Any]:
    """合规基线 seed：8 节点 / 3 里程碑 / 2 条成熟化单 / 无环 / 全字段齐备。"""
    goal = ("节点目标：在平台共性机制层实现一项可机器验证的改进，给出实现路径与机器可算判据，"
            "跑通单元测试、落盘证据工件并在报告里逐条列出判据数值，报告须含误差判定与局限说明，"
            "全部改动只在本分支内提交，不得改写历史提交。")
    mat = goal + "本次为成熟化单：复核既有能力并补机器判据。"
    nodes = []
    for i, nid in enumerate(range(101, 109)):
        deps = [] if i == 0 else [nid - 1]
        if i == 4:
            deps = [101, 102]
        nodes.append({
            "node_id": nid, "milestone_id": f"M{min(3, i // 3 + 1)}",
            "dependencies": deps,
            "metadata": {"ext_id": f"IN99-N{nid}", "repo": "TensorPlatform",
                         "goal": mat if i < 2 else goal,
                         "acceptance_criteria": list(ACCEPT_CRITERIA)},
        })
    return {"plan": {"status": "draft", "name": "bench-seed"},
            "milestones": [{"id": "M1"}, {"id": "M2"}, {"id": "M3"}],
            "nodes": nodes}


def _mutants() -> List[Tuple[str, str, List[str]]]:
    """(变异 id, 描述, 预期违规码)。预期码是该变异**必然**触发的判据（至少这些）。"""
    return [
        ("M01_node_count_low", "节点数 4 < 5", ["NODE_COUNT"]),
        ("M02_node_count_high", "节点数 16 > 12", ["NODE_COUNT"]),
        ("M03_ext_id_missing", "一个节点缺 ext_id", ["EXT_ID_MISSING"]),
        ("M04_ext_id_dup", "两个节点 ext_id 相同", ["EXT_ID_DUPLICATE"]),
        ("M05_ext_id_collision", "ext_id 与库内既有记录撞车", ["EXT_ID_COLLISION"]),
        ("M06_repo_not_allowed", "repo 越白名单", ["REPO_NOT_ALLOWED"]),
        ("M07_goal_thin", "任务书 10 字", ["GOAL_TOO_THIN"]),
        ("M08_criteria_missing", "节点缺验收判据", ["CRITERIA_MISSING"]),
        ("M09_milestone_missing", "节点未挂里程碑", ["MILESTONE_MISSING"]),
        ("M10_milestone_empty", "新增里程碑无节点", ["MILESTONE_EMPTY"]),
        ("M11_milestone_few", "只有 1 个里程碑", ["MILESTONE_FEW"]),
        ("M12_maturation_few", "无成熟化单", ["MATURATION_FEW"]),
        ("M13_dag_cycle", "三节点成环", ["DAG_CYCLE"]),
        ("M14_dag_out_of_plan", "依赖引用 plan 外节点", ["DAG_OUT_OF_PLAN"]),
        ("M15_dag_self_loop", "自指依赖", ["DAG_SELF_LOOP"]),
        ("M16_plan_status", "status=archived", ["PLAN_STATUS"]),
        ("M17_combo", "越白名单 + 任务书过薄 + 成环", ["REPO_NOT_ALLOWED", "GOAL_TOO_THIN", "DAG_CYCLE"]),
        ("M18_no_nodes", "节点为空", ["NODE_COUNT"]),
    ]


def apply_mutant(seed: Dict[str, Any], mid: str) -> Dict[str, Any]:
    s = copy.deepcopy(seed)
    nodes = s["nodes"]
    if mid == "M01_node_count_low":
        s["nodes"] = nodes[:4]
    elif mid == "M02_node_count_high":
        for i in range(8):
            s["nodes"].append({"node_id": 201 + i, "milestone_id": "M3", "dependencies": [101],
                               "metadata": dict(nodes[0]["metadata"], ext_id=f"IN99-X{201 + i}")})
    elif mid == "M03_ext_id_missing":
        nodes[3]["metadata"]["ext_id"] = ""
    elif mid == "M04_ext_id_dup":
        nodes[4]["metadata"]["ext_id"] = nodes[3]["metadata"]["ext_id"]
    elif mid == "M05_ext_id_collision":
        nodes[0]["metadata"]["ext_id"] = "IN98-OLD-1"
    elif mid == "M06_repo_not_allowed":
        nodes[2]["metadata"]["repo"] = "UnknownRepo"
    elif mid == "M07_goal_thin":
        nodes[5]["metadata"]["goal"] = "做一个改进。"
    elif mid == "M08_criteria_missing":
        nodes[6]["metadata"].pop("acceptance_criteria", None)
    elif mid == "M09_milestone_missing":
        nodes[7]["milestone_id"] = None
    elif mid == "M10_milestone_empty":
        s["milestones"].append({"id": "M4"})
    elif mid == "M11_milestone_few":
        s["milestones"] = [{"id": "M1"}]
        for nd in nodes:
            nd["milestone_id"] = "M1"
    elif mid == "M12_maturation_few":
        for nd in nodes:
            nd["metadata"]["goal"] = nd["metadata"]["goal"].replace("成熟化", "常规")
    elif mid == "M13_dag_cycle":
        nodes[0]["dependencies"] = [103]
    elif mid == "M14_dag_out_of_plan":
        nodes[1]["dependencies"] = [101, 999]
    elif mid == "M15_dag_self_loop":
        nodes[2]["dependencies"] = [103]
    elif mid == "M16_plan_status":
        s["plan"]["status"] = "archived"
    elif mid == "M17_combo":
        nodes[2]["metadata"]["repo"] = "UnknownRepo"
        nodes[5]["metadata"]["goal"] = "短。"
        nodes[0]["dependencies"] = [103]
    elif mid == "M18_no_nodes":
        s["nodes"] = []
    else:
        raise KeyError(mid)
    return s


def bench_gate() -> Dict[str, Any]:
    cfg = gate_mod.GateConfig()
    seed = compliant_seed()
    base = gate_mod.evaluate_seed(seed, cfg)
    registry = {"IN98-OLD-1", "IN98-OLD-2"}
    rows = []
    for mid, desc, expected in _mutants():
        mutated = apply_mutant(seed, mid)
        rep = gate_mod.evaluate_seed(mutated, cfg, registry)
        codes = rep.codes()
        hit = [c for c in expected if c in codes]
        rows.append({"id": mid, "desc": desc, "expected_codes": expected,
                     "got_codes": codes, "rejected": not rep.ok,
                     "all_expected_present": len(hit) == len(expected),
                     "reasons": rep.to_dict()["reasons"][:4]})
    rejected = sum(1 for r in rows if r["rejected"] and r["all_expected_present"])
    # C5 负控：合法 seed + 反向变异（把每条判据单独放回）确认不是"全拒"
    # C5 负控：合规 seed 在"注入既占 ext_id 注册表"后必须被拒（只多这一个违规）
    collided = copy.deepcopy(seed)
    collided["nodes"][0]["metadata"]["ext_id"] = "IN98-OLD-1"
    collided_rep = gate_mod.evaluate_seed(collided, cfg, registry)
    negative_control = {
        "compliant_seed_accepted": base.ok,
        "compliant_seed_violations": base.to_dict()["reasons"],
        "registry_does_not_block_compliant": gate_mod.evaluate_seed(seed, cfg, registry).ok,
        "registry_collision_rejected": not collided_rep.ok,
        "registry_collision_codes": collided_rep.codes(),
    }
    return {
        "config": cfg.to_dict(),
        "baseline": base.to_dict()["stats"],
        "baseline_accepted": base.ok,
        "mutants": rows,
        "n_mutants": len(rows),
        "n_rejected_with_expected_code": rejected,
        "rejection_rate": round(rejected / len(rows), 6),
        "negative_control": negative_control,
        "C3_pass": base.ok and rejected == len(rows),
        "C5_pass": (base.ok and rejected == len(rows)
                    and negative_control["registry_does_not_block_compliant"]
                    and negative_control["registry_collision_rejected"]
                    and negative_control["registry_collision_codes"] == ["EXT_ID_COLLISION"]),
    }


# ═════════════════════════ C4 证据台账 ═════════════════════════

def _record(mission_id: int, cap: str, *, accepted: bool = True,
            criteria: Sequence[bool] = (True, True, True),
            baseline: Mapping[str, Any] | None = None,
            declares: bool = False, artifacts: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "mission_id": mission_id, "capability_id": cap, "category": "new_capability",
        "accepted": accepted,
        "acceptance": [{"id": f"C{i+1}", "passed": p, "value": 1.0 if p else 0.0,
                        "threshold": 1.0} for i, p in enumerate(criteria)],
        "baseline": dict(baseline) if baseline else None,
        "change": {"declares_breakthrough": declares},
        "artifacts": list(artifacts),
        "evidence": {"report_id": 9000 + mission_id},
    }


def bench_ledger() -> Dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="in99n10_ledger_"))
    path = tmp / "capability_ledger.sqlite"
    base = {"metric": "recall", "reference": 0.75, "measured": 0.95}
    steps = []
    steps.append(("S3", led.register(path, _record(1001, "gov.dedup_gate", baseline=base,
                                                   declares=True,
                                                   artifacts=["ops/taskplane/governance/proposal_dedup.py",
                                                              "artifacts/in99_n10/metrics.json"]))))
    steps.append(("S2", led.register(path, _record(1002, "gov.dag_check", baseline=base))))
    steps.append(("S1", led.register(path, _record(1003, "gov.machine_gate"))))
    steps.append(("NOT_ACCEPTED", led.register(path, _record(1004, "gov.ledger", accepted=False,
                                                            criteria=(True, False, True)))))
    steps.append(("CRITERIA_FAILED", led.register(path, _record(1005, "gov.ledger",
                                                                criteria=(True, False, True)))))
    steps.append(("IDEMPOTENT", led.register(path, _record(1001, "gov.dedup_gate", baseline=base,
                                                           declares=True,
                                                           artifacts=["ops/taskplane/governance/proposal_dedup.py",
                                                                      "artifacts/in99_n10/metrics.json"]))))
    codes = [{"case": name, "code": res.code, "grade": (res.grade_verdict.grade if res.grade_verdict else None),
              "reason": res.detail} for name, res in steps]
    index = led.capability_index(path)
    q = led.query(path, capability_id="gov.dag_check")
    trace = led.trace(path, steps[0][1].entry["entry_id"])
    chain = led.verify_chain(path)
    s_events = led.s_events(path)
    attempted = led.attempts(path)

    # 篡改检出：复制台账后改一条记录，链必须断
    tampered_path = tmp / "tampered.sqlite"
    tampered_path.write_bytes(path.read_bytes())
    import sqlite3
    conn = sqlite3.connect(str(tampered_path))
    conn.execute("UPDATE ledger_entries SET reason='好' WHERE capability_id='gov.dag_check'")
    conn.commit()
    conn.close()
    tamper = led.verify_chain(tampered_path)

    ok_codes = {"S3": "REGISTERED", "S2": "REGISTERED", "S1": "REGISTERED",
                "NOT_ACCEPTED": "NOT_ACCEPTED", "CRITERIA_FAILED": "CRITERIA_FAILED",
                "IDEMPOTENT": "IDEMPOTENT"}
    code_ok = all(c["code"] == ok_codes[c["case"]] for c in codes)
    grades_ok = ([c["grade"] for c in codes[:3]] == ["S3", "S2", "S1"])
    return {
        "ledger_path_kind": "tmp sqlite (每跑一次重建，不入库)",
        "cases": codes,
        "capability_index": index,
        "query_by_capability_n": len(q),
        "query_by_capability_grade": q[0]["grade"] if q else None,
        "trace": {"ok": trace.get("ok"), "seq": trace.get("seq"),
                  "prev_hash": trace.get("prev_hash", "")[:16],
                  "entry_hash": trace.get("entry_hash", "")[:16],
                  "chain_ok": trace.get("chain", {}).get("ok")},
        "chain": {"ok": chain["ok"], "n_entries": chain["n_entries"],
                  "head_hash": chain["head_hash"][:16]},
        "tamper_detected": {"ok": tamper["ok"], "code": tamper.get("code"),
                            "bad_entry": tamper.get("bad_entry")},
        "s_events": s_events,
        "attempts_logged": len(attempted),
        "C4_pass": (code_ok and grades_ok and index.get("gov.dag_check", {}).get("latest_grade") == "S2"
                    and len(q) == 1 and trace.get("ok") and chain["ok"]
                    and (not tamper["ok"]) and tamper.get("code") == "CONTENT_TAMPERED"
                    and len(attempted) == 2 and len(s_events) == 3),
    }


# ═════════════════════════ 图 ═════════════════════════

def _rel(path: Path) -> str:
    """相对仓根路径（out_dir 可能传入相对路径，统一解析后再算）。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def make_figures(out_dir: Path, metrics: Mapping[str, Any]) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir = out_dir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # 图1：阈值扫描（dev/holdout 的召回与误报）
    ded = metrics["dedup"]
    sweep = ded["sweep"]
    thetas = [r["theta"] for r in sweep]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(thetas, [r["recall"] for r in sweep], label="dev recall", color="#1f77b4", lw=2)
    ax.plot(thetas, [r["fpr"] for r in sweep], label="dev FPR", color="#d62728", lw=2, ls="--")
    theta = ded["meta"]["dev_selected_theta"]
    hold_recall = ded["holdout_frozen_theta"]["recall"]
    hold_fpr = ded["holdout_frozen_theta"]["fpr"]
    ax.axvline(theta, color="0.3", lw=1, ls=":")
    ax.scatter([theta], [hold_recall], marker="o", color="#1f77b4", zorder=5,
               label=f"holdout recall={hold_recall:.3f}")
    ax.scatter([theta], [hold_fpr], marker="s", color="#d62728", zorder=5,
               label=f"holdout FPR={hold_fpr:.3f}")
    ax.axhline(0.90, color="#2ca02c", lw=0.8, ls="-.", alpha=0.7)
    ax.axhline(0.10, color="#9467bd", lw=0.8, ls="-.", alpha=0.7)
    ax.set_xlabel("clone threshold θ")
    ax.set_ylabel("rate")
    ax.set_title("C1 dedup gate: threshold sweep (dev) and frozen holdout point")
    ax.set_ylim(-0.02, 1.05)
    ax.legend(fontsize=7, loc="center right")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p1 = figdir / "fig1_threshold_sweep.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig1_threshold_sweep.png",
                 "caption": "去重门综合分阈值扫描：dev 召回/误报曲线与冻结阈值下的 holdout 点"
                            f"（θ={theta:.2f}, holdout recall={hold_recall:.3f}, FPR={hold_fpr:.3f}）",
                 "path": _rel(p1)})

    # 图2：holdout 分数分布
    hold = ded["holdout_pairs"]
    dup = sorted(s["score"] for s in hold if s["label"] == "dup")
    ind = sorted(s["score"] for s in hold if s["label"] == "indep")
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    bins = [i / 20 for i in range(21)]
    ax.hist(dup, bins=bins, alpha=0.65, label=f"near-duplicate (n={len(dup)})", color="#1f77b4")
    ax.hist(ind, bins=bins, alpha=0.65, label=f"independent (n={len(ind)})", color="#d62728")
    ax.axvline(theta, color="0.2", ls="--", lw=1.2, label=f"frozen θ={theta:.2f}")
    ax.set_xlabel("composite similarity score")
    ax.set_ylabel("pair count")
    ax.set_title("C1 holdout: score separation of labeled proposal pairs")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p2 = figdir / "fig2_score_distribution.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig2_score_distribution.png",
                 "caption": "holdout 标注对照集的综合相似度分布（近重复 vs 独立/硬负例），竖线为冻结阈值",
                 "path": _rel(p2)})

    # 图3：三面板（DAG 检出 / 机器门拒收 / 台账等级与链）
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.9))
    dagm = metrics["dag"]
    labels = list(dagm["exhaustive"].keys()) + list(dagm["random"].keys())
    vals, totals = [], []
    for key in labels:
        row = dagm["exhaustive"].get(key) or dagm["random"][key]
        vals.append(row["detection_rate"] or 0.0)
        totals.append(row["n_cyclic"])
    ax = axes[0]
    bars = ax.bar(range(len(labels)), vals, color="#2ca02c")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, fontsize=7)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("cycle detection rate")
    ax.set_title(f"C2 DAG cycle detection (total {dagm['totals']['n_cyclic']} cyclic graphs)")
    for b, t in zip(bars, totals):
        ax.text(b.get_x() + b.get_width() / 2, 1.02, f"{t}", ha="center", fontsize=6)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1]
    gm = metrics["gate"]
    rej = sum(1 for r in gm["mutants"] if r["rejected"] and r["all_expected_present"])
    acc = 1 if gm["baseline_accepted"] else 0
    ax.bar([0, 1], [acc, rej], color=["#1f77b4", "#d62728"])
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["compliant seed\n(n=1)", f"violating seeds\n(n={gm['n_mutants']})"], fontsize=8)
    ax.set_ylim(0, gm["n_mutants"] + 2)
    ax.set_ylabel("count")
    ax.set_title(f"C3/C5 machine gate (rejected with expected code: {rej}/{gm['n_mutants']})")
    for x, v in zip([0, 1], [acc, rej]):
        ax.text(x, v + 0.2, str(v), ha="center", fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[2]
    lm = metrics["ledger"]
    grades = [c["grade"] or c["code"] for c in lm["cases"]]
    ax.bar(range(len(grades)), [1] * len(grades),
           color=["#2ca02c" if g and g.startswith("S") else "#d62728" for g in grades])
    ax.set_xticks(range(len(grades)))
    ax.set_xticklabels(grades, rotation=45, fontsize=7)
    ax.set_yticks([])
    ax.set_title(f"C4 ledger: chain ok={lm['chain']['ok']} "
                 f"tamper detected={lm['tamper_detected']['code']}")
    fig.tight_layout()
    p3 = figdir / "fig3_gate_dag_ledger.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig3_gate_dag_ledger.png",
                 "caption": "三面板：C2 各规模含环图检出率（柱上数字=含环样本数）、C3/C5 机器门"
                            "对合规/违规 seed 的裁决计数、C4 台账各类登记路径与链校验",
                 "path": _rel(p3)})
    return figs


def run(out_dir: Path, with_figures: bool = True) -> Dict[str, Any]:
    metrics = {
        "node": "IN99-N10",
        "version": dedup.VERSION,
        "dedup": bench_dedup(),
        "dag": bench_dag(),
        "gate": bench_gate(),
        "ledger": bench_ledger(),
    }
    metrics["acceptance"] = {
        "C1_dedup_recall_fpr": {
            "pass": metrics["dedup"]["C1_pass"],
            "recall": metrics["dedup"]["holdout_frozen_theta"]["recall"],
            "fpr": metrics["dedup"]["holdout_frozen_theta"]["fpr"],
            "threshold": {"recall_min": 0.90, "fpr_max": 0.10},
        },
        "C2_dag_cycle_detection": {
            "pass": metrics["dag"]["C2_pass"],
            "detection_rate": metrics["dag"]["totals"]["detection_rate"],
            "false_positive": metrics["dag"]["totals"]["n_false_positive"],
            "threshold": {"detection_rate": 1.0, "false_positive_max": 0},
        },
        "C3_gate_reject_with_items": {
            "pass": metrics["gate"]["C3_pass"],
            "rejection_rate": metrics["gate"]["rejection_rate"],
            "n_mutants": metrics["gate"]["n_mutants"],
            "threshold": {"rejection_rate": 1.0},
        },
        "C4_ledger_query_and_audit": {
            "pass": metrics["ledger"]["C4_pass"],
            "chain_ok": metrics["ledger"]["chain"]["ok"],
            "tamper_detected": metrics["ledger"]["tamper_detected"]["code"],
            "capabilities": sorted(metrics["ledger"]["capability_index"]),
        },
        "C5_negative_control": {
            "pass": metrics["gate"]["C5_pass"],
            "detail": "违规 seed 18 例全部拒收且码对得上；合规 seed 被接受",
        },
    }
    metrics["all_pass"] = all(v["pass"] for v in metrics["acceptance"].values())
    out_dir.mkdir(parents=True, exist_ok=True)
    if with_figures:
        metrics["figures"] = make_figures(out_dir, metrics)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN99-N10 governance bench")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "in99_n10"))
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    metrics = run(Path(args.out), with_figures=not args.no_figures)
    print(json.dumps(metrics["acceptance"], ensure_ascii=False, indent=2))
    print("all_pass =", metrics["all_pass"])
    return 0 if metrics["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())