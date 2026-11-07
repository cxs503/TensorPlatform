"""credit_ledger 档位（L1..L5）→ 派发准入联动，且**禁排名**。

两条硬约束：
1. **档位是绝对阈值，不是相对名次**：每个 worker 的档位只由自己的历史统计决定，
   加入/移除任何同级或更强的同行都不会改变该 worker 的档位。这样评价统计才能
   牵引准入而不会退化成排行榜（“禁排名”）。
2. **honest FAIL 不降级**：探索档位的 honest FAIL 是学习成本，从档位计算的
   分母中剔除（与 :mod:`exploration` 同一口径）；只有机械失败才降档。

准入集合随档位变化：L1→{S1,S2}、L2→+S3、L3→+S4、L4/L5→+S5；
L1/L2 拿不到探索预算（倍数 0），L5 拿到 1.5 倍探索额度。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .gradient import TIERS

CREDIT_TIERS: Tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5")

#: 档位 → 该档位可被派发的任务档位集合（单调包含）。
ADMISSION: Dict[str, Tuple[str, ...]] = {
    "L1": ("S1", "S2"),
    "L2": ("S1", "S2", "S3"),
    "L3": ("S1", "S2", "S3", "S4"),
    "L4": TIERS,
    "L5": TIERS,
}

#: 档位 → 探索预算倍数（0 = 不发探索预算）。
EXPLORATION_MULTIPLIER: Dict[str, float] = {
    "L1": 0.0,
    "L2": 0.0,
    "L3": 1.0,
    "L4": 1.0,
    "L5": 1.5,
}

#: 绝对阈值阶梯（从高到低）。每档要求全部条件满足。
#: max_real_failures=None 表示不设上限。
TIER_THRESHOLDS: Tuple[Tuple[str, Dict[str, object]], ...] = (
    ("L5", {"min_successes": 25, "min_rate": 0.90, "max_real_failures": 0}),
    ("L4", {"min_successes": 15, "min_rate": 0.85, "max_real_failures": 1}),
    ("L3", {"min_successes": 8, "min_rate": 0.80, "max_real_failures": 3}),
    ("L2", {"min_successes": 3, "min_rate": 0.70, "max_real_failures": None}),
)

#: 准入行的结构字段（用于排名语义自检；不检查 worker 名等数据键，避免假阳性）。
ROW_FIELDS: Tuple[str, ...] = ("tier", "allowed_tiers", "exploration_multiplier", "evidence", "rule")

#: 输出里禁止出现的字段名片段（排名类语义词）。
FORBIDDEN_RANKING_TOKENS: Tuple[str, ...] = ("rank", "leaderboard", "standing", "order_by", "position")


class AdmissionError(ValueError):
    """准入输入/输出不合法。"""


class RankingLeakError(AssertionError):
    """输出中出现了排名语义字段（违反禁排名）。"""


@dataclass(frozen=True)
class CreditStats:
    """单个 worker 的信用统计（来自 credit_ledger 的窗口聚合）。"""

    worker: str
    successes: int = 0
    real_failures: int = 0
    honest_fails: int = 0
    stopped: int = 0

    def __post_init__(self) -> None:
        if not str(self.worker).strip():
            raise AdmissionError("worker 不能为空")
        for name in ("successes", "real_failures", "honest_fails", "stopped"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise AdmissionError(f"{self.worker}.{name} 必须是非负整数")

    @property
    def real_sampled(self) -> int:
        """计入失败率的分母：honest FAIL 被豁免。"""
        return self.successes + self.real_failures

    @property
    def real_rate(self) -> Optional[float]:
        return self.successes / self.real_sampled if self.real_sampled else None

    def to_dict(self) -> Dict[str, object]:
        return {
            "worker": self.worker,
            "successes": self.successes,
            "real_failures": self.real_failures,
            "honest_fails": self.honest_fails,
            "real_sampled": self.real_sampled,
            "real_rate": self.real_rate,
        }


def credit_tier(stats: CreditStats) -> str:
    """按绝对阈值给档位；没有样本 → L1。"""
    rate = stats.real_rate
    if rate is None:
        return "L1"
    for tier, rule in TIER_THRESHOLDS:
        if stats.successes < int(rule["min_successes"]):
            continue
        if rate < float(rule["min_rate"]):
            continue
        max_fail = rule["max_real_failures"]
        if max_fail is not None and stats.real_failures > int(max_fail):
            continue
        return tier
    return "L1"


def admission_for(tier: str) -> Tuple[str, ...]:
    if tier not in ADMISSION:
        raise AdmissionError(f"未知信用档位: {tier!r}")
    return ADMISSION[tier]


def admission_delta(before: str, after: str) -> Dict[str, object]:
    """两档之间准入集合的变化（准入变化实证用）。"""
    granted = [t for t in admission_for(after) if t not in admission_for(before)]
    revoked = [t for t in admission_for(before) if t not in admission_for(after)]
    return {
        "before": before,
        "after": after,
        "granted": granted,
        "revoked": revoked,
        "changed": bool(granted or revoked),
    }


def assert_no_ranking(payload: object) -> None:
    """校验**结构字段名**（含嵌套）不含排名语义；违反即抛 :class:`RankingLeakError`。

    只应传入结构描述（字段名），不要传入以数据为键的字典——否则 worker 名/工件名里
    偶然出现的词会误报（关键词假阳性是本仓已知坑）。
    """
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_l = str(key).lower()
                if any(token in key_l for token in FORBIDDEN_RANKING_TOKENS):
                    raise RankingLeakError(f"输出出现排名语义字段: {key!r}")
                stack.append(value)
        elif isinstance(node, (list, tuple, set)):
            stack.extend(node)


def build_admission(stats: Sequence[CreditStats]) -> Dict[str, Dict[str, object]]:
    """逐 worker 计算档位与准入集合。

    输出以 worker 为键的字典（**无序**）：不提供任何名次、序位或同行比较字段，
    刻意不返回列表，避免调用方按位置读成排行。
    """
    seen = set()
    out: Dict[str, Dict[str, object]] = {}
    for entry in stats:
        if entry.worker in seen:
            raise AdmissionError(f"worker 重复: {entry.worker!r}")
        seen.add(entry.worker)
        tier = credit_tier(entry)
        out[entry.worker] = {
            "tier": tier,
            "allowed_tiers": list(admission_for(tier)),
            "exploration_multiplier": EXPLORATION_MULTIPLIER[tier],
            "evidence": entry.to_dict(),
            "rule": dict(
                dict(TIER_THRESHOLDS).get(tier, {"min_successes": 0, "min_rate": 0.0, "max_real_failures": None})
            ),
        }
    # 结构自检：字段名不得带排名语义；且返回值刻意是映射（无位置语义）而非列表。
    assert not isinstance(out, list)
    assert_no_ranking({field: None for row in out.values() for field in row})
    return out


def allowed_tiers(admission_row: Mapping[str, object]) -> Tuple[str, ...]:
    return tuple(str(t) for t in admission_row.get("allowed_tiers", ()))  # type: ignore[arg-type]