"""KPI-QC-RECHECK 计量能力（只读）：已合并 agent PR 的独立参考复核通过率。

对外入口：

* ``tools.kpi_qc_recheck.measure.measure(reader, days)`` —— Reader → 结果（纯聚合）
* ``tools.kpi_qc_recheck.measure.measure_with_session(db, days)`` —— 平台 API 内联调用
* ``python3 -m tools.kpi_qc_recheck.measure`` —— CLI，落盘 result/provenance/原始行
* ``python3 -m tools.kpi_qc_recheck.verify`` —— 独立第二实现复算 + benchmark（ref vs got）

注：包级采用惰性导入（``__getattr__``），这样 ``python3 -m tools.kpi_qc_recheck.measure``
不会因为包初始化已经导入子模块而触发 runpy 的双导入告警。
"""
from __future__ import annotations

from tools.kpi_qc_recheck import spec  # noqa: F401  （常量无依赖，可直接导入）

_LAZY = {
    "aggregate": "tools.kpi_qc_recheck.measure",
    "dedupe_denominator": "tools.kpi_qc_recheck.measure",
    "index_evaluations": "tools.kpi_qc_recheck.measure",
    "measure": "tools.kpi_qc_recheck.measure",
    "measure_with_session": "tools.kpi_qc_recheck.measure",
    "write_artifacts": "tools.kpi_qc_recheck.measure",
    "git_sensitivity": "tools.kpi_qc_recheck.measure",
}

__all__ = ["spec", *_LAZY]


def __getattr__(name: str):
    mod_name = _LAZY.get(name)
    if mod_name is None:
        raise AttributeError(name)
    import importlib
    return getattr(importlib.import_module(mod_name), name)