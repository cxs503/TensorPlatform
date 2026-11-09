"""判据治理成熟化：阈值注册表 + 派生一致性引擎 + CI 漂移门 + 成熟度评分。

平台共性机制层：**不含任何领域语义的硬编码**。判据集、晶格权重集、探测参数、
阈值全部由 ``data/criteria_governance/*.json`` 工件注入；本包只提供通用机制：

- :mod:`.registry`     —— 阈值注册表（source_type 分类、derivation 链、负控剔除）
- :mod:`.derivation`   —— 派生一致性引擎（解析恒等 / 细化扫描 → 实测衰减指数）
- :mod:`.drift_gate`   —— CI 漂移门（sha256 钉桩 + 值级对照 + 篡改/假阳性统计）
- :mod:`.maturity`     —— 四维成熟度评分（覆盖率/可派生率/漂移检出率/假阳性）
- :mod:`.kernels`      —— 通用数值核（张量矩、对称群、细化扫描拟合）
"""

from __future__ import annotations

__all__ = ["registry", "derivation", "drift_gate", "maturity", "kernels", "probes"]