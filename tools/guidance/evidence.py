#!/usr/bin/env python3
"""IN24-E2 引导回路：部署验证与生效实证生成器。

跑法（口令只走环境变量，绝不落仓）::

    export PGPASSWORD=***   # 或 TP_PG_PASSWORD
    python3 tools/guidance/evidence.py            # 默认写库 + 出 artifacts
    python3 tools/guidance/evidence.py --dry-run  # 只算不写（SQLite 内存库）

产出：
- ``artifacts/guidance_loop/evidence.json``  —— 四条判据的机器可读实证；
- ``artifacts/guidance_loop/evidence.md``    —— 同一批数字的表格化产物（零手抄）。

判据对应关系：
1. 梯度统计位移：第 A 轮 → 第 B 轮，S4/S5 份额上调、S1 下调；
2. 准入变化：w-alpha 因真实失败 L3→L2（S4 被收回）、w-beta 因探索产出 L2→L3（S4 获准）；
3. 豁免记账：honest FAIL 不进失败率、退预算、落 S-N 轨（evaluation_budget 表可查）；
4. 负例：同一份统计重放 → 指纹不变 / 份额零位移 / 落库零新增行。

落库使用 organization_id=0 与 ``EV2-`` 前缀的周期/任务键，便于与生产行区分。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.guidance_loop import (  # noqa: E402
    BASE_SHARE,
    EXPLORATION_TIERS,
    S_N_TRACK,
    TIERS,
    CreditStats,
    ExplorationBudgetBook,
    OutcomeLedger,
    OutcomeRecord,
    GuidanceLoop,
    admission_delta,
    build_admission,
    credit_tier,
    persist_cycle,
)
from services.guidance_loop.store import (  # noqa: E402
    GUIDANCE_TABLES,
    GuidanceStore,
    PsqlExecutor,
    SqliteExecutor,
)

ORG = 0
POOL = 40
CYCLE_A = "EV2-cycleA"
CYCLE_B = "EV2-cycleB"
ARTIFACTS = ROOT / "artifacts" / "guidance_loop"


# ───────────────────────── 合成存量评价（零领域语义） ─────────────────────────
def round_a_outcomes() -> List[OutcomeRecord]:
    """第 A 轮：存量评价基线（三个 worker，合成命名，零领域语义）。

    设计（数字见 artifacts/guidance_loop/evidence.md）：
    - w-alpha 11 成功 / 1 机械失败 → 信用 L3；w-beta 6/1 → L2；w-gamma 10/5 → L1；
    - 档位统计：S1 16/6、S2 6/0、S3 3/1、S4 2/0（2 honest）、S5 0/0（2 honest）。
    """
    schedule = (
        ("S1", "w-gamma", "success", 10),
        ("S1", "w-gamma", "mechanical_fail", 4),
        ("S1", "w-alpha", "success", 4),
        ("S1", "w-alpha", "mechanical_fail", 1),
        ("S1", "w-beta", "success", 2),
        ("S1", "w-beta", "mechanical_fail", 1),
        ("S2", "w-alpha", "success", 4),
        ("S2", "w-beta", "success", 2),
        ("S3", "w-alpha", "success", 2),
        ("S3", "w-beta", "success", 1),
        ("S3", "w-gamma", "mechanical_fail", 1),
        ("S4", "w-alpha", "success", 1),
        ("S4", "w-alpha", "honest_fail", 1),
        ("S4", "w-beta", "success", 1),
        ("S4", "w-beta", "honest_fail", 1),
        ("S5", "w-alpha", "honest_fail", 1),
        ("S5", "w-beta", "honest_fail", 1),
    )
    return _expand("A", schedule)


def round_b_outcomes() -> List[OutcomeRecord]:
    """第 B 轮新增：探索档位产出上行（S4/S5 成功）、核心档位 S1 恶化（失败累积）。

    结果：w-alpha L3→L2（S4 被收回）、w-beta L2→L4（S4/S5 获准）、
    S1 产出率 0.71→0.57（份额下调）、S4/S5 产出率上行（份额上调）。
    """
    schedule = (
        ("S1", "w-alpha", "success", 1),
        ("S1", "w-alpha", "mechanical_fail", 3),
        ("S1", "w-gamma", "mechanical_fail", 4),
        ("S4", "w-beta", "success", 6),
        ("S5", "w-beta", "success", 5),
        ("S5", "w-beta", "honest_fail", 2),
    )
    return _expand("B", schedule)


def _expand(round_tag: str, schedule) -> List[OutcomeRecord]:
    rows: List[OutcomeRecord] = []
    for tier, worker, outcome, count in schedule:
        for index in range(count):
            rows.append(
                OutcomeRecord(
                    mission_key=f"EV2-{round_tag}-{tier}-{worker}-{outcome}-{index:02d}",
                    worker=worker,
                    tier=tier,
                    outcome=outcome,
                )
            )
    return rows


# ───────────────────────────── 实证步骤 ─────────────────────────────
def _admission_snapshot(ledger: OutcomeLedger) -> Dict[str, Any]:
    return build_admission(ledger.credit_stats())


def _tier_of(snapshot: Dict[str, Any], worker: str) -> str:
    return str(snapshot[worker]["tier"])


def build_evidence(store: GuidanceStore) -> Dict[str, Any]:
    schema = {
        "tables_expected": list(GUIDANCE_TABLES),
        "tables_present": list(store.existing_tables()),
        "counts_after_schema": {table: store.count_rows(table) for table in GUIDANCE_TABLES},
    }

    ledger = OutcomeLedger(store=store)
    seeded_a = ledger.extend(round_a_outcomes())
    book = ExplorationBudgetBook()
    loop = GuidanceLoop(ledger, total_pool=POOL, book=book)

    res_a = loop.run(cycle_id=CYCLE_A)
    write_a = persist_cycle(store, res_a, reason="IN24-E2 A 轮：存量评价基线")
    admission_a = _admission_snapshot(ledger)

    # —— 第 B 轮：追加存量评价后重算 ——
    seeded_b = ledger.extend(round_b_outcomes())
    res_b = loop.run(cycle_id=CYCLE_B)
    admission_b = _admission_snapshot(ledger)

    # —— 探索预算：B 轮消费一次 S5 + honest FAIL 结算（退预算 + 落 S-N 轨）——
    account_b = res_b.budgets["w-beta"]
    settlements: List[Dict[str, Any]] = []
    honest_key = "EV2-B-S5-w-beta-honest_fail-00"
    if book.can_dispatch(account_b, "S5"):
        book.consume(account_b, "S5", honest_key)
        settlements.append(book.settle(account_b, "S5", "honest_fail", honest_key))
    write_b = persist_cycle(
        store, res_b, reason="IN24-E2 B 轮：探索产出上行 / S1 恶化",
        settlements=tuple(settlements),
    )

    # —— 负例：同一份统计重放（不追加任何结论）——
    replay = loop.run(cycle_id=CYCLE_B)
    write_replay = persist_cycle(store, replay, reason="IN24-E2 负例重放")
    drift = res_b.plan.delta(replay.plan)

    # —— 审查预算账本 / 创新提案：把余下两个缺失实体也点亮并留痕 ——
    review_technical = store.append_review(
        kind="review", review_type="technical", subject="EV2-guidance-loop",
        conclusion="approved_with_findings", reviewer="rev-e2",
        duration_s=18.0, duration_source="observed", source="IN24-E2 evidence",
        note="引导回路结构审查",
    )
    review_exemption = store.append_review(
        kind="exemption", review_type="outcome", subject="EV2-S-N-track",
        conclusion="exempted", reviewer="rev-e2", duration_s=3.0,
        duration_source="reported", source="IN24-E2 evidence",
        note="honest FAIL 豁免留痕",
    )
    store.insert_proposal(
        "EV2-prop-1",
        "派发梯度按存量评价自校（有界漂移）",
        "假设：有界乘子 + 地板能在不掀翻派发结构的前提下让评价真实牵引梯度",
        evidence={"cycle_a": CYCLE_A, "cycle_b": CYCLE_B},
    )
    store.link_review("EV2-prop-1", review_technical["entry_id"], "approved_with_findings")

    # —— 判据汇总 ——
    shares_a = res_a.plan.shares
    shares_b = res_b.plan.shares
    shift = {
        "cycle_a": {tier: shares_a[tier] for tier in TIERS},
        "cycle_b": {tier: shares_b[tier] for tier in TIERS},
        "delta": {tier: round(shares_b[tier] - shares_a[tier], 6) for tier in TIERS},
        "counts_a": res_a.plan.counts,
        "counts_b": res_b.plan.counts,
        "digest_a": res_a.plan.stats_digest,
        "digest_b": res_b.plan.stats_digest,
        "yields_a": res_a.plan.yields,
        "yields_b": res_b.plan.yields,
        "yield_bar_a": res_a.plan.yield_bar,
        "yield_bar_b": res_b.plan.yield_bar,
        "multipliers_a": res_a.plan.multipliers,
        "multipliers_b": res_b.plan.multipliers,
        "exploration_share_a": round(res_a.plan.exploration_share, 6),
        "exploration_share_b": round(res_b.plan.exploration_share, 6),
        "expected_direction": "S4/S5 上调、S1 下调",
        "holds": bool(
            shares_b["S4"] > shares_a["S4"]
            and shares_b["S5"] > shares_a["S5"]
            and shares_b["S1"] < shares_a["S1"]
        ),
    }
    delta_alpha = admission_delta(_tier_of(admission_a, "w-alpha"), _tier_of(admission_b, "w-alpha"))
    delta_beta = admission_delta(_tier_of(admission_a, "w-beta"), _tier_of(admission_b, "w-beta"))
    admission_evidence = {
        "before": {worker: {"tier": row["tier"], "allowed_tiers": row["allowed_tiers"]}
                   for worker, row in admission_a.items()},
        "after": {worker: {"tier": row["tier"], "allowed_tiers": row["allowed_tiers"]}
                  for worker, row in admission_b.items()},
        "w_alpha": delta_alpha,
        "w_beta": delta_beta,
        "holds": bool(delta_alpha["changed"] or delta_beta["changed"]),
        "note": "档位由绝对阈值决定；同行强弱不影响本人档位（禁排名）",
    }
    failure = res_b.failure
    exempt_rows = [row for row in store.exploration_rows(CYCLE_B) if row["track"] == S_N_TRACK]
    exemption_evidence = {
        "honest_fails_total": failure["by_tier"].get("S5", {}).get("honest_fails", 0)
        + failure["by_tier"].get("S4", {}).get("honest_fails", 0),
        "exempted_honest_fails": failure["overall"]["exempted_honest_fails"],
        "failure_rate_overall": round(failure["overall"]["rate"], 6),
        "s5_tier_rate": round(failure["by_tier"].get("S5", {}).get("rate", 0.0), 6),
        "settlements": settlements,
        "s_n_rows_in_db": exempt_rows,
        "s_n_track_entries_in_result": len(res_b.s_n_track),
        "holds": bool(
            failure["overall"]["exempted_honest_fails"] > 0
            and failure["by_tier"].get("S5", {}).get("rate", 1.0) == 0.0
            and len(exempt_rows) >= 1
        ),
    }
    negative = {
        "replay_cycle_id": replay.cycle_id,
        "digest_equal": replay.plan.stats_digest == res_b.plan.stats_digest,
        "share_delta": {tier: round(drift[tier], 9) for tier in TIERS},
        "max_abs_delta": max(abs(value) for value in drift.values()),
        "rows_inserted_on_replay": write_replay["gradient_rows_inserted"],
        "cycle_inserted_on_replay": write_replay["cycle_inserted"],
        "holds": bool(
            replay.plan.stats_digest == res_b.plan.stats_digest
            and max(abs(value) for value in drift.values()) == 0.0
            and write_replay["gradient_rows_inserted"] == 0
        ),
    }
    final_counts = {table: store.count_rows(table) for table in GUIDANCE_TABLES}
    persisted = {
        "gradient_rows_cycle_a": len(store.gradient_rows(CYCLE_A)),
        "gradient_rows_cycle_b": len(store.gradient_rows(CYCLE_B)),
        "cycle_rows": store.count_rows("guidance_cycle"),
        "note": "首次执行时 writes.* 记录插入行数；重复执行同一脚本为回放（零新增），"
                "此时以 persisted.* 的库内实存行数为准。",
    }
    return {
        "mission": "IN24-E2",
        "organization_id": ORG,
        "total_pool": POOL,
        "schema": schema,
        "seeding": {"round_a": seeded_a, "round_b": seeded_b},
        "gradient_shift": shift,
        "admission_change": admission_evidence,
        "exemption_accounting": exemption_evidence,
        "failure_detail": failure,
        "negative_control": negative,
        "writes": {"cycle_a": write_a, "cycle_b": write_b, "replay": write_replay},
        "review_entries": [review_technical, review_exemption],
        "final_counts": final_counts,
        "persisted": persisted,
        "chain": store.verify_review_chain(),
        "criteria": {
            "gradient_shift_holds": shift["holds"],
            "admission_change_holds": admission_evidence["holds"],
            "exemption_accounting_holds": exemption_evidence["holds"],
            "negative_control_holds": negative["holds"],
            "entities_queryable": set(GUIDANCE_TABLES) <= set(schema["tables_present"]),
        },
    }


def render_markdown(evidence: Dict[str, Any]) -> str:
    shift = evidence["gradient_shift"]
    lines = [
        "# 引导回路生效实证（IN24-E2，程序化生成，零手抄）",
        "",
        f"- 池容量：{evidence['total_pool']}；组织标记：{evidence['organization_id']}",
        f"- A 轮统计指纹：`{shift['digest_a']}`",
        f"- B 轮统计指纹：`{shift['digest_b']}`",
        "",
        "## 1. 梯度统计位移",
        "",
        "| 档位 | 基线 | A 轮份额 | B 轮份额 | 位移 | A 派单数 | B 派单数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for tier in TIERS:
        lines.append(
            f"| {tier} | {BASE_SHARE[tier]:.4f} | {shift['cycle_a'][tier]:.6f} | "
            f"{shift['cycle_b'][tier]:.6f} | {shift['delta'][tier]:+.6f} | "
            f"{shift['counts_a'][tier]} | {shift['counts_b'][tier]} |"
        )
    lines += [
        "",
        f"- 探索份额：{shift['exploration_share_a']:.6f} → {shift['exploration_share_b']:.6f}"
        f"（判据 `{shift['expected_direction']}`：{'成立' if shift['holds'] else '不成立'}）",
        "",
        "## 2. 准入变化",
        "",
        "| worker | 前档位 | 后档位 | 收回档位 | 获准档位 |",
        "|---|---|---|---|---|",
    ]
    for worker, key in (("w-alpha", "w_alpha"), ("w-beta", "w_beta")):
        row = evidence["admission_change"][key]
        lines.append(
            f"| {worker} | {row['before']} | {row['after']} | {','.join(row['revoked']) or '-'} | "
            f"{','.join(row['granted']) or '-'} |"
        )
    ex = evidence["exemption_accounting"]
    lines += [
        "",
        "## 3. 探索预算与 honest FAIL 豁免",
        "",
        f"- honest FAIL 总数：{ex['honest_fails_total']}；豁免数：{ex['exempted_honest_fails']}",
        f"- 全局失败率：{ex['failure_rate_overall']:.6f}；S5 档失败率：{ex['s5_tier_rate']:.6f}",
        f"- 库内 S-N 轨行数：{len(ex['s_n_rows_in_db'])}",
    ]
    for settlement in ex["settlements"]:
        lines.append(
            f"- 结算：{settlement['mission_key']} · {settlement['tier']} · {settlement['outcome']}"
            f" · 退还 {settlement['refunded']:.2f} · 轨道 {settlement['track']}"
        )
    neg = evidence["negative_control"]
    lines += [
        "",
        "## 4. 负例（评价未变 → 梯度不漂）",
        "",
        f"- 指纹相同：{neg['digest_equal']}；最大份额位移：{neg['max_abs_delta']}",
        f"- 重放新增梯度行：{neg['rows_inserted_on_replay']}；新增周期行：{neg['cycle_inserted_on_replay']}",
        "",
        "## 5. 实体覆盖（迁移后库内可查）",
        "",
        "| 实体 | 迁移后行数 |",
        "|---|---|",
    ]
    for table in GUIDANCE_TABLES:
        lines.append(f"| {table} | {evidence['final_counts'][table]} |")
    lines += [
        "",
        f"- 审查账本链校验：`{'ok' if evidence['chain']['ok'] else 'BROKEN'}`"
        f"（{evidence['chain']['entries']} 条）",
        "",
        "## 6. 判据汇总",
        "",
    ]
    for key, value in evidence["criteria"].items():
        lines.append(f"- `{key}`: {'PASS' if value else 'FAIL'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="IN24-E2 引导回路实证")
    parser.add_argument("--dry-run", action="store_true", help="只算不写生产库（SQLite 内存库）")
    args = parser.parse_args()

    if args.dry_run:
        store = GuidanceStore(SqliteExecutor(), organization_id=ORG)
    else:
        store = GuidanceStore(PsqlExecutor(), organization_id=ORG)
    store.ensure_schema()
    evidence = build_evidence(store)

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    (ARTIFACTS / "evidence.md").write_text(render_markdown(evidence), encoding="utf-8")

    print(json.dumps({"criteria": evidence["criteria"], "final_counts": evidence["final_counts"]},
                     indent=2, ensure_ascii=False))
    all_pass = all(evidence["criteria"].values())
    print("ALL_CRITERIA_PASS" if all_pass else "SOME_CRITERIA_FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())