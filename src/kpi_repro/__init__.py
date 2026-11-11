"""KPI-REPRO-95: 复现官记录通过率计量模块。

口径: KPI-REPRO-95 = 「复现任务 succeeded 且 工件在」/ 复现任务总数
目标: 95%

提供:
  - measure_kpi_repro(): 主计量函数
  - verify_kpi_repro(): 独立复核函数
  - generate_figures(): 结果图生成
"""
from __future__ import annotations

__version__ = "1.0.0"
