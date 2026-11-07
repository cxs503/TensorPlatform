"""外部效度校准（IN56-N07）：S×E 判级对下游真实结果的判别力。

模块边界（只读平台库，不写任何 dispatcher / 表结构）：

- :mod:`dataset`   把 `tensor_dev_missions` / `outcome_evaluations` /
  `mission_lifecycle_log` 拼成「特征时刻 < 标签时刻」的样本表；
- :mod:`models`    纯 numpy 估计器（L2 逻辑回归、CART 随机森林）+ AUC / Brier /
  可靠性曲线 + 采样 Shapley（无 sklearn / shap 依赖）；
- :mod:`analysis`  walk-forward（扩张窗口）时间序列交叉验证、基线对比、
  bootstrap 置信区间、出图；
- :mod:`acceptance` 七条机器判据 + 命令行入口。

纪律：本包**不**写数据库、**不**改任何既有模块；唯一输出是 JSON 产物与 PNG 图。
"""

__all__ = ["dataset", "models", "analysis", "acceptance"]

SCHEMA_VERSION = "extval.v1"