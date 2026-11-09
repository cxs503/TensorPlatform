"""评价外部效度校准 v2：判别力 / 校准 / 通胀 / 分解（战役节点 p422w3-n11）。

与既有 :mod:`tools.external_validity`（IN58-N07，CUSUM 口径）的分工：

- **复用**：标签与特征（``external_validity.ledger``）、AUC/Brier/校准曲线/逻辑回归/
  bootstrap（``external_validity.metrics``）——口径单一源，不另起一套。
- **新增**（本包）：
  1. ``stats``：Mann-Kendall 趋势检验（结修正 + 连续性修正）、Theil-Sen 斜率与
     Kendall 型 CI、Hanley-McNeil AUC 标准误与功效反解、同日条件配对一致性、Wilson 区间；
  2. ``analysis``：带样本外明细的 walk-forward、归因消融（grade_only vs full）、
     逐维度判别力分解、负控（100 次置乱）与正控（注入已知 AUC 的合成标签）；
  3. ``acceptance``：J1..J7 机器判据（PASS / IMPROVE / ALERT / WITHHELD / FAIL 五态，
     FAIL 只留给「仪器坏了」）；
  4. ``plots`` / ``report``：四图 + 六要素论文级 Markdown（数字全部从 JSON 注入）。
"""
from __future__ import annotations

from .analysis import (  # noqa: F401
    FEATURE_SETS,
    calibration_block,
    decomposition,
    identifiability,
    inflation_block,
    inject_labels,
    negative_control,
    recovery_control,
    samples_with_features,
    walk_forward_oos,
)
from .cohort import (  # noqa: F401
    DIMENSIONS,
    build_study,
    dump_snapshot,
    labeled,
    load_snapshot,
    retention,
    snapshot_payload,
    snapshot_sha256,
)
from .stats import (  # noqa: F401
    conditional_pair_concordance,
    hanley_mcneil_se,
    mann_kendall,
    n_for_se,
    theil_sen_slope,
    theil_sen_with_ci,
    wilson_interval,
)

__version__ = "2.0.0"

__all__ = [
    "DIMENSIONS", "FEATURE_SETS",
    "build_study", "labeled", "retention", "snapshot_payload", "dump_snapshot",
    "load_snapshot", "snapshot_sha256",
    "samples_with_features", "walk_forward_oos", "calibration_block",
    "inflation_block", "decomposition", "negative_control", "recovery_control",
    "identifiability", "inject_labels",
    "mann_kendall", "theil_sen_slope", "theil_sen_with_ci",
    "hanley_mcneil_se", "n_for_se", "conditional_pair_concordance",
    "wilson_interval",
    "__version__",
]