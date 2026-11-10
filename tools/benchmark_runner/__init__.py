"""平台自主基准测试引擎（P167W2-N08）。

四件套，全部**零领域语义**（本包内不出现任何应用域词条，算例全为合成数学）：

* :mod:`tools.benchmark_runner.runner`      —— 配置驱动的无人值守 runner（CLI）
* :mod:`tools.benchmark_runner.gate`        —— 预声明判据门（fail-closed：无判据=不通过）
* :mod:`tools.benchmark_runner.artifact`    —— 证据工件（JSON + PNG + sha256 + provenance）
* :mod:`tools.benchmark_runner.regression`  —— 与基线对比，漂移 >3% 告警

设计铁律
--------
1. **判据是数据**：阈值写在 suite 配置里（预声明），runner 代码内不含任何具体阈值常量；
2. **fail-closed**：缺判据/缺指标/非有限值/未知算子 —— 一律判 FAIL，绝不静默通过；
3. **零领域语义**：SUT 与参考解都经 ``"module:function"`` 点分路径注入，引擎只认数字。

快速用法::

    python3 -m tools.benchmark_runner.runner --config tools/benchmark_runner/configs/fx_suite.json \
        --out artifacts/benchmark_runner --baseline artifacts/benchmark_runner/baselines/fx_suite.json
"""
from __future__ import annotations

ENGINE_VERSION = "1.0.0"