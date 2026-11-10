"""派发前可行性先验校准器（P475W1-N03）。

把「判据特征」映射为「验收概率」并做概率校准，供战役播种在**派发前**估计节点可行性：
判据复杂度（判据条数/数值阈值密度）、依赖拓扑（链深/扇出）、repo 成熟度（历史验收率）、
提案新颖度（与历史节点的文本最近邻距离）→ 逻辑回归原始分数 → Platt / isotonic 校准
→ 时间留出背测（早计划训练、晚计划测试）+ shuffle 负控（标签置换）。

模块边界
--------
* ``dataset``  —— 只读取数 + 标签口径 + 右删失剔除
* ``features`` —— 16 维因果特征（严格只用更早样本）+ 派发前路径的 ``HistoryState``
* ``calibrate``—— 逻辑回归 / Platt / isotonic(PAV) / Brier / BSS / AUC / ECE
* ``backtest`` —— 计划级时间三切分、四变体评估、标签置换负控
* ``report``   —— 判据裁决 + markdown + 图
* ``cli``      —— ``run``（背测落产物）/ ``predict``（派发前查概率）

零领域语义：不识别任何物理域词条，判据只按 JSON 结构计数。
"""

import os as _os

#: BLAS 线程数在「小矩阵 + 高频」场景下是纯粹的反优化：16 维特征上的 Newton 迭代，
#: 多线程 OpenBLAS 的同步开销是计算本身的 16 倍（实测 0.099s → 0.006s）。
#: 这里在导入 numpy 之前把默认线程数钉成 1（外部显式设置优先）。
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

from .calibrate import (IsotonicCalibrator, LogisticModel, PlattCalibrator, Standardizer,
                        auc, brier, brier_skill, ece, log_loss, reliability_table)
from .features import FEATURE_FAMILIES, FEATURE_NAMES, FeatureSet, HistoryState, extract_features
from .backtest import Pipeline, Split, evaluate, shuffle_control, time_split

__all__ = [
    "FEATURE_NAMES", "FEATURE_FAMILIES", "FeatureSet", "HistoryState", "extract_features",
    "LogisticModel", "PlattCalibrator", "IsotonicCalibrator", "Standardizer",
    "auc", "brier", "brier_skill", "ece", "log_loss", "reliability_table",
    "Pipeline", "Split", "evaluate", "shuffle_control", "time_split",
]