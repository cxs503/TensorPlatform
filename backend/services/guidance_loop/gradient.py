"""派发梯度自动配比：S1..S5 基线 60/25/10/4/1，随存量评价统计自校。

机制（可单测的纯函数，无 IO、无时钟、无随机）：
1. 每个档位由 :class:`TierStats` 给出存量评价统计（成功 / 机械失败 / honest FAIL）；
2. :func:`tier_yield` 用经验贝叶斯平滑把统计折成「产出率」——轮次少的档位不会
   因 1、2 个样本被极端化（先验强度 ``PRIOR_STRENGTH``）；
3. :func:`calibrate` 用产出率相对加权均值的偏离给基线乘一个**有界**增益
   （``GAIN`` / ``DRIFT_CAP``）：单轮乘子被夹在 [1-25%, 1+25%]，因此份额的相对
   漂移有上界（归一化后最坏约 1/(1-c) ≈ 1.67 倍，实测远小于此），
   避免统计噪声直接把派发结构掀翻；
4. 施加**地板**（``FLOOR_RATIO`` × 基线）：任何档位都不会被评价统计饿死；
5. :func:`allocate` 用最大余数法把份额折成整数派单数，总数精确守恒；
6. **探索档位整数保底**：池容量 ≥1 时 S4/S5 各至少 1 单（否则 1% 的 S5 份额永远
   折成 0 单、探索预算无处消耗），缺口从当前派单数最多的档位扣（确定序，无随机）。

honest FAIL 口径（与 :mod:`exploration` 一致）：
- 探索档位（S4/S5）的 honest FAIL **不计入失败率**，故也不压低该档位产出率；
- 非探索档位（S1..S3）不承认 honest FAIL 语义，一律按机械失败计入分母。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

TIERS: Tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
TIER_INDEX: Dict[str, int] = {tier: i for i, tier in enumerate(TIERS)}

#: 基线配比（S1 存量维护 60% / S2 能力补齐 25% / S3 验证审计 10% /
#: S4 探索 4% / S5 前沿 1%）。和必须为 1。
BASE_SHARE: Dict[str, float] = {
    "S1": 0.60,
    "S2": 0.25,
    "S3": 0.10,
    "S4": 0.04,
    "S5": 0.01,
}

#: 探索档位：honest FAIL 可豁免、可记探索预算。
EXPLORATION_TIERS: Tuple[str, ...] = ("S4", "S5")

#: 经验贝叶斯平滑强度（虚拟样本数）与产出率先验。
PRIOR_STRENGTH: float = 4.0
YIELD_PRIOR: float = 0.60

#: 产出率偏离的增益与单轮漂移上限（相对值）。
GAIN: float = 1.5
DRIFT_CAP: float = 0.25

#: 档位份额地板（相对基线的比例），保证任何档位都不会被统计饿死。
FLOOR_RATIO: float = 0.25

#: 份额输出精度。
SHARE_DECIMALS = 6

#: 探索档位整数保底（池容量 ≥1 时每档至少派 1 单）。
MIN_EXPLORATION_COUNT = 1


class GradientError(ValueError):
    """梯度计算输入不合法（未知档位 / 负数统计 / 非法池容量）。"""


@dataclass(frozen=True)
class TierStats:
    """单个档位的存量评价统计（只含计数，不含任何领域语义）。"""

    tier: str
    successes: int = 0
    mechanical_fails: int = 0
    honest_fails: int = 0
    stopped: int = 0

    def __post_init__(self) -> None:
        if self.tier not in TIER_INDEX:
            raise GradientError(f"未知档位: {self.tier!r}")
        for name in ("successes", "mechanical_fails", "honest_fails", "stopped"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise GradientError(f"{self.tier}.{name} 必须是整数")
            if value < 0:
                raise GradientError(f"{self.tier}.{name} 不能为负")

    # ---- 口径 ----
    @property
    def counted_fails(self) -> int:
        """计入失败率的失败数：探索档位剔除 honest FAIL。"""
        if self.tier in EXPLORATION_TIERS:
            return self.mechanical_fails
        return self.mechanical_fails + self.honest_fails

    @property
    def sampled(self) -> int:
        """参与产出率计算的样本量（成功 + 计入失败）。"""
        return self.successes + self.counted_fails

    @property
    def failure_rate(self) -> Optional[float]:
        return self.counted_fails / self.sampled if self.sampled else None

    def to_dict(self) -> Dict[str, object]:
        return {
            "tier": self.tier,
            "successes": self.successes,
            "mechanical_fails": self.mechanical_fails,
            "honest_fails": self.honest_fails,
            "stopped": self.stopped,
            "counted_fails": self.counted_fails,
            "sampled": self.sampled,
            "failure_rate": self.failure_rate,
        }


def tier_yield(stats: Optional[TierStats]) -> float:
    """档位产出率（经验贝叶斯平滑后的成功率）。"""
    if stats is None:
        return YIELD_PRIOR
    return (stats.successes + PRIOR_STRENGTH * YIELD_PRIOR) / (
        stats.sampled + PRIOR_STRENGTH
    )


@dataclass(frozen=True)
class Calibration:
    """一次标定的中间量，供审计与图表使用。"""

    shares: Dict[str, float]
    yields: Dict[str, float]
    yield_bar: float
    multipliers: Dict[str, float]
    floors: Dict[str, float]
    floored_tiers: Tuple[str, ...]
    stats: Dict[str, TierStats] = field(default_factory=dict)


def _normalized(weights: Mapping[str, float]) -> Dict[str, float]:
    total = sum(weights[tier] for tier in TIERS)
    if total <= 0:
        raise GradientError("权重和为 0，无法归一化")
    return {tier: weights[tier] / total for tier in TIERS}


def _apply_floors(
    shares: Mapping[str, float], floors: Mapping[str, float]
) -> Tuple[Dict[str, float], Tuple[str, ...]]:
    """施加地板并把缺口按比例从未触地档位扣除（迭代至稳定）。"""
    out = {tier: float(shares[tier]) for tier in TIERS}
    touched: Dict[str, bool] = {tier: False for tier in TIERS}
    for _ in range(len(TIERS) + 2):
        clamped = [t for t in TIERS if out[t] < floors[t] - 1e-12]
        if not clamped:
            break
        deficit = 0.0
        for tier in clamped:
            deficit += floors[tier] - out[tier]
            out[tier] = floors[tier]
            touched[tier] = True
        free = [t for t in TIERS if out[t] > floors[t] + 1e-12]
        free_total = sum(out[t] for t in free)
        if free_total <= 0:
            break
        for tier in free:
            out[tier] -= deficit * out[tier] / free_total
    return out, tuple(t for t in TIERS if touched[t])


def _fix_sum(shares: Mapping[str, float]) -> Dict[str, float]:
    """把浮点残差收敛到最大份额上，保证 sum == 1（1e-12 内）。"""
    out = {tier: float(shares[tier]) for tier in TIERS}
    residual = 1.0 - sum(out.values())
    top = max(TIERS, key=lambda t: out[t])
    out[top] += residual
    if out[top] < 0:
        raise GradientError("份额出现负值")
    return out


def _round_shares(shares: Mapping[str, float]) -> Dict[str, float]:
    rounded = {tier: round(shares[tier], SHARE_DECIMALS) for tier in TIERS}
    residual = round(1.0 - sum(rounded.values()), SHARE_DECIMALS)
    if residual:
        top = max(TIERS, key=lambda t: rounded[t])
        rounded[top] = round(rounded[top] + residual, SHARE_DECIMALS)
    return rounded


def calibrate(stats: Mapping[str, TierStats]) -> Calibration:
    """按存量评价统计校准基线份额。"""
    for tier in stats:
        if tier not in TIER_INDEX:
            raise GradientError(f"未知档位: {tier!r}")
    yields = {tier: tier_yield(stats.get(tier)) for tier in TIERS}
    yield_bar = sum(BASE_SHARE[tier] * yields[tier] for tier in TIERS)
    multipliers = {}
    for tier in TIERS:
        raw = 1.0 + GAIN * (yields[tier] - yield_bar)
        multipliers[tier] = min(1.0 + DRIFT_CAP, max(1.0 - DRIFT_CAP, raw))
    weights = {tier: BASE_SHARE[tier] * multipliers[tier] for tier in TIERS}
    shares = _normalized(weights)
    floors = {tier: FLOOR_RATIO * BASE_SHARE[tier] for tier in TIERS}
    floored_shares, floored_tiers = _apply_floors(shares, floors)
    floored_shares = _round_shares(_fix_sum(floored_shares))
    return Calibration(
        shares=floored_shares,
        yields=yields,
        yield_bar=yield_bar,
        multipliers=multipliers,
        floors=floors,
        floored_tiers=floored_tiers,
        stats=dict(stats),
    )


def allocate(shares: Mapping[str, float], total: int) -> Dict[str, int]:
    """最大余数法把份额折成整数派单数；总和精确等于 ``total``。"""
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise GradientError("池容量必须是非负整数")
    for tier in TIERS:
        if tier not in shares:
            raise GradientError(f"缺少档位份额: {tier}")
    raw = {tier: float(shares[tier]) * total for tier in TIERS}
    counts = {tier: int(math.floor(raw[tier])) for tier in TIERS}
    remainder = {tier: raw[tier] - counts[tier] for tier in TIERS}
    deficit = total - sum(counts.values())
    order = sorted(TIERS, key=lambda t: (-remainder[t], TIER_INDEX[t]))
    for tier in order[:deficit]:
        counts[tier] += 1
    # 探索档位整数保底：缺口从派单数最多的档位扣（同数按基线次序，确定可复现）。
    if total >= 1:
        for tier in EXPLORATION_TIERS:
            if counts[tier] >= MIN_EXPLORATION_COUNT:
                continue
            donor = max(
                (t for t in TIERS if t not in EXPLORATION_TIERS and counts[t] > 0),
                key=lambda t: (counts[t], -TIER_INDEX[t]),
                default=None,
            )
            if donor is None:
                break
            counts[donor] -= 1
            counts[tier] += MIN_EXPLORATION_COUNT
    return counts


def stats_digest(stats: Mapping[str, TierStats]) -> str:
    """存量评价统计的规范化指纹：统计不变 → 指纹不变 → 梯度不许漂。"""
    payload = {}
    for tier in TIERS:
        entry = stats.get(tier)
        if entry is None:
            payload[tier] = None
        else:
            payload[tier] = [
                entry.successes,
                entry.mechanical_fails,
                entry.honest_fails,
                entry.stopped,
            ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GradientPlan:
    """一轮派发梯度：份额 + 整数派单数 + 统计指纹（幂等键的一半）。"""

    cycle_id: str
    stats_digest: str
    total: int
    shares: Dict[str, float]
    counts: Dict[str, int]
    yields: Dict[str, float]
    yield_bar: float
    multipliers: Dict[str, float]
    floored_tiers: Tuple[str, ...] = ()

    @property
    def exploration_share(self) -> float:
        return sum(self.shares[t] for t in EXPLORATION_TIERS)

    @property
    def exploration_count(self) -> int:
        return sum(self.counts[t] for t in EXPLORATION_TIERS)

    def delta(self, other: "GradientPlan") -> Dict[str, float]:
        """两份梯度的份额位移（pp 之外的绝对值差，用于实证「统计位移」）。"""
        return {tier: self.shares[tier] - other.shares[tier] for tier in TIERS}

    def as_rows(self) -> Sequence[Dict[str, object]]:
        rows = []
        for tier in TIERS:
            rows.append(
                {
                    "cycle_id": self.cycle_id,
                    "tier": tier,
                    "base_share": BASE_SHARE[tier],
                    "calibrated_share": self.shares[tier],
                    "dispatched_count": self.counts[tier],
                    "tier_yield": self.yields[tier],
                    "yield_bar": self.yield_bar,
                    "multiplier": self.multipliers[tier],
                    "stats_digest": self.stats_digest,
                    "total_pool": self.total,
                }
            )
        return rows

    def to_dict(self) -> Dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "stats_digest": self.stats_digest,
            "total": self.total,
            "shares": dict(self.shares),
            "counts": dict(self.counts),
            "yields": dict(self.yields),
            "yield_bar": self.yield_bar,
            "multipliers": dict(self.multipliers),
            "floored_tiers": list(self.floored_tiers),
            "exploration_share": self.exploration_share,
            "exploration_count": self.exploration_count,
        }


def plan_gradient(
    stats: Mapping[str, TierStats],
    total: int,
    cycle_id: Optional[str] = None,
) -> GradientPlan:
    """生成本轮派发梯度。同一份统计 + 同一池容量 → 逐位相同的结果。"""
    calibration = calibrate(stats)
    digest = stats_digest(stats)
    return GradientPlan(
        cycle_id=cycle_id or f"cycle-{digest[:12]}",
        stats_digest=digest,
        total=total,
        shares=calibration.shares,
        counts=allocate(calibration.shares, total),
        yields=calibration.yields,
        yield_bar=calibration.yield_bar,
        multipliers=calibration.multipliers,
        floored_tiers=calibration.floored_tiers,
    )