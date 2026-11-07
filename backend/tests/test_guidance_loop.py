"""引导回路（Guidance Loop）验收用例。

覆盖本战役 IN24-E2 的四条硬判据：
1. 回路生效实证：梯度随统计位移 / 准入随信用变化 / honest FAIL 豁免记账；
2. 缺失实体在迁移后库内可查（用 sqlite 执行**同一份迁移文件**做结构回归）；
3. 负例：评价统计未变时梯度不漂、落库不产生新行；
4. honest FAIL 不可覆盖。

零领域语义：夹具使用 alpha/beta 之类的合成命名。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.guidance_loop import (
    ADMISSION,
    BASE_SHARE,
    EXPLORATION_MULTIPLIER,
    EXPLORATION_TIERS,
    S_N_TRACK,
    TIERS,
    CreditStats,
    ExplorationBudgetBook,
    ExplorationExhausted,
    GradientPlan,
    GuidanceLoop,
    HonestFailProtectedError,
    OutcomeConflictError,
    OutcomeLedger,
    OutcomeRecord,
    TierStats,
    admission_delta,
    admission_for,
    allocate,
    assert_no_ranking,
    build_admission,
    credit_tier,
    plan_gradient,
    persist_cycle,
    stats_digest,
)
from services.guidance_loop.admission import RankingLeakError
from services.guidance_loop.gradient import (
    DRIFT_CAP,
    FLOOR_RATIO,
    MIN_EXPLORATION_COUNT,
    calibrate,
)
from services.guidance_loop.store import (
    GUIDANCE_TABLES,
    GuidanceStore,
    ProposalGateError,
    SqliteExecutor,
)

POOL = 40


def _stats(**kwargs) -> dict:
    """按档位构造统计：``_stats(s5=dict(successes=2, honest_fails=3))``。"""
    return {key.upper(): TierStats(tier=key.upper(), **fields) for key, fields in kwargs.items()}


# ───────────────── 1. 梯度：基线、守恒、自校、有界、地板 ─────────────────
def test_base_gradient_reproduces_60_25_10_4_1():
    plan = plan_gradient({}, 100)
    assert plan.shares == pytest.approx(BASE_SHARE, abs=1e-9)
    assert plan.counts == {"S1": 60, "S2": 25, "S3": 10, "S4": 4, "S5": 1}


@pytest.mark.parametrize("total", [0, 1, 4, 7, 40, 101, 253])
def test_counts_conserve_total_pool(total):
    counts = allocate(plan_gradient({}, total).shares, total)
    assert sum(counts.values()) == total
    assert all(value >= 0 for value in counts.values())


def test_gradient_shifts_with_accumulated_evaluation():
    """梯度统计位移：高产出档位份额上调、低产出档位份额下调。"""
    baseline = plan_gradient({}, POOL)
    empty = plan_gradient(_stats(), POOL)  # 零样本 → 等同于基线，不漂
    assert empty.shares == pytest.approx(baseline.shares, abs=1e-9)
    stats = _stats(
        s1=dict(successes=1, mechanical_fails=9),
        s4=dict(successes=12, mechanical_fails=0),
        s5=dict(successes=8, mechanical_fails=0),
    )
    moved = plan_gradient(stats, POOL)
    assert moved.shares["S4"] > baseline.shares["S4"]
    assert moved.shares["S5"] > baseline.shares["S5"]
    assert moved.shares["S1"] < baseline.shares["S1"]
    assert moved.stats_digest != baseline.stats_digest
    # 份额单调性：某档位产出率提高，其份额不降。
    better = plan_gradient(
        _stats(
            s1=dict(successes=1, mechanical_fails=9),
            s4=dict(successes=20, mechanical_fails=0),
            s5=dict(successes=8, mechanical_fails=0),
        ),
        POOL,
    )
    assert better.shares["S4"] >= moved.shares["S4"]


def test_gradient_multiplier_is_bounded_and_floor_prevents_starvation():
    extreme = _stats(
        s1=dict(successes=0, mechanical_fails=60),
        s2=dict(successes=0, mechanical_fails=60),
        s3=dict(successes=0, mechanical_fails=60),
        s4=dict(successes=0, mechanical_fails=60),
        s5=dict(successes=0, mechanical_fails=60),
    )
    plan = plan_gradient(extreme, POOL)
    for tier in TIERS:
        assert 1.0 - DRIFT_CAP - 1e-12 <= plan.multipliers[tier] <= 1.0 + DRIFT_CAP + 1e-12
        assert plan.shares[tier] >= FLOOR_RATIO * BASE_SHARE[tier] - 1e-9
    assert sum(plan.shares.values()) == pytest.approx(1.0, abs=1e-9)


def test_exploration_tiers_get_integer_floor():
    counts = plan_gradient(_stats(s5=dict(successes=0, mechanical_fails=30)), POOL).counts
    for tier in EXPLORATION_TIERS:
        assert counts[tier] >= MIN_EXPLORATION_COUNT


# ───────────────── 2. 负例：评价未变则梯度不漂 ─────────────────
def test_unchanged_evaluation_does_not_drift():
    stats = _stats(
        s1=dict(successes=20, mechanical_fails=4),
        s2=dict(successes=10, mechanical_fails=2),
        s3=dict(successes=6, mechanical_fails=1),
        s4=dict(successes=4, mechanical_fails=1, honest_fails=3),
        s5=dict(successes=2, mechanical_fails=0, honest_fails=2),
    )
    first = plan_gradient(stats, POOL)
    second = plan_gradient(stats, POOL)
    assert first.to_dict() == second.to_dict()
    assert first.stats_digest == second.stats_digest
    assert first.cycle_id == second.cycle_id
    assert all(abs(delta) == 0.0 for delta in first.delta(second).values())


# ───────────── 3. honest FAIL 口径：探索豁免 / 核心档不豁免 ─────────────
def test_honest_fail_exempt_on_exploration_tiers():
    tiers = _stats(s5=dict(successes=2, mechanical_fails=0, honest_fails=5))
    assert tiers["S5"].counted_fails == 0
    assert tiers["S5"].failure_rate == 0.0


def test_honest_fail_counts_as_failure_on_core_tiers():
    tiers = _stats(s1=dict(successes=0, mechanical_fails=0, honest_fails=2))
    assert tiers["S1"].counted_fails == 2
    assert tiers["S1"].failure_rate == 1.0


def test_exploration_budget_refund_and_s_n_track():
    book = ExplorationBudgetBook()
    account = book.open("w-alpha", "cycle-1", multiplier=1.0)
    assert account.allocated == pytest.approx(8.0)
    book.consume(account, "S5", "m-1")
    settlement = book.settle(account, "S5", "honest_fail", "m-1")
    assert settlement["refunded"] == pytest.approx(1.0)
    assert settlement["track"] == S_N_TRACK
    assert settlement["exempt_from_failure_rate"] is True
    assert account.refunded == pytest.approx(1.0)
    assert account.remaining == pytest.approx(account.allocated)
    assert len(account.s_n_entries) == 1


def test_exploration_budget_exhaustion_blocks_dispatch():
    book = ExplorationBudgetBook()
    account = book.open("w-beta", "cycle-1", multiplier=0.25)  # 2.0 单位
    for index in range(2):
        book.consume(account, "S4", f"m-{index}")
    assert book.can_dispatch(account, "S4") is False
    with pytest.raises(ExplorationExhausted):
        book.consume(account, "S4", "m-2")
    # 非探索档位永不消耗探索预算。
    assert book.can_dispatch(account, "S1") is False


# ───────────────── 4. 准入联动与禁排名 ─────────────────
def test_admission_ladder_is_monotone_and_l1_limited():
    for lower, higher in zip(ADMISSION, list(ADMISSION)[1:]):
        assert set(ADMISSION[lower]) <= set(ADMISSION[higher])
    assert admission_for("L1") == ("S1", "S2")
    assert admission_for("L5") == TIERS
    assert EXPLORATION_MULTIPLIER["L1"] == 0.0 < EXPLORATION_MULTIPLIER["L5"]
    assert credit_tier(CreditStats(worker="w-nobody")) == "L1"


def test_admission_changes_when_real_failures_accumulate():
    before = CreditStats(worker="w-alpha", successes=10, real_failures=1)
    assert credit_tier(before) == "L3"
    after = CreditStats(worker="w-alpha", successes=10, real_failures=4)
    assert credit_tier(after) == "L2"
    delta = admission_delta("L3", "L2")
    assert delta["changed"] is True and delta["revoked"] == ["S4"]
    # honest FAIL 不进失败率 → 不降档。
    exempt_only = CreditStats(worker="w-beta", successes=10, real_failures=1, honest_fails=6)
    assert credit_tier(exempt_only) == "L3"


def test_admission_is_not_a_ranking():
    solo = CreditStats(worker="w-alpha", successes=10, real_failures=1)
    peer = CreditStats(worker="w-stronger", successes=40, real_failures=0)
    alone = build_admission([solo])
    with_peer = build_admission([solo, peer])
    assert alone["w-alpha"]["tier"] == with_peer["w-alpha"]["tier"] == "L3"
    assert with_peer["w-alpha"]["allowed_tiers"] == alone["w-alpha"]["allowed_tiers"]
    assert isinstance(with_peer, dict)  # 映射而非列表：无位置/名次语义
    assert_no_ranking(with_peer)  # 结构字段自检通过


def test_ranking_field_leak_is_rejected():
    with pytest.raises(RankingLeakError):
        assert_no_ranking({"worker": "w-alpha", "rank": 1})
    with pytest.raises(RankingLeakError):
        assert_no_ranking({"rows": [{"leaderboard_position": 2}]})


# ───────────────── 5. 落库：迁移即建库、链、幂等、提案闸门 ─────────────────
@pytest.fixture()
def store():
    instance = GuidanceStore(SqliteExecutor())
    instance.ensure_schema()
    return instance


def test_migration_creates_all_guidance_entities(store):
    assert set(store.existing_tables()) == set(GUIDANCE_TABLES)
    summary = store.summary()
    for table in GUIDANCE_TABLES:
        assert summary["counts"][table] == 0
    assert summary["chain"]["ok"] is True


def test_review_ledger_hash_chain_and_tamper_detection(store):
    first = store.append_review(
        kind="review", review_type="technical", subject="alpha-1",
        conclusion="approved", reviewer="rev-1", duration_s=12.5, duration_source="observed",
    )
    second = store.append_review(
        kind="exemption", review_type="innovation", subject="beta-2",
        conclusion="exempted", reviewer="rev-2",
    )
    assert second["prev_hash"] == first["entry_hash"]
    assert store.verify_review_chain()["ok"] is True
    store.executor.raw.execute(
        "UPDATE review_budget_ledger SET conclusion='rejected' WHERE seq=1"
    )
    broken = store.verify_review_chain()
    assert broken["ok"] is False and broken["broken_at"] == 1


def test_outcome_ledger_protects_honest_fail(store):
    ledger = OutcomeLedger(store=store)
    assert ledger.record(OutcomeRecord("m-1", "w-alpha", "S5", "honest_fail")) == "inserted"
    assert ledger.record(OutcomeRecord("m-1", "w-alpha", "S5", "honest_fail")) == "duplicate"
    with pytest.raises(HonestFailProtectedError):
        ledger.record(OutcomeRecord("m-1", "w-alpha", "S5", "mechanical_fail"))
    with pytest.raises((HonestFailProtectedError, OutcomeConflictError)):
        store.insert_outcome(OutcomeRecord("m-1", "w-alpha", "S5", "mechanical_fail"))
    assert store.count_rows("guidance_outcome") == 1


def test_conflicting_outcome_append_only():
    ledger = OutcomeLedger()
    ledger.record(OutcomeRecord("m-2", "w-alpha", "S3", "success"))
    with pytest.raises(OutcomeConflictError):
        ledger.record(OutcomeRecord("m-2", "w-alpha", "S3", "mechanical_fail"))
    assert len(ledger.records()) == 1


def test_innovation_proposals_gated_by_innovation_review(store):
    assert store.insert_proposal("p-1", "合成提案 alpha", "假设：alpha 可迁移") is True
    assert store.get_proposal("p-1")["status"] == "submitted"
    with pytest.raises(ProposalGateError):
        store.promote_proposal("p-1")
    review = store.append_review(
        kind="review", review_type="innovation", subject="p-1",
        conclusion="approved_with_findings", reviewer="rev-3",
    )
    assert store.link_review("p-1", review["entry_id"], "approved_with_findings") == "approved"
    assert store.promote_proposal("p-1")["status"] == "approved"


# ───────────────── 6. 端到端：一轮周期生效 + 幂等 ─────────────────
def test_cycle_end_to_end_shows_drift_then_idempotence(store):
    ledger = OutcomeLedger(store=store)
    ledger.extend(
        [
            OutcomeRecord("a-1", "w-alpha", "S1", "success"),
            OutcomeRecord("a-2", "w-alpha", "S1", "success"),
            OutcomeRecord("a-3", "w-alpha", "S1", "mechanical_fail"),
            OutcomeRecord("t-1", "w-alpha", "S5", "success"),
            OutcomeRecord("t-2", "w-alpha", "S5", "honest_fail"),
            OutcomeRecord("t-3", "w-alpha", "S5", "honest_fail"),
        ]
    )
    loop = GuidanceLoop(ledger, total_pool=POOL)
    first = loop.run()
    written_first = persist_cycle(store, first)
    assert written_first["gradient_rows_inserted"] == 5
    assert store.verify_review_chain()["ok"] is True

    # 重复落库：零新增行（评价未变 → 梯度不漂 + 幂等）。
    again = loop.run()
    assert again.plan.stats_digest == first.plan.stats_digest
    assert again.plan.shares == first.plan.shares
    written_again = persist_cycle(store, again)
    assert written_again["gradient_rows_inserted"] == 0
    assert store.count_rows("task_gradient") == 5

    # 统计变化 → 梯度位移 + 新周期行。
    ledger.extend(
        [
            OutcomeRecord("t-4", "w-alpha", "S5", "success"),
            OutcomeRecord("t-5", "w-alpha", "S5", "success"),
            OutcomeRecord("t-6", "w-alpha", "S4", "success"),
            OutcomeRecord("t-7", "w-alpha", "S4", "success"),
            OutcomeRecord("a-4", "w-alpha", "S1", "mechanical_fail"),
            OutcomeRecord("a-5", "w-alpha", "S1", "mechanical_fail"),
        ]
    )
    shifted = loop.run()
    written_shifted = persist_cycle(store, shifted)
    assert shifted.plan.stats_digest != first.plan.stats_digest
    assert shifted.plan.shares["S5"] > first.plan.shares["S5"]
    assert shifted.plan.shares["S1"] < first.plan.shares["S1"]
    assert written_shifted["gradient_rows_inserted"] == 5
    assert store.count_rows("task_gradient") == 10
    rows = store.gradient_rows(shifted.cycle_id)
    assert len(rows) == 5
    assert sum(row["dispatched_count"] for row in rows) == POOL
    assert any(row["calibrated_share"] != BASE_SHARE[row["tier"]] for row in rows)


def test_cycle_settlements_land_on_s_n_track(store):
    ledger = OutcomeLedger(store=store)
    book = ExplorationBudgetBook()
    account = book.open("w-alpha", "cycle-net", multiplier=1.0)
    book.consume(account, "S4", "m-net-1")
    settlement = book.settle(account, "S4", "honest_fail", "m-net-1")
    ledger.record(OutcomeRecord("m-net-1", "w-alpha", "S4", "honest_fail"))
    loop = GuidanceLoop(ledger, total_pool=POOL, book=book)
    result = loop.run(cycle_id="cycle-net")
    written = persist_cycle(store, result, settlements=(settlement,))
    assert written["settlement_rows_inserted"] == 1
    rows = store.exploration_rows("cycle-net")
    s_n = [row for row in rows if row["track"] == S_N_TRACK]
    assert len(s_n) == 1 and s_n[0]["exempt"] is True
    assert result.failure["overall"]["exempted_honest_fails"] == 1
    assert result.failure["overall"]["fails"] == 0
    assert len(result.s_n_track) == 1


def test_digest_is_stable_and_sensitive():
    base = _stats(s1=dict(successes=3, mechanical_fails=1))
    same = _stats(s1=dict(successes=3, mechanical_fails=1))
    other = _stats(s1=dict(successes=3, mechanical_fails=2))
    assert stats_digest(base) == stats_digest(same)
    assert stats_digest(base) != stats_digest(other)
    assert calibrate(base).shares == calibrate(same).shares


def test_plan_serialisation_roundtrip():
    plan: GradientPlan = plan_gradient(_stats(s3=dict(successes=9, mechanical_fails=1)), POOL)
    payload = plan.to_dict()
    assert payload["total"] == POOL
    assert sum(payload["counts"].values()) == POOL
    assert len(plan.as_rows()) == 5
    assert all(row["stats_digest"] == plan.stats_digest for row in plan.as_rows())

# ───────────────── 7. 生产接线入口（tools/guidance/plan_cycle.py） ─────────────────
def test_plan_cycle_reads_db_outcomes_and_persists(store):
    from tools.guidance.plan_cycle import plan_from_store

    ledger = OutcomeLedger(store=store)
    ledger.extend(
        [
            OutcomeRecord(f"p-{index}", "w-alpha", "S4", "success") for index in range(8)
        ]
        + [OutcomeRecord("p-h1", "w-alpha", "S5", "honest_fail"),
           OutcomeRecord("p-h2", "w-beta", "S1", "success")]
    )
    payload = plan_from_store(store, total_pool=20, cycle_id="WAVE-1")
    assert payload["cycle_id"] == "WAVE-1"
    assert sum(payload["dispatch_plan"].values()) == 20
    assert payload["write"]["gradient_rows_inserted"] == 5
    assert payload["admission_allowed_tiers"]["w-alpha"] == ["S1", "S2", "S3", "S4"]
    rows = {row["tier"]: row for row in store.gradient_rows("WAVE-1")}
    assert rows["S4"]["calibrated_share"] > BASE_SHARE["S4"]
    # 重复调用幂等：不产生新行。
    again = plan_from_store(store, total_pool=20, cycle_id="WAVE-1")
    assert again["write"]["gradient_rows_inserted"] == 0
    assert store.count_rows("task_gradient") == 5


def test_plan_cycle_measurement_cycle_filter(store):
    from tools.guidance.plan_cycle import plan_from_store

    ledger = OutcomeLedger(store=store)
    ledger.record(OutcomeRecord("c1-a", "w-alpha", "S1", "success", ))
    store.insert_outcome(OutcomeRecord("c1-b", "w-alpha", "S1", "success"), cycle_id="C1")
    store.insert_outcome(OutcomeRecord("c2-a", "w-alpha", "S5", "success"), cycle_id="C2")
    payload_all = plan_from_store(store, total_pool=40, cycle_id="WAVE-all", persist=False)
    payload_c1 = plan_from_store(
        store, total_pool=40, cycle_id="WAVE-c1", measurement_cycle="C1", persist=False
    )
    assert payload_all["gradient"]["total"] == 40
    # C1 里没有探索档位样本，C2 里 S5 有成功 → 统计指纹必须不同。
    assert payload_all["gradient"]["stats_digest"] != payload_c1["gradient"]["stats_digest"]
