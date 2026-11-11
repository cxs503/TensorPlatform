"""KPI-TRACE-100 计量工具包：终态任务带可溯报告工件比率。

入口：
    python3 -m tools.kpi_trace measure --outdir artifacts/kpi_trace_100
    python3 -m tools.kpi_trace figures --indir artifacts/kpi_trace_100
    python3 -m tools.kpi_trace doc --result artifacts/kpi_trace_100/result.json --out docs/KPI_TRACE_100_MEASUREMENT.md

当数据库不可用时，使用 --synthetic 标志生成合成底账（用于 CI/门禁验证）。
"""
from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["measure", "figures", "doc", "synthetic"]
