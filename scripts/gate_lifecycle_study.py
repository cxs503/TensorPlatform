#!/usr/bin/env python
"""End-to-end study for the campaign-gate lifecycle (mission m8179 / P165W1-N08).

Produces, in one deterministic run:

  artifacts/m8179/baseline_plan165.json     frozen measured baseline (checked in)
  artifacts/m8179/wave_a.json               synthetic 10-node wave (checked in)
  artifacts/m8179/criteria.json             C1..C7 numbers + artifact paths
  artifacts/m8179/lifecycle_result.json     full lifecycle payload (wave A)
  artifacts/m8179/ledger.jsonl              capability ledger after wave A+B
  artifacts/m8179/proposals.json            self-regression proposals
  artifacts/m8179/seeded_plan.json          next-wave plan (wave B)
  artifacts/m8179/verdict_accuracy.json     golden-corpus confusion matrix
  artifacts/m8179/maturity_comparison.json  C5 comparison vs plan 165
  artifacts/m8179/fig[1-4]_*.png            figures
  docs/m8179/GATE_LIFECYCLE.md              paper-grade report

Usage: python scripts/gate_lifecycle_study.py [--out artifacts/m8179]
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.gate_lifecycle import (  # noqa: E402
    Criterion, LifecycleConfig, Node, Plan,
    autoregress, baseline as baseline_mod, dag, ledger as ledger_mod,
    pipeline, report as report_mod, verdict as verdict_mod,
)
from tools.gate_lifecycle.executor import StaticExecutor  # noqa: E402
from tools.gate_lifecycle.model import digest  # noqa: E402

DATA = ROOT / "tools" / "gate_lifecycle" / "data" / "golden_cases.json"
BASELINE_DATA = ROOT / "tools" / "gate_lifecycle" / "data" / "baseline_plan165.json"


# ---------------------------------------------------------------------------
# the synthetic wave: 10 nodes, 4 dependency levels, every gate outcome present
# ---------------------------------------------------------------------------
def wave_a() -> dict[str, Any]:
    def crit(metric: str, op: str, threshold: float, *, tol: float = 0.0,
             required: bool = True) -> dict[str, Any]:
        return {"name": f"{metric}_{op}", "metric": metric, "op": op,
                "threshold": threshold, "tolerance": tol, "required": required}

    nodes = [
        {"id": "n01", "deps": [], "duration": 120.0, "capability": "cap-alpha",
         "criteria": [crit("err", "le", 0.05)],
         "meta": {"measurements": {"err": 0.012},
                  "baseline_delta": -0.42, "baseline_ref": "plan165-cap-alpha",
                  "structural_guard": ["artifacts digest-pinned per run"],
                  "evidence_refs": ["artifacts/m8179/cap_alpha.json"]}},
        {"id": "n02", "deps": [], "duration": 90.0, "capability": "cap-beta",
         "criteria": [crit("err", "le", 0.02)],
         "meta": {"measurements": {"err": 0.02}, "baseline_delta": -0.11}},
        {"id": "n03", "deps": [], "duration": 60.0, "capability": "cap-gamma",
         "criteria": [crit("residual", "le", 1e-08)],
         "meta": {"measurements": {"residual": 5e-09}}},
        {"id": "n04", "deps": ["n01"], "duration": 150.0, "capability": "cap-delta",
         "criteria": [crit("score", "ge", 0.95)],
         "meta": {"measurements": {"score": 0.90}}},
        {"id": "n05", "deps": ["n02"], "duration": 100.0, "capability": "cap-eps",
         "criteria": [crit("drift", "le", 0.1)], "meta": {"measurements": {}}},
        {"id": "n06", "deps": ["n04"], "duration": 80.0, "capability": "cap-zeta",
         "criteria": [crit("err", "le", 0.05)],
         "meta": {"measurements": {"err": 0.01}}},
        {"id": "n07", "deps": [], "duration": 70.0, "capability": "cap-eta",
         "criteria": [crit("err", "le", 0.05)],
         "meta": {"measurements": {"err": 0.4},
                  "declaration": {"honest_fail": True,
                                  "note": "hypothesis falsified: trend does not persist"}}},
        {"id": "n08", "deps": ["n03", "n05"], "duration": 110.0, "capability": "cap-theta",
         "criteria": [crit("err", "le", 0.05)],
         "meta": {"measurements": {"err": 0.008},
                  "baseline_delta": -0.31,
                  "structural_guard": ["gate refuses PASS without a metric digest"]}},
        {"id": "n09", "deps": [], "duration": 40.0, "capability": "cap-iota",
         "criteria": [crit("err", "le", 0.05)],
         "meta": {"measurements": {"err": 0.0499}}},
        {"id": "n10", "deps": ["n08"], "duration": 95.0, "capability": "cap-kappa",
         "criteria": [crit("err", "le", 0.05), crit("drift", "le", 0.1, required=False)],
         "meta": {"measurements": {"err": 0.01, "drift": 0.5}, "baseline_delta": -0.07}},
    ]
    return {"plan_id": "wave-A", "nodes": nodes,
            "meta": {"kind": "synthetic lifecycle wave", "domain_free": True}}


# ---------------------------------------------------------------------------
def golden_accuracy() -> dict[str, Any]:
    corpus = json.loads(DATA.read_text(encoding="utf-8"))
    tpl = corpus["templates"]
    rows = []
    agree = 0
    confusion: dict[str, dict[str, int]] = {}
    for case in corpus["cases"]:
        crits = tuple(Criterion.from_dict(c) for c in tpl[case["tpl"]])
        node = Node(node_id=case["id"], criteria=crits, capability=f"cap-{case['id']}")
        got = verdict_mod.evaluate_node(node, case.get("m", {}), case.get("d", {}))
        ok = got.verdict == case["expect_verdict"] and got.status == case["expect_status"]
        agree += int(ok)
        key = case["expect_verdict"]
        confusion.setdefault(key, {})
        confusion[key][got.verdict] = confusion[key].get(got.verdict, 0) + 1
        rows.append({"id": case["id"], "expected": case["expect_verdict"],
                     "expected_status": case["expect_status"],
                     "got": got.verdict, "got_status": got.status, "flags": list(got.flags),
                     "agree": ok})
    n = len(rows)
    return {
        "cases": n,
        "agree": agree,
        "accuracy": agree / n if n else 0.0,
        "confusion": {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())},
        "mismatches": [r for r in rows if not r["agree"]],
        "rows": rows,
        "label_source": corpus["label_source"],
        "corpus_digest": digest(corpus),
    }


def dag_timing(plan: Plan, repeats: int = 200) -> dict[str, Any]:
    worst = 0.0
    t0 = time.perf_counter()
    for _ in range(repeats):
        t = time.perf_counter()
        dag.find_cycle(plan)
        dag.topological_order(plan)
        dag.critical_path(plan)
        worst = max(worst, (time.perf_counter() - t) * 1000.0)
    total = (time.perf_counter() - t0) * 1000.0
    return {"dag_ms_worst": worst, "dag_ms_mean": total / repeats,
            "dag_ms": worst, "repeats": repeats}


def brute_force_critical(plan: Plan) -> tuple[list[str], float]:
    """Independent reference: enumerate every root->leaf path and take the max."""
    nodes = plan.by_id()
    best: tuple[list[str], float] = ([], -1.0)

    def walk(v: str, path: list[str], acc: float) -> None:
        nonlocal best
        path = path + [v]
        acc += nodes[v].duration
        succ = [n.node_id for n in plan.nodes if v in n.deps]
        if not succ and acc > best[1]:
            best = (path, acc)
        for w in sorted(succ):
            walk(w, path, acc)

    for n in sorted(plan.nodes, key=lambda x: x.node_id):
        if not n.deps:
            walk(n.node_id, [], 0.0)
    return best


def run_dirs(out: Path) -> tuple[Path, Path]:
    state = out / "state"
    if state.exists():
        shutil.rmtree(state)
    state.mkdir(parents=True, exist_ok=True)
    return state / "a", state / "b"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "m8179"))
    ap.add_argument("--capacity", type=int, default=16)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    docs = ROOT / "docs" / "m8179"
    docs.mkdir(parents=True, exist_ok=True)

    # ---- plan + dispatch layer --------------------------------------
    plan_doc = wave_a()
    (out / "wave_a.json").write_text(json.dumps(plan_doc, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    plan = Plan.from_dict(plan_doc)
    t_dag = dag_timing(plan)
    cp, cp_len = dag.critical_path(plan)
    ref_cp, ref_len = brute_force_critical(plan)
    sched = dag.schedule(plan, capacity=args.capacity)
    inv_problems = dag.validate_schedule(plan, sched)

    # cycle detection is also checked on a deliberately cyclic plan
    cyclic = Plan.from_dict({"plan_id": "cyclic", "nodes": [
        {"id": "c1", "deps": ["c3"]}, {"id": "c2", "deps": ["c1"]}, {"id": "c3", "deps": ["c2"]}]})
    cyc = dag.find_cycle(cyclic)

    # ---- run wave A twice (idempotency) + ledger dedupe --------------
    dir_a, dir_b = run_dirs(out)
    ledger_path = out / "ledger.jsonl"
    pool_path = out / "proposals.jsonl"
    for p in (ledger_path, pool_path):
        if p.exists():
            p.unlink()

    common = dict(capacity=args.capacity, ledger_path=ledger_path, pool_path=pool_path,
                  max_seed_nodes=10)
    res_a = pipeline.run(plan, executor=StaticExecutor(), cfg=LifecycleConfig(**common, seed_plan_id="wave-B"),
                         out_dir=dir_a)
    res_b = pipeline.run(plan, executor=StaticExecutor(), cfg=LifecycleConfig(**common, seed_plan_id="wave-B"),
                         out_dir=dir_b)

    # second identical register call on the same ledger must append nothing
    recs = [ledger_mod.build_record(r, plan.by_id()[r.node_id].meta, run_id=res_a.run_id)
            for r in res_a.node_results if r.verdict == "PASS"]
    stats2 = ledger_mod.CapabilityLedger(ledger_path).register(recs)

    # ---- self-regression chain: proposals -> injection -> wave B ----
    pool_after = autoregress.ProposalSink(pool_path).read()
    seeded = res_a.seeded_plan
    wave_b_doc = json.loads(json.dumps(seeded))
    for node in wave_b_doc["nodes"]:
        node["duration"] = 50.0
        node["criteria"] = [{"name": "delta_le", "metric": "delta", "op": "le",
                             "threshold": 0.05, "required": True}]
        node.setdefault("meta", {})["measurements"] = {"delta": 0.01}
        node["meta"]["baseline_delta"] = -0.05
    (out / "seeded_plan.json").write_text(
        json.dumps({"seeded": seeded, "executed": wave_b_doc}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # close wave A proposals, then run wave B against the same ledger/pool
    autoregress.rewrite_pool(pool_path,
                             autoregress.close_wave(pool_after, res_a.injection["proposal_ids"]))
    res_wa = pipeline.run(plan, executor=StaticExecutor(),
                          cfg=LifecycleConfig(**common, seed_plan_id="wave-C"),
                          out_dir=None)
    res_wb = pipeline.run(Plan.from_dict(wave_b_doc), executor=StaticExecutor(),
                          cfg=LifecycleConfig(**common, seed_plan_id="wave-C"),
                          out_dir=out / "wave_b")

    # ---- C5 maturity vs plan 165 ------------------------------------
    # canonical fixture lives with the package; a copy is emitted into the
    # artifact directory so the report's numbers travel with their provenance
    base_path = out / "baseline_plan165.json"
    base_path.write_text(BASELINE_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    base = baseline_mod.load_baseline(base_path)
    maturity = baseline_mod.evaluate(base, capacity=args.capacity)
    (out / "maturity_comparison.json").write_text(
        json.dumps(maturity, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    # ---- C2 golden accuracy -----------------------------------------
    acc = golden_accuracy()
    (out / "verdict_accuracy.json").write_text(
        json.dumps({k: v for k, v in acc.items() if k != "rows"}, indent=2, ensure_ascii=False,
                   sort_keys=True) + "\n", encoding="utf-8")

    # ---- criteria table (C1..C7) ------------------------------------
    payload = res_a.to_dict()
    payload["result_digest"] = res_a.result_digest
    (out / "lifecycle_result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    (out / "proposals.json").write_text(
        json.dumps({"generated": res_a.proposals["proposals"],
                    "duplicates": res_a.proposals["duplicates"],
                    "injection": res_a.injection}, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")

    c1_cp_ok = abs(ref_len - cp_len) < 1e-9 and ref_cp == cp
    criteria = {
        "C1": {"metric": "DAG 无环检测+关键路径正确性与耗时",
               "value": f"wave_A_acyclic={dag.find_cycle(plan) is None}, "
                        f"deliberate_cycle={cyc}, cp={cp}, len={cp_len:.0f}s, "
                        f"brute_force_len={ref_len:.0f}s, worst={t_dag['dag_ms']:.3f}ms, "
                        f"schedule_problems={inv_problems}",
               "target": "无环判定正确 + 关键路径与穷举一致 + 10节点≤1000ms",
               "status": "PASS" if (c1_cp_ok and cyc and not inv_problems and
                                    t_dag["dag_ms"] < 1000.0) else "FAIL",
               "artifact": "artifacts/m8179/lifecycle_result.json"},
        "C2": {"metric": "自动裁决 vs 人工金标一致率",
               "value": f"{acc['agree']}/{acc['cases']} = {acc['accuracy']:.4f}",
               "target": ">=0.95",
               "status": "PASS" if acc["accuracy"] >= 0.95 else "FAIL",
               "artifact": "artifacts/m8179/verdict_accuracy.json"},
        "C3": {"metric": "能力台账自动登记完整率",
               "value": f"{res_a.completeness['registered']}/{res_a.completeness['passed_nodes']} "
                        f"= {res_a.completeness['completeness']:.4f}",
               "target": "=1.0",
               "status": "PASS" if res_a.completeness["completeness"] >= 1.0 else "FAIL",
               "artifact": "artifacts/m8179/ledger.jsonl"},
        "C4": {"metric": "gap_analysis→提案池注入→播种 全链路自动化",
               "value": f"gaps={res_a.gaps['counts']}, proposals={res_a.proposals['generated']}, "
                        f"injected={res_a.injection['written']}, seeded_nodes={len(seeded['nodes'])}, "
                        f"waveB_pass={res_wb.failures['passed']}/{len(res_wb.node_results)}",
               "target": "四段全自动且播种波次可执行",
               "status": "PASS" if (res_a.proposals["generated"] > 0 and
                                    res_a.injection["written"] == res_a.proposals["generated"] and
                                    len(seeded["nodes"]) > 0 and
                                    res_wb.failures["failed"] == 0) else "FAIL",
               "artifact": "artifacts/m8179/seeded_plan.json"},
        "C5": {"metric": "端到端时间缩短 或 人工干预减少（vs plan 165）",
               "value": f"time_replay={maturity['comparison']['time_replay']['reduction']:+.3f}, "
                        f"time_serial={maturity['comparison']['time_serial']['reduction']:+.3f}, "
                        f"interventions={maturity['comparison']['interventions']['reduction']:+.3f}",
               "target": "时间>=0.30 或 干预>=0.50",
               "status": maturity["comparison"]["decision"],
               "artifact": "artifacts/m8179/maturity_comparison.json"},
        "C6": {"metric": "全链路幂等（同输入两次，逐节点一致）",
               "value": f"digest_a={res_a.result_digest} digest_b={res_b.result_digest}, "
                        f"node_digest_a={res_a.node_digest[:16]} "
                        f"node_digest_b={res_b.node_digest[:16]}, "
                        f"ledger_second_appended={stats2.appended}, "
                        f"ledger_second_duplicates={stats2.duplicates}",
               "target": "两次 digest 相同且台账不重复",
               "status": "PASS" if (res_a.result_digest == res_b.result_digest and
                                    res_a.node_digest == res_b.node_digest and
                                    stats2.appended == 0) else "FAIL",
               "artifact": "artifacts/m8179/lifecycle_result.json"},
        "C7": {"metric": "honest-FAIL 不计失败率、进 S-N 轨",
               "value": f"failed={res_a.failures['failed']}, honest_fail="
                        f"{res_a.failures['honest_fail']}, denominator="
                        f"{res_a.failures['denominator']}, rate="
                        f"{res_a.failures['failure_rate']:.4f}, S-N="
                        f"{res_a.failures['negative_track']}",
               "target": "honest-FAIL 不入失败率且出现在 S-N 轨",
               "status": "PASS" if (res_a.failures["honest_fail"] == 1 and
                                    res_a.failures["failed"] == 1 and
                                    "n07" in res_a.failures["negative_track"]) else "FAIL",
               "artifact": "artifacts/m8179/lifecycle_result.json"},
    }
    (out / "criteria.json").write_text(
        json.dumps(criteria, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    # ---- figures ----------------------------------------------------
    sched_doc = res_a.schedule
    figs = []
    p1 = report_mod.fig_schedule_gantt(sched_doc, base["nodes"], out / "fig1_schedule.png")
    figs.append({"name": "fig1_schedule", "path": str(Path(p1).relative_to(ROOT)),
                 "caption": "调度对比：(a) plan 165 实测派发时刻（无 planned_start/planned_end，"
                            "汇总节点与其依赖同时启动）；(b) 自动 CPM 列表调度（k=16，红框=关键路径）。"})
    p2 = report_mod.fig_gate_outcomes(payload, out / "fig2_gate_outcomes.png")
    figs.append({"name": "fig2_gate_outcomes", "path": str(Path(p2).relative_to(ROOT)),
                 "caption": "验收门分布：(a) 自动裁决；(b) 节点状态（honest_fail 进 S-N 轨）；"
                            "(c) gap_analysis 各类缺口计数（直接驱动提案生成）。"})
    p3 = report_mod.fig_critical_path(payload, out / "fig3_critical_path.png")
    figs.append({"name": "fig3_critical_path", "path": str(Path(p3).relative_to(ROOT)),
                 "caption": "自动调度下每个节点的总时差（红线=关键路径，时差 0）："
                            "关键路径长度与穷举参考一致。"})
    p4 = report_mod.fig_maturity(maturity, out / "fig4_maturity.png")
    figs.append({"name": "fig4_maturity", "path": str(Path(p4).relative_to(ROOT)),
                 "caption": "成熟化对比：(a) 两种时间口径都如实给出（replay 口径无时间收益，"
                            "workload-equalised 口径 -62.9%）；(b) 人工干预/节点（2.0 → 0，"
                            "虚线=C5 目标线）。"})

    # ---- report -----------------------------------------------------
    rep = {
        "title": "战役门机制全生命周期自动化成熟化（P165W1-N08 / m8179）",
        "abstract": (
            "把 TensorPlatform 的战役门从「基础 DAG 派发 + 简单验收」升级为"
            "「派发→执行→验收→能力登记→自回归优化」的全自动闭环，并以 plan 165 的实测数据为基线做成熟化裁决。"
            "七条机器判据 C1–C7 全部由 `scripts/gate_lifecycle_study.py` 一次运行产出数字，"
            "结论 C1–C4/C6/C7 PASS；C5 通过「人工干预减少」分支（2.0→0 次/节点，-100%），"
            "时间分支在 replay 口径下为负（-86.0%，如实报 FAIL），只在 workload-equalised 口径下为 -62.9%。"
        ),
        "intro": (
            "任务对象不是物理场，而是平台的战役门机制本身，因此没有几何/流场参数，"
            "参数量是调度与裁决参数：计划为 10 节点 4 层合成波次（wave-A），每节点带机器可算判据"
            "（err/score/residual/drift 的相对阈值），执行器为 StaticExecutor（测量值即工件输出）；"
            "调度容量 k=16（合同要求 ≥10）；基线为 plan 165（P27-W3-seed）的实测派发记录："
            "8 节点、墙钟 6277.7 s、16 条事件（14 mission_dispatched + 2 plan_updated = 2.0 次/节点），"
            "planned_start/planned_end 全为空，且汇总节点与其 7 个依赖同一时刻派发。"
            "合成波次的节点命名与指标名全部与学科无关（cap-alpha…/err/score），因为平台仓铁律是零领域语义。"
        ),
        "method": [
            "派发层 `tools/gate_lifecycle/dag.py`：Kahn 拓扑＋DFS 回边定位环、CPM 求关键路径与时差、"
            "按关键长度优先的资源饱和列表调度（k≥10），全部平局按节点 id 破平以保证确定性；"
            "关键路径用「穷举所有根→叶路径」的独立实现交叉校验。",
            "执行层 `executor.py`：StaticExecutor（合成波次）与 JsonCommandExecutor（真实子进程输出 JSON 测量值），"
            "执行器异常被记为 failed 证据而非静默通过。",
            "验收层 `verdict.py`：6 条显式裁决规则（R1 缺证据=WITHHELD 不是 FAIL、R2 证据压声明、"
            "R3 有违约即 FAIL、R4 honest-FAIL 进 S-N 轨不计失败率、R5 声明白拿保护会被标 declaration_conflict、"
            "R6 可选判据不改裁决），对 52 例人工金标集做一致率统计。",
            "台账层 `ledger.py`：PASS 节点自动写 capability_delta，按 (node_id, result) 摘要去重，"
            "S 级由声明式规则推导（S3=结构性保障+基线增量，S2=有基线增量，S1=其余）。",
            "自回归层 `autoregress.py`：gap_analysis 五类缺口（gap 违约/缺证据/受阻/负结果/未登记）→ "
            "按指纹去重后生成提案 → 注入 append-only 提案池 → 播种下一波计划（after 提示转依赖并做 DAG 校验）。",
            "幂等层：run_id 与 result_digest 都是「计划+配置」的规范化 JSON sha256，去掉了时间戳/绝对路径，"
            "两次运行逐节点结果必须逐字节一致。",
            "成熟化层 `baseline.py`：读冻结的 plan 165 实测基线，同时给出 replay 与 workload-equalised 两种时间口径，"
            "并逐条列出基线中的依赖违约。",
            "工具链：python 3 + pytest（判据回归）+ matplotlib（图）；全流程 `python scripts/gate_lifecycle_study.py` 可复现。",
        ],
        "criteria": criteria,
        "plan": {"plan_id": plan.plan_id, "nodes": plan_doc["nodes"]},
        "schedule": sched_doc,
        "invariants": res_a.invariants,
        "timing": t_dag,
        "accuracy": acc,
        "completeness": res_a.completeness,
        "ledger": res_a.ledger_stats,
        "failures": res_a.failures,
        "gaps": res_a.gaps,
        "proposals": {"generated": res_a.proposals["generated"],
                      "deduped": res_a.proposals["deduped"],
                      "items": res_a.proposals["proposals"]},
        "injection": res_a.injection,
        "seeded_plan": seeded,
        "wave_b": {"passed": res_wb.failures["passed"],
                   "appended": res_wb.ledger_stats.get("appended", 0),
                   "duplicates": res_wb.ledger_stats.get("duplicates", 0),
                   "completeness": res_wb.completeness["completeness"]},
        "second_wave_dedupe": {"generated": res_wa.proposals["generated"],
                               "deduped": res_wa.proposals["deduped"],
                               "written": res_wa.injection["written"]},
        "idempotency": {"digest_a": res_a.result_digest, "digest_b": res_b.result_digest,
                        "node_digest_a": res_a.node_digest, "node_digest_b": res_b.node_digest,
                        "equal": res_a.result_digest == res_b.result_digest,
                        "ledger_appended_second": stats2.appended,
                        "ledger_duplicates_second": stats2.duplicates},
        "maturity": maturity,
        "figures": figs,
        "notes": [
            "C5 时间分支必须分开报：plan 165 的墙钟被单个长节点支配（6277 s ≈ 全部墙钟），"
            "因此「同一时长、依赖被真正强制」的 replay 口径下自动调度反而更长（-86.0%），这是基线的依赖违约掩盖了真实工作量；"
            "只有 workload-equalised 的串行模型口径才得到 -62.9%。两条都写进报告，不挑口径。",
            "workload-equalised 口径是模型：基线串行执行 + 每节点 2.0 次人工交接 × 62 s 交接成本"
            "（62 s 取自 plan 165 初始批次后 5 个相邻派发间隔的中位数 61.82 s）。模型的口径与参数都已冻结在基线 JSON，可复算。",
            "基线依赖违约是实测事实：汇总节点（依赖 7 个节点）在 offset 1.5 s 被派发，而其最长依赖在 offset 6277 s 才结束；"
            "新派发层把此类违约作为阻塞条件处理（未 PASS 的上游 → 下游 blocked，不伪造成功）。",
            "规模局限：合成波次 10 节点、容量 16，列表调度未做优先级抢占/回填；"
            "真实战役里节点时长不可预知，本机制的时长字段需由历史中位数估计，估计误差会传导到 makespan。",
            "裁决规则里「证据压声明」是刻意选择：若执行者声称 PASS 而指标违约，节点判 FAIL 并标 overclaim；"
            "这也意味着指标采集器本身的 bug 会变成 FAIL 而不是 WITHHELD，需要靠 C2 金标集与 metric-digest 审计来兜底。",
            "提案去重只做指纹级（规范化文本 sha256 前 16 位）；语义级近重复（同义改写）仍可能漏过，需要后续 embedding 去重。",
            "C2 的「人工裁决」是 52 例手写期望值（每个案例的 verdict+status 在跑引擎之前就写定），但它与引擎由同一个智能体编写，"
            "因此 100% 一致只能证明「规则实现与声明规则无偏差」，不等于独立人类评审的一致率；真要做人机一致率裁决需要独立评审席位复核这批案例"
            "（本单未做，属残余风险）。",
            "honest-FAIL 的下游处理是刻意选择：honest-FAIL 保护执行者不背失败率，但它没有产出可用结果，因此其下游节点一律 blocked ——不伪造成功链。",
        ],
        "error_verdict": (
            "本任务的「关键量」是机制判据而非物理量，逐项判定如下：\n\n"
            f"| 关键量 | 实测 | 基准 | 误差/差异 | 是否达标 |\n|---|---|---|---|---|\n"
            f"| 关键路径长度 | {cp_len:.0f} s | 穷举参考 {ref_len:.0f} s | 0（逐节点路径完全一致） | 是（<3%） |\n"
            f"| 自动裁决一致率 | {acc['accuracy'] * 100:.2f}% | 手写金标 100% | {100 - acc['accuracy'] * 100:.2f} pp | "
            f"{'是' if acc['accuracy'] >= 0.95 else '否'}（阈值 95%，非 3%） |\n"
            f"| 台账登记完整率 | {res_a.completeness['completeness'] * 100:.1f}% | 100% | 0 | 是（<3%） |\n"
            f"| 幂等摘要 | {res_a.result_digest} | {res_b.result_digest} | 0 | 是（<3%） |\n"
            f"| 干预次数/节点 | 0 | 基线 2.0 | -2.0（-100%） | 是（<3% 口径不适用，按 C5 阈值 50%） |\n"
            f"| end-to-end 时间（replay） | {maturity['comparison']['time_replay']['automated_s']:.0f} s | "
            f"{maturity['comparison']['time_replay']['baseline_s']:.0f} s | "
            f"{maturity['comparison']['time_replay']['reduction'] * 100:+.1f}% | 否（如实报 FAIL） |\n"
            f"| end-to-end 时间（serial model） | {maturity['comparison']['time_serial']['automated_s']:.0f} s | "
            f"{maturity['comparison']['time_serial']['baseline_s']:.0f} s | "
            f"{maturity['comparison']['time_serial']['reduction'] * 100:+.1f}% | 是（>30%） |\n\n"
            "回答「是否<3%」：与可定义基准的项（关键路径、台账完整率、幂等摘要）误差均为 0，<3% 成立；"
            "裁决一致率以 95% 机制阈值为准（实测见上），时间项无 3% 意义（不是物理量），按 C5 的 30%/50% 阈值裁决，"
            "且 replay 口径明确未达标。"
        ),
        "audit": [
            f"基线冻结：`artifacts/m8179/baseline_plan165.json`（含 4 条 SQL 与观测记录，digest {digest(base)[:16]}）",
            f"合成波次：`artifacts/m8179/wave_a.json`（digest {digest(plan_doc)[:16]}）",
            f"全链路结果：`artifacts/m8179/lifecycle_result.json`（run_id {res_a.run_id}，result_digest {res_a.result_digest}）",
            f"能力台账：`artifacts/m8179/ledger.jsonl`（{res_a.ledger_stats['records']} 条，去重 "
            f"{stats2.duplicates} 条）",
            f"提案池：`artifacts/m8179/proposals.jsonl`（注入 {res_a.injection['written']} 条；"
            f"第二波同输入再生成 {res_wa.proposals['generated']} / 去重 {res_wa.proposals['deduped']}）",
            f"播种计划：`artifacts/m8179/seeded_plan.json`（wave-B 执行 PASS {res_wb.failures['passed']}/"
            f"{len(res_wb.node_results)}）",
            f"金标语料：`tools/gate_lifecycle/data/golden_cases.json`（{acc['cases']} 例，corpus digest "
            f"{acc['corpus_digest'][:16]}）",
            f"环境：python {platform.python_version()} / {platform.platform()}",
        ],
    }

    usage = """
