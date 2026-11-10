"""判级可靠性（inter-rater reliability）成熟化——战役 DAG IN55W3-N07。

S×E 判级体系的**测量可靠性**此前是空白：台账里只有一个等级，没有任何
「同一个成果换一个判级者会不会给出同样等级」的证据，因此所有 S×E 数字都缺
一个可靠性置信区间。本模块补上这一层：

- ``stats``      纯统计核：Krippendorff α(ordinal/nominal)、Gwet AC1/AC2、
                 Fleiss κ、ICC(1,1)/ICC(2,1)/ICC(2,k)、按单元（mission）重采样
                 bootstrap CI、判级者方差占比 φ_g 的矩估计。
- ``synthetic``  合成恢复夹具：由**总体极限**（可精确枚举的生成模型）算出
                 α_true，再校准判级者噪声使总体 α 命中目标值；注入
                 lenient/harsh 判级者（±0.5 级）后给出**理论** Δα 预测。
- ``dataset``    只读抽取 ``outcome_evaluations`` → (mission, grader, S, E,
                 reason, ts)；缺判级者身份的记录标 ``UNKEYED`` 计入覆盖率分母，
                 **不插补**。
- ``gate``       决策门：α < 0.667 强制第三评审判；单审判级标 UNRELIABLE，
                 禁作台账升账依据（附证据不足时的降级语义）。
- ``acceptance`` 判据 J1..J5 的机器裁决（判据数字、阈值、状态）。
- ``report`` / ``run``  论文级报告与 CLI。

边界：本模块零领域语义（不出现任何学科词）；等级语义由
``tools.outcome_evaluations`` 提供。
"""

from __future__ import annotations

__all__ = ["stats", "synthetic", "dataset", "gate", "acceptance", "report"]