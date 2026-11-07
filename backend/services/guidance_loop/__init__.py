"""引导回路（Guidance Loop）：评价 → 派发梯度的真实牵引接线。

设计立场（本包的存在理由）：**评价不是记账，是牵引**。存量评价统计必须能够
（1）改变派发梯度、（2）改变准入集合、（3）改变探索预算，否则评价体系退化为
台账。本包把这三条通路实现为可单测的纯函数 + 可审计的落库写入：

- :mod:`gradient`    —— 任务档位 S1..S5 的基线与「随存量评价统计自校」的自动配比；
- :mod:`admission`   —— credit_ledger 档位 L1..L5 → 准入集合联动（**禁排名**）；
- :mod:`exploration` —— 探索预算记账：S4/S5 honest FAIL 豁免、落 S-N 轨、不可覆盖；
- :mod:`store`       —— 迁移后可在库内查询的持久化（task_gradient / exploration_budget /
                        review_budget_ledger / innovation_proposals / guidance_outcome）；
- :mod:`loop`        —— 一轮派发周期的编排与幂等写入。

零领域语义：本包不出现任何领域专名，档位/角色/工件名全部由调用方以数据注入
（测试夹具使用 alpha/beta 之类的合成域名）。
"""
from __future__ import annotations

from .admission import (
    ADMISSION,
    CREDIT_TIERS,
    EXPLORATION_MULTIPLIER,
    CreditStats,
    admission_delta,
    admission_for,
    assert_no_ranking,
    build_admission,
    credit_tier,
)
from .exploration import (
    OutcomeConflictError,
    OutcomeLedger,
    OutcomeRecord,
    S_N_TRACK,
    ExplorationAccount,
    ExplorationBudgetBook,
    ExplorationError,
    ExplorationExhausted,
    HonestFailProtectedError,
    failure_rates,
    resolve_outcome_conflict,
)
from .gradient import (
    BASE_SHARE,
    EXPLORATION_TIERS,
    TIERS,
    GradientPlan,
    TierStats,
    allocate,
    calibrate,
    plan_gradient,
    stats_digest,
)
from .loop import CycleResult, GuidanceLoop, persist_cycle

__all__ = [
    "ADMISSION",
    "BASE_SHARE",
    "CREDIT_TIERS",
    "EXPLORATION_MULTIPLIER",
    "EXPLORATION_TIERS",
    "S_N_TRACK",
    "TIERS",
    "CreditStats",
    "CycleResult",
    "ExplorationAccount",
    "ExplorationBudgetBook",
    "ExplorationError",
    "ExplorationExhausted",
    "GradientPlan",
    "GuidanceLoop",
    "HonestFailProtectedError",
    "OutcomeConflictError",
    "OutcomeLedger",
    "OutcomeRecord",
    "TierStats",
    "admission_delta",
    "admission_for",
    "allocate",
    "assert_no_ranking",
    "build_admission",
    "calibrate",
    "credit_tier",
    "failure_rates",
    "persist_cycle",
    "plan_gradient",
    "resolve_outcome_conflict",
    "stats_digest",
]