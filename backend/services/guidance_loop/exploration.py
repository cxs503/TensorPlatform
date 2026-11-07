"""探索预算记账：S4/S5 honest FAIL 豁免、落 S-N 轨、只增不改。

三条规则（本模块的全部语义）：
1. **预算记账**：每个 worker 每轮拿到一份探索额度（S4/S5 专用），派发时消耗；
2. **honest FAIL 豁免**：探索档位的 honest FAIL 是「诚实的失败」——真实执行、
   有据可查、结论为否。它不是事故，因此（a）不进入失败率分母，（b）把该次消耗
   退还（净额记账），（c）落 **S-N 轨**（探索中性轨）留痕；
3. **不可覆盖**：任何结论一经记账不得改写；honest FAIL 尤其不能被后续的
   mechanical_fail 覆盖（否则豁免会被追认式抹除）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .admission import CreditStats
from .gradient import EXPLORATION_TIERS, TIERS, TierStats

#: 探索中性轨标识。
S_N_TRACK = "S-N"

OUTCOMES: Tuple[str, ...] = ("success", "mechanical_fail", "honest_fail", "stopped")

#: 每轮基础探索额度（预算单位）。
DEFAULT_ALLOWANCE: Dict[str, float] = {"S4": 6.0, "S5": 2.0}

#: 单次派发的预算消耗。
UNIT_COST: Dict[str, float] = {"S4": 1.0, "S5": 1.0}


class ExplorationError(ValueError):
    """探索记账输入不合法。"""


class ExplorationExhausted(RuntimeError):
    """探索预算耗尽，该档位本轮不得再派。"""


class HonestFailProtectedError(RuntimeError):
    """honest FAIL 一经记账不可被覆盖。"""


class OutcomeConflictError(RuntimeError):
    """账本只增不改：同一任务键的结论不允许改写。"""


@dataclass(frozen=True)
class OutcomeRecord:
    """一条任务结论（评价统计的最小单位）。"""

    mission_key: str
    worker: str
    tier: str
    outcome: str

    def __post_init__(self) -> None:
        if not str(self.mission_key).strip():
            raise ExplorationError("mission_key 不能为空")
        if not str(self.worker).strip():
            raise ExplorationError("worker 不能为空")
        if self.tier not in TIERS:
            raise ExplorationError(f"未知档位: {self.tier!r}")
        if self.outcome not in OUTCOMES:
            raise ExplorationError(f"未知结论: {self.outcome!r}")

    @property
    def exempt(self) -> bool:
        """该条结论是否按 honest FAIL 豁免（仅探索档位成立）。"""
        return self.outcome == "honest_fail" and self.tier in EXPLORATION_TIERS

    def to_dict(self) -> Dict[str, object]:
        return {
            "mission_key": self.mission_key,
            "worker": self.worker,
            "tier": self.tier,
            "outcome": self.outcome,
            "exempt": self.exempt,
        }


def resolve_outcome_conflict(existing: str, new: str) -> str:
    """判定同一任务键的重复记账：``duplicate`` 幂等放行，其余拒绝。"""
    if existing == new:
        return "duplicate"
    if existing == "honest_fail":
        raise HonestFailProtectedError(
            f"honest FAIL 不可覆盖（既有 {existing!r} → 新 {new!r}）"
        )
    raise OutcomeConflictError(f"账本只增不改（既有 {existing!r} → 新 {new!r}）")


def failure_rates(records: Iterable[OutcomeRecord]) -> Dict[str, object]:
    """失败率口径：honest FAIL（探索档位）不计入分母，单独列为豁免量。"""
    by_tier: Dict[str, Dict[str, float]] = {}
    total_succ = total_fail = total_stop = exempt = 0
    for record in records:
        bucket = by_tier.setdefault(
            record.tier, {"successes": 0, "fails": 0, "honest_fails": 0, "stopped": 0}
        )
        if record.outcome == "success":
            bucket["successes"] += 1
            total_succ += 1
        elif record.outcome == "mechanical_fail":
            bucket["fails"] += 1
            total_fail += 1
        elif record.outcome == "honest_fail":
            bucket["honest_fails"] += 1
            if record.exempt:
                exempt += 1
            else:
                bucket["fails"] += 1
                total_fail += 1
        else:
            bucket["stopped"] += 1
            total_stop += 1
    for bucket in by_tier.values():
        denominator = bucket["successes"] + bucket["fails"]
        bucket["rate"] = bucket["fails"] / denominator if denominator else 0.0
    denominator = total_succ + total_fail
    return {
        "overall": {
            "successes": total_succ,
            "fails": total_fail,
            "stopped": total_stop,
            "exempted_honest_fails": exempt,
            "rate": total_fail / denominator if denominator else 0.0,
        },
        "by_tier": by_tier,
    }


@dataclass
class ExplorationAccount:
    """单个 worker 单轮的探索预算账户（净额记账，可审计）。"""

    worker: str
    cycle_id: str
    allocated: float
    multiplier: float = 1.0
    consumed: float = 0.0
    refunded: float = 0.0
    dispatches: List[Dict[str, object]] = field(default_factory=list)
    s_n_entries: List[Dict[str, object]] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return max(0.0, self.allocated - self.consumed + self.refunded)

    @property
    def exhausted(self) -> bool:
        return self.remaining < min(UNIT_COST.values())

    def to_dict(self) -> Dict[str, object]:
        return {
            "worker": self.worker,
            "cycle_id": self.cycle_id,
            "allocated": round(self.allocated, 6),
            "multiplier": self.multiplier,
            "consumed": round(self.consumed, 6),
            "refunded": round(self.refunded, 6),
            "remaining": round(self.remaining, 6),
            "exhausted": self.exhausted,
            "dispatches": list(self.dispatches),
            "s_n_entries": list(self.s_n_entries),
        }


class ExplorationBudgetBook:
    """探索预算账本：开账 → 消耗 → 结算（honest FAIL 退还并入 S-N 轨）。"""

    def __init__(
        self,
        allowance: Optional[Mapping[str, float]] = None,
        unit_cost: Optional[Mapping[str, float]] = None,
    ) -> None:
        self.allowance = dict(allowance or DEFAULT_ALLOWANCE)
        self.unit_cost = dict(unit_cost or UNIT_COST)
        for tier in self.allowance:
            if tier not in EXPLORATION_TIERS:
                raise ExplorationError(f"只有探索档位可开预算: {tier!r}")

    def open(self, worker: str, cycle_id: str, multiplier: float = 1.0) -> ExplorationAccount:
        if multiplier < 0:
            raise ExplorationError("预算倍数不能为负")
        allocated = sum(self.allowance.values()) * multiplier
        return ExplorationAccount(
            worker=worker,
            cycle_id=cycle_id,
            allocated=allocated,
            multiplier=multiplier,
        )

    def can_dispatch(self, account: ExplorationAccount, tier: str) -> bool:
        if tier not in EXPLORATION_TIERS:
            return False
        return account.remaining >= self.unit_cost.get(tier, 1.0)

    def consume(self, account: ExplorationAccount, tier: str, mission_key: str) -> float:
        if not self.can_dispatch(account, tier):
            raise ExplorationExhausted(
                f"{account.worker} 探索预算耗尽（remaining={account.remaining:.3f}），{tier} 本轮不得派发"
            )
        cost = self.unit_cost.get(tier, 1.0)
        account.consumed += cost
        account.dispatches.append({"mission_key": mission_key, "tier": tier, "cost": cost})
        return cost

    def settle(
        self,
        account: ExplorationAccount,
        tier: str,
        outcome: str,
        mission_key: str,
    ) -> Dict[str, object]:
        """结算一次探索派发；honest FAIL 退还消耗并落 S-N 轨。"""
        if outcome not in OUTCOMES:
            raise ExplorationError(f"未知结论: {outcome!r}")
        cost = self.unit_cost.get(tier, 1.0)
        record: Dict[str, object] = {
            "worker": account.worker,
            "cycle_id": account.cycle_id,
            "tier": tier,
            "mission_key": mission_key,
            "outcome": outcome,
            "cost": cost,
            "refunded": 0.0,
            "track": tier,
            "exempt_from_failure_rate": False,
        }
        if outcome == "honest_fail":
            account.refunded += cost
            record["refunded"] = cost
            record["track"] = S_N_TRACK
            record["exempt_from_failure_rate"] = True
            account.s_n_entries.append(
                {
                    "worker": account.worker,
                    "cycle_id": account.cycle_id,
                    "tier": tier,
                    "mission_key": mission_key,
                    "track": S_N_TRACK,
                    "refunded": cost,
                }
            )
        return record


class OutcomeLedger:
    """结论账本：内存索引 + 可选持久化（store 存在时由 store 承担只增不改的硬门）。"""

    def __init__(self, store: Optional[object] = None) -> None:
        self._records: Dict[str, OutcomeRecord] = {}
        self._store = store

    # ---- 写 ----
    def record(self, record: OutcomeRecord) -> str:
        existing = self._records.get(record.mission_key)
        if existing is not None:
            verdict = resolve_outcome_conflict(existing.outcome, record.outcome)
            return verdict
        if self._store is not None:
            self._store.insert_outcome(record)  # 持久化层同样执行只增不改/不可覆盖
        self._records[record.mission_key] = record
        return "inserted"

    def extend(self, records: Iterable[OutcomeRecord]) -> Dict[str, int]:
        tally = {"inserted": 0, "duplicate": 0}
        for record in records:
            tally[self.record(record)] += 1
        return tally

    # ---- 读 ----
    def records(self) -> Tuple[OutcomeRecord, ...]:
        return tuple(self._records.values())

    def tier_stats(self) -> Dict[str, TierStats]:
        counters: Dict[str, Dict[str, int]] = {
            tier: {"successes": 0, "mechanical_fails": 0, "honest_fails": 0, "stopped": 0}
            for tier in TIERS
        }
        for record in self._records.values():
            bucket = counters[record.tier]
            if record.outcome == "success":
                bucket["successes"] += 1
            elif record.outcome == "mechanical_fail":
                bucket["mechanical_fails"] += 1
            elif record.outcome == "honest_fail":
                bucket["honest_fails"] += 1
            else:
                bucket["stopped"] += 1
        return {tier: TierStats(tier=tier, **counters[tier]) for tier in TIERS}

    def credit_stats(self) -> List[CreditStats]:
        """按 worker 聚合信用统计（honest FAIL 单列，不进失败率）。"""
        counters: Dict[str, Dict[str, int]] = {}
        for record in self._records.values():
            bucket = counters.setdefault(
                record.worker,
                {"successes": 0, "real_failures": 0, "honest_fails": 0, "stopped": 0},
            )
            if record.outcome == "success":
                bucket["successes"] += 1
            elif record.outcome == "mechanical_fail":
                bucket["real_failures"] += 1
            elif record.outcome == "honest_fail":
                bucket["honest_fails"] += 1
            else:
                bucket["stopped"] += 1
        return [
            CreditStats(worker=worker, **counters[worker])
            for worker in sorted(counters)
        ]

    def failure_rates(self) -> Dict[str, object]:
        return failure_rates(self._records.values())

    def s_n_track(self) -> Tuple[Dict[str, object], ...]:
        entries = []
        for record in self._records.values():
            if record.exempt:
                entries.append(
                    {
                        "mission_key": record.mission_key,
                        "worker": record.worker,
                        "tier": record.tier,
                        "track": S_N_TRACK,
                    }
                )
        return tuple(entries)