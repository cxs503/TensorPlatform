"""评价体系外部效度校准（战役 DAG IN55-N07）。

把 S×E 判级当作一个**预测器**，去预测下游真实结果（merge / rollback /
rework / discard），用 walk-forward AUC、校准曲线（Brier/ECE）、评分通胀检测、
标签置换负控、跨仓一致性五条判据量化「评价是否自说自话」。

模块边界
--------
- ``metrics``   纯统计核（AUC-ROC / Brier / 分箱校准 / Wilson / 置换 / bootstrap）。
- ``dataset``   只读数据抽取（psql SELECT + 船仓合并台账 + JSONL 回放），把
                每单解析成 ``EvalRecord``（判级 + 下游终局 + 观测时间）。
- ``calibrate`` 编排：walk-forward 时间窗拟合/测试、负控、通胀、跨仓、判据裁决。
- ``run``       CLI：产出 ``artifacts/m6153/validity_calibration.json`` + 图。

零领域语义：本模块不出现学科词；判级语义由 ``tools.outcome_evaluations`` 提供。
"""

from __future__ import annotations

__all__ = ["metrics", "dataset", "calibrate"]