"""KPI-QC-RECHECK 规格：口径、目标、数据源。

本模块只放**常量与口径文字**，不含任何查询逻辑，便于审计时单独比对
「报告里写的口径」与「SQL 里实际实现的口径」是否一致（口径漂移检查）。

口径（协调官 2026-11-11 规划令）：

* 分母 N = 近 window 天内**已合并 agent PR**，按 ``(mission_id, repo, pr)`` 去重。
  数据源 ``mission_lifecycle_log``：``action='pr_gate'`` 且 ``detail->>'state'='merged'``。
* 分子 M = 该 mission 存在**独立参考复核通过**记录：
  ``reviewer_kind='independent'`` 且 ``review_status='reviewed'`` 且 ``status='confirmed'``。
  数据源 ``outcome_evaluations``（独立席位 S×E 复判记录）。
* 指标 = M / N × 100（单位 %），目标 100（治理语义：每条合并交付都应有独立复核结论）。

分子排除 ``auto_scan``：平台证据自动扫描不是"另一双眼睛"。把它算进分子会把读数虚高
（定义阶梯 D1 与 D2 的差值即为虚高量），故 D1 只作为口径阶梯的对照项上报，不参与主读数。
"""
from __future__ import annotations

KPI_KEY = "qc_recheck_rate"
KPI_TITLE = "已合并 agent PR 的独立参考复核通过率"
ANCHOR = "KPI-QC-RECHECK"
UNIT = "percent"
TARGET = 100.0
DEFAULT_WINDOW_DAYS = 30

# 分母谓词（逐词绑定 SQL，供审计逐条比对 queries.py）
DENOMINATOR_PREDICATES = {
    "merged": "mission_lifecycle_log.action = 'pr_gate' AND detail->>'state' = 'merged'",
    "window": "mission_lifecycle_log.created_at > now() - window",
    "dedupe": "(mission_id, detail->>'repo', detail->>'pr')",
}

# 分子谓词
NUMERATOR_PREDICATES = {
    "independent": "outcome_evaluations.reviewer_kind = 'independent'",
    "reviewed": "outcome_evaluations.review_status = 'reviewed'",
    "confirmed": "outcome_evaluations.status = 'confirmed'",
    "excluded": "reviewer_kind = 'auto_scan' 不计入分子（见模块 docstring）",
}

# 口径阶梯：同一分母，逐级收紧"什么算复核通过"
LADDER = {
    "D0": "有任何复核记录（任意 kind / 任意 status，含 pending 占位行）",
    "D1": "任意 kind 的 reviewed+confirmed（含 auto_scan）",
    "D2": "reviewer_kind='independent' 的 reviewed+confirmed（主读数）",
    "D3": "D2 且 cross_model_verified=true（异构模型交叉）",
}

DATA_SOURCES = {
    "denominator": "postgresql:///tensorplatform :: mission_lifecycle_log (action='pr_gate', detail->>'state'='merged')",
    "numerator": "postgresql:///tensorplatform :: outcome_evaluations (reviewer_kind/review_status/status)",
    "backlog": "postgresql:///tensorplatform :: outcome_evaluations (status='pending')",
    "cross_source": "git log origin/main --grep 'Merge #' （第三源，仅作分母敏感性对照）",
}

# 精度判据：计数必须逐位一致（严格相等），比率允许 1e-9 相对误差（仅排版舍入）
COUNT_REL_TOL = 0.0
RATE_REL_TOL = 1e-9
RATE_ABS_TOL_PP = 0.01