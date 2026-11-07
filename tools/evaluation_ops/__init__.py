"""评价运维（Evaluation Operations）成熟化链：cohort 不变量 · SLO · in-flight 评价 · 交叉模型复核。

模块分工
--------
- ``cohorts``   cohort 定义（ISO 周，成员只看 ``finished_at``）+ ``eval_cohorts`` 落库/冻结/漂移
- ``views``     7/30/90 天视图只筛 cohort，不改数；旧窗口口径负控
- ``slo``       P90 评价时延 ≤24h、积压 ≤20 行；积压年龄从 ``finished_at`` 起算
- ``inflight``  已完结但无评价的 mission → 单行增量评价（不碰调度面）
- ``robustness`` 行序/重复/分片不变性、分母为 0 的 None 语义、交叉验证、外部效度
- ``cross_model`` 交叉模型复核（两独立推理族一致性≥80%）+ 评分器噪声稳健性（翻转率≤5%）
"""
from __future__ import annotations

from .cohorts import (
    COHORT_TABLE,
    DECIDED_STATUSES,
    METRIC_FIELDS,
    SCHEMA as COHORT_SCHEMA,
    cohort_key,
    compute_cohorts,
    ensure_table,
    fetch_missions,
    fetch_records,
    is_frozen,
    load_stored,
    sql_crosscheck,
    upsert_cohorts,
    week_bounds,
)
from .slo import DEFAULT_SLO, evaluate_slo, ops_snapshot, slo_report
from .views import VIEW_DAYS, filter_view, legacy_window_sweep, window_independence
from .cross_model import (
    cross_model_verify,
    family_a_rule_scan,
    family_b_weighted,
    scorer_noise_robustness,
)

__all__ = [
    "COHORT_TABLE",
    "COHORT_SCHEMA",
    "DECIDED_STATUSES",
    "DEFAULT_SLO",
    "METRIC_FIELDS",
    "VIEW_DAYS",
    "cohort_key",
    "compute_cohorts",
    "cross_model_verify",
    "ensure_table",
    "evaluate_slo",
    "family_a_rule_scan",
    "family_b_weighted",
    "fetch_missions",
    "fetch_records",
    "filter_view",
    "is_frozen",
    "legacy_window_sweep",
    "load_stored",
    "ops_snapshot",
    "scorer_noise_robustness",
    "slo_report",
    "sql_crosscheck",
    "upsert_cohorts",
    "week_bounds",
    "window_independence",
]
