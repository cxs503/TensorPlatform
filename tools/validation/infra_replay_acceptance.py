#!/usr/bin/env python3
"""损耗补偿闭环的机器验收：判据 a–g 逐项真跑，产出可复算 JSON + 图。

判据（与战役节点 metadata 一一对应）
------------------------------------
a) **混淆矩阵 7/7**：5 个运行期丢失（白名单 + 零工具调用）→ ``replayable=true``；
   1 个声明被证伪、1 个验收判据拒绝 → ``false``；逐行打印期望/实际。
   同时给出**全集判读表**（全库终态 mission 的 reason_code 分布），避免「7 条对就算
   过」的幸存者偏差。
b) **幂等**：同一节点连调 3 次 → 沙箱只多 1 个 replay mission、1 条 replay_created 审计。
c) **熔断**：同一模型连续 3 次 infra_runtime → 黑名单 +1 行、事件 +1 条，且第 4 次
   选模型时该模型不在候选里。
d) **预算**：每节点第 3 次重播被拒，审计行含 ``reason=budget_cap``。
e) **KPI**：plan 188 的 ``campaign.yield`` 由 campaign_nodes 现算 = 6/11 = 0.545455。
f) **零领域语义**：本节点三个交付文件过 ``tools.evidence_domain_scan`` 零命中。
g) **kill-switch**：开关打开后全库终态 mission 判定统一 ``false`` / ``kill_switch_off``。

所有写操作落 ``--sandbox-schema`` 的沙箱 schema（LIKE 生产表），生产节点零写入。

用法::

    python3 -m tools.validation.infra_replay_acceptance \
        --out-json artifacts/infra_replay/acceptance.json \
        --out-fig artifacts/infra_replay/fig_acceptance.png
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.validation import infra_replay as IR  # noqa: E402
from tools.validation import mission_attribution as ATTR  # noqa: E402

DEFAULT_SANDBOX = "sbx_infra_replay"
#: 判据 (a) 的 7 条样本：5 条可复活（运行期丢失 + 零工具调用）+ 2 条必须拒
REPLAYABLE_SAMPLES = (6771, 6816, 6900, 7025, 7044)


def _pick(rows: Sequence[Mapping[str, Any]], predicate) -> List[Mapping[str, Any]]:
    return [r for r in rows if predicate(r)]


def _verdict_row(mission: Mapping[str, Any], expect: bool) -> Dict[str, Any]:
    verdict = IR.classify_replayable(mission)
    return {
        "mission_id": verdict["mission_id"],
        "expect_replayable": expect,
        "replayable": verdict["replayable"],
        "reason_code": verdict["reason_code"],
        "attribution": verdict["attribution"],
        "stage_tokens": verdict["stage_tokens"],
        "tool_calls": verdict["tool_calls"],
        "pass": bool(verdict["replayable"]) == bool(expect),
    }


# ── 判据 a ────────────────────────────────────────────────────────────────────
def check_confusion_matrix(missions: Sequence[Mapping[str, Any]],
                           nodes188: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_id = {int(m["id"]): m for m in missions}
    rows: List[Dict[str, Any]] = []
    missing: List[int] = []
    for mid in REPLAYABLE_SAMPLES:
        mission = by_id.get(mid)
        if mission is None:
            missing.append(mid)
            continue
        rows.append(_verdict_row(mission, True))

    # 归因必须喂**完整** mission：只传部分字段会把「revoked 但无 revoked_reason」误判成
    # claim_falsified（本条是自查抓到的真 bug，留在这里当回归桩）
    def primary_of(mission: Mapping[str, Any]) -> str:
        return str(ATTR.classify(dict(mission)).get("primary") or "")

    falsified = [m for m in missions if primary_of(m) == "claim_falsified"]
    rejected = [m for m in missions if primary_of(m) == "acceptance_rejected"]
    if falsified:
        rows.append(_verdict_row(falsified[0], False))
    if rejected:
        rows.append(_verdict_row(rejected[0], False))

    # 全集判读表：不靠 7 条样本活命
    pool = Counter()
    for mission in missions:
        pool[IR.classify_replayable(mission)["reason_code"]] += 1

    passed = sum(1 for r in rows if r["pass"])
    return {
        "criterion": "a_confusion_matrix_7of7",
        "status": "PASS" if (passed == len(rows) and len(rows) == 7) else "FAIL",
        "cells": len(rows),
        "correct": passed,
        "rows": rows,
        "missing_samples": missing,
        "claim_falsified_pool": [int(m["id"]) for m in falsified],
        "acceptance_rejected_pool": [int(m["id"]) for m in rejected],
        "pool_reason_distribution": dict(sorted(pool.items(), key=lambda kv: -kv[1])),
        "node_188_sample": [{"node_id": n["node_id"], "status": n["status"]} for n in nodes188],
    }


# ── 判据 b：幂等 ──────────────────────────────────────────────────────────────
def check_idempotency(store: IR.PgStore, mission: Mapping[str, Any]) -> Dict[str, Any]:
    before = store.counts()
    recs = [IR.replay_one(mission, store=store, model_yields={"modelA": 1.0, "modelB": 0.5})
            for _ in range(3)]
    after = store.counts()
    created = [r for r in recs if r["action"] == "replay"]
    lifecycle_created = [row for row in store.lifecycle_rows()
                         if row["detail"].get("action") == "replay_created"
                         and str(row["detail"].get("parent_mission_id")) == str(mission["id"])]
    delta = {k: after[k] - before[k] for k in after}
    ok = (delta["replay_missions"] == 1 and len(created) == 1 and len(lifecycle_created) == 1
          and all(r["reason_code"] == IR.REASON_ALREADY_REPLAYED for r in recs[1:]))
    return {
        "criterion": "b_idempotent_three_calls_one_mission",
        "status": "PASS" if ok else "FAIL",
        "calls": 3,
        "missions_created": delta["replay_missions"],
        "lifecycle_replay_created": len(lifecycle_created),
        "call_reason_codes": [r["reason_code"] for r in recs],
        "call_actions": [r["action"] for r in recs],
        "replay_mission_ids": [r.get("replay_mission_id") for r in recs],
    }


# ── 判据 c：熔断 ──────────────────────────────────────────────────────────────
def check_breaker(store: IR.PgStore, model: str = "breaker_model_X") -> Dict[str, Any]:
    events = [
        {"model": model, "primary": "infra_runtime"},
        {"model": model, "primary": "infra_runtime"},
        {"model": model, "primary": "infra_runtime"},
    ]
    before = store.counts()
    report = IR.run_round([], store=store, events=events,
                          model_yields={model: 0.99, "modelB": 0.5})
    after = store.counts()
    blacklist = store.list_blacklist()
    chosen = IR.select_fallback_model({model: 0.99, "modelB": 0.5}, blacklist)
    # 负控：同一模型只连续 2 次 → 不熔断
    neg_events = events[:2]
    negative = IR.breaker_tripped(neg_events, model)
    ok = (after["blacklist_rows"] - before["blacklist_rows"] == 1
          and after["event_rows"] - before["event_rows"] >= 1
          and model in blacklist and chosen == "modelB" and negative is False)
    return {
        "criterion": "c_breaker_3_consecutive",
        "status": "PASS" if ok else "FAIL",
        "blacklist_delta": after["blacklist_rows"] - before["blacklist_rows"],
        "event_delta": after["event_rows"] - before["event_rows"],
        "blacklisted": blacklist,
        "model_after_blacklist": chosen,
        "model_skipped": chosen != model,
        "breaker_rows": report["breaker"],
        "negative_control_two_consecutive_trips": negative,
    }


# ── 判据 d：预算封顶 ──────────────────────────────────────────────────────────
def check_budget_cap(store: IR.PgStore, mission: Mapping[str, Any],
                     plan_total_nodes: int) -> Dict[str, Any]:
    """造两次**终态失败**的重播（模拟重播也没活成），第 3 次必须被预算拒。"""
    attempts = []
    for _ in range(2):
        rec = IR.replay_one(mission, store=store, plan_total_nodes=plan_total_nodes,
                            model_yields={"modelA": 1.0})
        if rec["action"] == "replay":
            store.set_replay_status(rec["replay_mission_id"], "failed")
        attempts.append({"action": rec["action"], "reason_code": rec["reason_code"],
                         "replay_mission_id": rec.get("replay_mission_id")})
    third = IR.replay_one(mission, store=store, plan_total_nodes=plan_total_nodes,
                          model_yields={"modelA": 1.0})
    denied = [row for row in store.lifecycle_rows()
              if str(row["mission_id"]) == str(mission["id"])
              and row["detail"].get("action") == "replay_denied"
              and row["detail"].get("reason") == IR.REASON_BUDGET_CAP]
    ok = (len([a for a in attempts if a["action"] == "replay"]) == 2
          and third["action"] == "skip" and third["reason_code"] == IR.REASON_BUDGET_CAP
          and len(denied) == 1)
    return {
        "criterion": "d_budget_cap_third_denied",
        "status": "PASS" if ok else "FAIL",
        "plan_total_nodes": plan_total_nodes,
        "per_node_cap": IR.DEFAULT_POLICY.per_node_cap,
        "first_two": attempts,
        "third": {"action": third["action"], "reason_code": third["reason_code"],
                  "budget": third.get("budget")},
        "audit_rows_with_reason_budget_cap": len(denied),
        "audit_reason": denied[0]["detail"].get("reason") if denied else None,
    }


# ── 判据 e：KPI ───────────────────────────────────────────────────────────────
def check_campaign_yield(nodes188: Sequence[Mapping[str, Any]],
                         missions188: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    node_yield = IR.campaign_yield([n["status"] for n in nodes188])
    mission_yield = IR.mission_level_yield([str(m.get("status")) for m in missions188])
    expected = 6 / 11
    ok = node_yield["succeeded"] == 6 and node_yield["total"] == 11 \
        and abs(node_yield["yield"] - expected) < 1e-9
    divergence = [
        {"node_id": n["node_id"], "node_status": n["status"],
         "mission_id": n["mission_id"],
         "mission_status": next((m["status"] for m in missions188
                                 if int(m["id"]) == int(n["mission_id"] or -1)), None)}
        for n in nodes188
        if n["status"] != "done"
    ]
    return {
        "criterion": "e_campaign_yield_188",
        "status": "PASS" if ok else "FAIL",
        "plan_id": 188,
        "campaign.yield": node_yield,
        "expected": {"succeeded": 6, "total": 11, "yield": expected},
        "mission_level_yield": mission_yield,
        "blocked_nodes": divergence,
        "note": "节点口径 6/11 与 mission 口径分叉，分叉本身被记录（不是抹平）",
    }


# ── 判据 f：零领域语义 ────────────────────────────────────────────────────────
def check_zero_domain(paths: Sequence[str]) -> Dict[str, Any]:
    try:
        from tools.evidence_domain_scan import load_lexicon, scan_paths  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        return {"criterion": "f_zero_domain_semantics", "status": "WITHHELD",
                "error": f"{exc}"}
    result = scan_paths(list(paths), lexicon=load_lexicon(), root=ROOT)
    hits = result.get("hits", [])
    return {
        "criterion": "f_zero_domain_semantics",
        "status": "PASS" if not hits else "FAIL",
        "paths": list(paths),
        "files_scanned": result.get("scanned_files"),
        "hits": hits[:20],
        "hit_count": len(hits),
    }


# ── 判据 g：kill-switch ──────────────────────────────────────────────────────
def check_kill_switch(missions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    verdicts = [IR.classify_replayable(m, kill_switch=True) for m in missions]
    replayable = sum(1 for v in verdicts if v["replayable"])
    codes = Counter(v["reason_code"] for v in verdicts)
    ok = replayable == 0 and set(codes) == {IR.REASON_KILL_SWITCH}
    return {
        "criterion": "g_kill_switch_all_false",
        "status": "PASS" if ok else "FAIL",
        "scanned": len(verdicts),
        "replayable": replayable,
        "reason_codes": dict(codes),
    }


# ── 图 ────────────────────────────────────────────────────────────────────────
def make_figure(report: Mapping[str, Any], out_png: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18.0, 5.4))
    pool = report["checks"]["a"]["pool_reason_distribution"]
    names = list(pool.keys())
    values = [pool[n] for n in names]
    ax = axes[0]
    bars = ax.barh(range(len(names)), values, color="#3d6fb4")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("terminal missions (log scale)")
    ax.set_title("Replay verdict distribution over the whole terminal fleet")
    for bar, value in zip(bars, values):
        ax.text(bar.get_width() * 1.05, bar.get_y() + bar.get_height() / 2, str(value),
                va="center", fontsize=8)

    ax = axes[1]
    kpi = report["kpi"]
    labels = ["campaign.yield\n(plan 188, nodes)", "mission_level_yield\n(plan 188)",
              "infra_loss_rate\n(fleet)", "forced_finish_rate\n(fleet)"]
    vals = [kpi["campaign.yield"]["yield"], kpi["mission_level_yield"]["yield"],
            kpi["infra_loss_rate"]["infra_loss_rate"], kpi["forced_finish_rate"]["forced_finish_rate"]]
    colors = ["#2f7f4f", "#8fae4d", "#c0603a", "#7a5aa8"]
    bars = ax.bar(labels, vals, color=colors)
    for bar, value in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.012, f"{value:.4f}",
                ha="center", fontsize=9)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("ratio")
    ax.set_title("Campaign KPIs (node vs mission yield diverge on purpose)")
    ax.tick_params(axis="x", labelsize=8)
    # 面板 3：判据 a 的 7 格混淆矩阵（期望 vs 实际），错格标红
    ax = axes[2]
    rows = report["checks"]["a"]["rows"]
    matrix = []
    for row in rows:
        matrix.append([1 if (not row["expect_replayable"] and not row["replayable"]) else 0,
                       1 if (not row["expect_replayable"] and row["replayable"]) else 0,
                       1 if (row["expect_replayable"] and not row["replayable"]) else 0,
                       1 if (row["expect_replayable"] and row["replayable"]) else 0])
    grid = [[sum(m[0] for m in matrix), sum(m[1] for m in matrix)],
            [sum(m[2] for m in matrix), sum(m[3] for m in matrix)]]
    ax.imshow(grid, cmap="Greens", vmin=0, vmax=max(1, max(max(r) for r in grid)))
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(grid[i][j]), ha="center", va="center", fontsize=13,
                    color="#11331f")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["predicted false", "predicted true"])
    ax.set_yticks([0, 1]); ax.set_yticklabels(["expected false", "expected true"])
    ax.set_title(f"Confusion matrix (criterion a): {report['checks']['a']['correct']}"
                 f"/{report['checks']['a']['cells']} cells correct")
    fig.suptitle("Infra-loss replay loop: verdict pool / KPIs / confusion matrix", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return [out_png]


# ── main ─────────────────────────────────────────────────────────────────────
def run_acceptance(*, dsn: str, sandbox_schema: str, out_json: str, out_fig: str,
                   pytest_result: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    missions = IR.fetch_terminal_missions(dsn)
    nodes188 = IR.fetch_plan_nodes(dsn, 188)
    missions188 = [m for m in missions if int(m.get("plan_id") or 0) == 188]
    by_id = {int(m["id"]): m for m in missions}

    checks: Dict[str, Any] = {}
    checks["a"] = check_confusion_matrix(missions, nodes188)
    checks["e"] = check_campaign_yield(nodes188, missions188)
    checks["g"] = check_kill_switch(missions)

    store = IR.PgStore(dsn, schema=sandbox_schema)
    checks["sandbox"] = store.prepare_sandbox()
    checks["sandbox"]["seeded"] = store.seed_missions(missions)

    checks["b"] = check_idempotency(store, by_id[REPLAYABLE_SAMPLES[0]])
    checks["c"] = check_breaker(store)
    checks["d"] = check_budget_cap(store, by_id[REPLAYABLE_SAMPLES[1]], plan_total_nodes=11)
    checks["f"] = check_zero_domain([
        "tools/validation/infra_replay.py",
        "tools/validation/infra_replay_acceptance.py",
        "tools/validation/infra_replay_report.py",
        "tests/test_infra_replay.py",
    ])

    verdicts = [IR.classify_replayable(m) for m in missions]
    report: Dict[str, Any] = {
        "module": "tools/validation/infra_replay.py",
        "sandbox_schema": sandbox_schema,
        "fleet": {
            "terminal_missions": len(missions),
            "replayable": sum(1 for v in verdicts if v["replayable"]),
            "replayable_ids": [v["mission_id"] for v in verdicts if v["replayable"]],
        },
        "kpi": {
            "campaign.yield": checks["e"]["campaign.yield"],
            "mission_level_yield": checks["e"]["mission_level_yield"],
            "infra_loss_rate": IR.infra_loss_rate(verdicts),
            "forced_finish_rate": IR.forced_finish_rate(verdicts),
        },
        "checks": checks,
        "pytest": dict(pytest_result or {}),
        "store_counts": store.counts(),
    }
    core = [checks[k]["status"] for k in ("a", "b", "c", "d", "e", "f", "g")]
    report["verdict"] = "PASS" if all(s == "PASS" for s in core) else "FAIL"
    report["criteria_status"] = {k: checks[k]["status"] for k in ("a", "b", "c", "d", "e", "f", "g")}
    report["figures"] = make_figure(report, out_fig)

    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def run_pytest(target: str = "tests/test_infra_replay.py") -> Dict[str, Any]:
    """把 pytest 结果机读入账（判据 f 的另一半：单测全绿）。"""
    import subprocess  # noqa: PLC0415
    proc = subprocess.run([sys.executable, "-m", "pytest", target, "-q", "--no-header"],
                          cwd=ROOT, capture_output=True, text=True, timeout=1800)
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-1:] or [""]
    summary = tail[-1]
    passed = int(re.search(r"(\d+) passed", summary).group(1)) if re.search(r"(\d+) passed", summary) else 0
    failed = int(re.search(r"(\d+) failed", summary).group(1)) if re.search(r"(\d+) failed", summary) else 0
    return {"target": target, "returncode": proc.returncode, "passed": passed,
            "failed": failed, "summary": summary,
            "status": "PASS" if proc.returncode == 0 and failed == 0 else "FAIL"}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="infra-loss replay acceptance (criteria a-g)")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--with-pytest", action="store_true", help="跑单测并把结果写进 JSON")
    ap.add_argument("--sandbox-schema", default=DEFAULT_SANDBOX)
    ap.add_argument("--out-json", default="artifacts/infra_replay/acceptance.json")
    ap.add_argument("--out-fig", default="artifacts/infra_replay/fig_acceptance.png")
    args = ap.parse_args(argv)
    dsn = args.dsn or IR.default_dsn()
    pytest_result = run_pytest() if args.with_pytest else {}
    report = run_acceptance(dsn=dsn, sandbox_schema=args.sandbox_schema,
                            out_json=args.out_json, out_fig=args.out_fig,
                            pytest_result=pytest_result)
    print(json.dumps({"verdict": report["verdict"],
                      "criteria_status": report["criteria_status"],
                      "fleet": report["fleet"],
                      "kpi": report["kpi"]}, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())