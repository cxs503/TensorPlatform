"""外部效度校准（external validity calibration）——IN58-N07。

评价体系回答"这单做得好不好"（S/E 判级），本模块回答更上一层的问题：
**这个判级对下游真实结果有没有判别力？**

下游真实结果（outcome）取自平台的生产记录，不取自判级本身：

- ``merged``   —— mission 的 PR 已合入主干（``result->'pr_gate'->>'state'``）
- ``closed``   —— PR 被关闭/拒收（丢弃）
- ``reverted`` —— 结果文本或 PR 历史出现回退/撤销/回归标记
- ``rework``   —— 该 mission 之后被要求修复/诊断/返工（后续 mission 明确点名），
                  或生命周期里出现 custody_requeue / orphan_detect 重做记录

四个判据量：walk-forward AUC（判别力）、校准曲线+Brier（概率语义）、
CUSUM（评分通胀）、随机标签负控（判别力非偶然）。

模块结构：

- :mod:`tools.external_validity.ledger`     —— 生产库 → 带标签的 cohort（只读）
- :mod:`tools.external_validity.metrics`    —— AUC / 逻辑回归 / 校准 / CUSUM
- :mod:`tools.external_validity.acceptance` —— J1..J7 机器裁据
- :mod:`tools.external_validity.plots`      —— 图
- :mod:`tools.external_validity.cli`        —— 命令行入口
"""

from .ledger import (  # noqa: F401
    GRADE_ORD,
    E_ORD,
    FEATURE_NAMES,
    build_cohort,
    cohort_features,
    grade_ordinal,
    e_ordinal,
    summarize,
)
from .metrics import (  # noqa: F401
    auc_score,
    brier_score,
    brier_skill_score,
    calibration_curve,
    cusum_detect,
    fit_logistic,
    walk_forward,
)

__all__ = [
    "GRADE_ORD",
    "E_ORD",
    "FEATURE_NAMES",
    "build_cohort",
    "cohort_features",
    "grade_ordinal",
    "e_ordinal",
    "summarize",
    "auc_score",
    "brier_score",
    "brier_skill_score",
    "calibration_curve",
    "cusum_detect",
    "fit_logistic",
    "walk_forward",
]
__version__ = "1.0.0"