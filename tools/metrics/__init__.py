"""KPI 反操纵审计模块（m9358）。

提供：
- kpi_registry.json: KPI 注册表（每条给 recompute_sql 或 not_recomputable=fail-closed）
- gaming_fixtures.py: 5 类操纵向量夹具生成器
- kpi_recompute.py: ledger 源 KPI 的可复算替代
"""
