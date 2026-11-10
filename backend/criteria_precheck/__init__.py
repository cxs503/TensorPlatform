#!/usr/bin/env python3
"""``backend.criteria_precheck`` —— 判据预检门（7227 合并）。

三维能力：
* ``rounding``      舍入界预测与实测比对（float64 累加下界，E2）
* ``convergence``   收敛阶判定（p=0→SUSPECT, p=2→PRE-FEASIBLE，E3）
* ``precision_model`` 精度模型 / 不可达成性判定（E1）
* ``gold_standard`` 合成金标集生成与评估（E1）
* ``shadow_audit``  影子模式审计与灰度回滚（E6/E7）

平台铁律：本包零领域语义——只有数值方法/形状/方法论词表，域内容经任务书文本注入。
"""

from __future__ import annotations

from .rounding import (  # noqa: F401
    RoundingBound,
    predict_sum_lower_bound,
    measure_sum_error,
    validate_rounding_bound,
)
from .convergence import (  # noqa: F401
    ConvergenceResult,
    estimate_order,
    judge_convergence,
)
from .precision_model import (  # noqa: F401
    Achievability,
    assess_achievability,
    parse_criteria_thresholds,
)
from .gold_standard import (  # noqa: F401
    GoldStandard,
    generate_gold_standard,
    evaluate_gold_standard,
)
from .shadow_audit import (  # noqa: F401
    ShadowAudit,
    run_shadow_audit,
    verify_rollback_consistency,
)

__all__ = [
    "RoundingBound", "predict_sum_lower_bound", "measure_sum_error",
    "validate_rounding_bound",
    "ConvergenceResult", "estimate_order", "judge_convergence",
    "Achievability", "assess_achievability", "parse_criteria_thresholds",
    "GoldStandard", "generate_gold_standard", "evaluate_gold_standard",
    "ShadowAudit", "run_shadow_audit", "verify_rollback_consistency",
]
