"""一轮派发周期的编排：评价统计 → 梯度 / 准入 / 预算 → 落库（幂等）。

一轮周期的定义（也是本战役「回路生效」的实证单元）：
1. 从 :class:`~.exploration.OutcomeLedger` 取存量评价统计（带档位、带豁免口径）；
2. 出梯度：S1..S5 份额与整数派单数（:mod:`.gradient`）；
3. 出准入：逐 worker 的信用档位与可派档位集合（:mod:`.admission`，禁排名）；
4. 开预算：按准入倍数给每个 worker 开探索额度（:mod:`.exploration`）；
5. 落库：周期快照 + 梯度 5 行 + 预算账户 + S-N 轨留痕（:mod:`.store`）。

幂等：``cycle_id`` 缺省由统计指纹派生 —— 统计不变则 cycle_id 不变，
重复写入不会产生新行（负例「评价未变时梯度不漂」的机器判据）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .admission import CreditStats, build_admission, credit_tier, EXPLORATION_MULTIPLIER
from .exploration import (
    ExplorationAccount,
    ExplorationBudgetBook,
    OutcomeLedger,
    S_N_TRACK,
)
from .gradient import GradientPlan, plan_gradient

DEFAULT_TOTAL_POOL = 40


@dataclass(frozen=True)
class CycleResult:
    """一轮派发周期的完整结果（可直接序列化落库 / 出图）。"""

    cycle_id: str
    total_pool: int
    plan: GradientPlan
    admission: Dict[str, Dict[str, Any]]
    budgets: Dict[str, ExplorationAccount]
    failure: Dict[str, Any]
    s_n_track: Tuple[Dict[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "total_pool": self.total_pool,
            "gradient": self.plan.to_dict(),
            "admission": self.admission,
            "budgets": {worker: account.to_dict() for worker, account in self.budgets.items()},
            "failure": self.failure,
            "s_n_track": list(self.s_n_track),
        }


class GuidanceLoop:
    """引导回路：把评价统计接成可验证的派发牵引。"""

    def __init__(
        self,
        ledger: OutcomeLedger,
        total_pool: int = DEFAULT_TOTAL_POOL,
        book: Optional[ExplorationBudgetBook] = None,
        extra_credit_stats: Optional[Tuple[CreditStats, ...]] = None,
    ) -> None:
        self.ledger = ledger
        self.total_pool = total_pool
        self.book = book or ExplorationBudgetBook()
        self.extra_credit_stats = tuple(extra_credit_stats or ())

    def run(self, cycle_id: Optional[str] = None) -> CycleResult:
        stats = self.ledger.tier_stats()
        plan = plan_gradient(stats, self.total_pool, cycle_id=cycle_id)
        credit_stats = list(self.ledger.credit_stats()) + list(self.extra_credit_stats)
        admission = build_admission(credit_stats)
        budgets: Dict[str, ExplorationAccount] = {}
        for entry in credit_stats:
            multiplier = EXPLORATION_MULTIPLIER[credit_tier(entry)]
            budgets[entry.worker] = self.book.open(entry.worker, plan.cycle_id, multiplier)
        return CycleResult(
            cycle_id=plan.cycle_id,
            total_pool=self.total_pool,
            plan=plan,
            admission=admission,
            budgets=budgets,
            failure=self.ledger.failure_rates(),
            s_n_track=self.ledger.s_n_track(),
        )


def persist_cycle(
    store: Any,
    result: CycleResult,
    reason: str = "",
    settlements: Optional[Tuple[Mapping[str, Any], ...]] = None,
) -> Dict[str, Any]:
    """把一轮结果落库；重复调用不产生新行（幂等），返回写入统计。"""
    payload = {
        "shares": result.plan.shares,
        "counts": result.plan.counts,
        "yield_bar": result.plan.yield_bar,
        "exploration_share": result.plan.exploration_share,
        "stats_digest": result.plan.stats_digest,
        "total_pool": result.total_pool,
        "admission_tiers": {worker: row["tier"] for worker, row in result.admission.items()},
        "failure_overall": result.failure.get("overall", {}),
    }
    cycle_inserted = store.record_cycle(
        result.cycle_id, result.plan.stats_digest, result.total_pool, payload
    )
    gradient_inserted = store.record_gradient(result.plan, reason=reason or "guidance-loop")
    account_inserted = sum(
        1 for account in result.budgets.values() if store.record_exploration_account(account)
    )
    settlement_inserted = 0
    for settlement in settlements or ():
        settlement_inserted += 1 if store.record_settlement(settlement) else 0
    return {
        "cycle_id": result.cycle_id,
        "cycle_inserted": cycle_inserted,
        "gradient_rows_inserted": gradient_inserted,
        "exploration_account_rows_inserted": account_inserted,
        "settlement_rows_inserted": settlement_inserted,
        "stats_digest": result.plan.stats_digest,
        "total_pool": result.total_pool,
        "s_n_track_size": len(result.s_n_track),
        "track": S_N_TRACK,
    }