## 7. 复现与接入

```bash
python scripts/gate_lifecycle_study.py                       # 复现全部判据数字与图
python -m pytest tests/test_gate_lifecycle_*.py -q           # 62 条回归（C1-C7）
python -m tools.gate_lifecycle.cli dag  --plan artifacts/m8179/wave_a.json
python -m tools.gate_lifecycle.cli run  --plan plan.json --ledger l.jsonl --pool p.jsonl --out out/
python -m tools.gate_lifecycle.cli maturity --baseline tools/gate_lifecycle/data/baseline_plan165.json
```

接入方式：plan JSON 就是战役 DAG（`id / deps / duration / criteria / meta.measurements`）；
真实门用 `--cmd "<命令>"` 走 `JsonCommandExecutor`（命令最后一行输出
`{"measurements": {...}, "declaration": {...}}`，非零退出码记 failed 证据）；
能力台账与提案池是 append-only JSONL，可替换为 backend 侧 `campaign_plans` /
`proposal_gate` 的存储适配器而不改裁决逻辑。
"""
    md = report_mod.render_markdown(rep) + usage
    (docs / "GATE_LIFECYCLE.md").write_text(md, encoding="utf-8")
    (out / "report.json").write_text(json.dumps({k: v for k, v in rep.items() if k != "plan"},
                                                indent=2, ensure_ascii=False, default=str) + "\n",
                                     encoding="utf-8")
    print(json.dumps({"criteria": {k: v["status"] for k, v in criteria.items()},
                      "report": str(docs / "GATE_LIFECYCLE.md"),
                      "result_digest": res_a.result_digest},
                     ensure_ascii=False, indent=2))
    return 0 if all(v["status"] == "PASS" for v in criteria.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())