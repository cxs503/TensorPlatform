"""Drift Fingerprint Classifier — 五类漂移裁决（零领域语义）。

Categories:
  * ``bit_exact``      — 逐位重复（sha256 完全一致）
  * ``ulp_level``      — ULP 级差异（<1e-15 相对差）
  * ``threshold_level`` — 阈值级差异（<声明容差但 >ULP）
  * ``structural``     — 结构性差异（字段缺失/类型不匹配）
  * ``timeout_blocked`` — 超时/阻塞（无法完成比对）

Usage:
    from tools.drift_classifier import classify_drift_fingerprint
    result = classify_drift_fingerprint(baseline, candidate, declared_tol=0.01)
"""
from __future__ import annotations

from .classifier import (
    DRIFT_CLASSES,
    DRIFT_BIT_EXACT,
    DRIFT_ULP_LEVEL,
    DRIFT_THRESHOLD_LEVEL,
    DRIFT_STRUCTURAL,
    DRIFT_TIMEOUT_BLOCKED,
    ULP_THRESHOLD,
    classify_drift_fingerprint,
    classify_value_drift,
    compare_structures,
    compute_ulp_distance,
    fingerprint_of,
)
from .blocked_attribution import (
    BLOCKED_SUBCLASSES,
    BLOCKED_ENV_MISSING_DEP,
    BLOCKED_ENV_COLLECT_ERROR,
    BLOCKED_RESOURCE_TIMEOUT,
    BLOCKED_RESOURCE_OOM,
    BLOCKED_INFRA_FAILURE,
    attribute_blocked,
    is_blocked,
    can_rerun_scaled,
)

__all__ = [
    "DRIFT_CLASSES",
    "DRIFT_BIT_EXACT",
    "DRIFT_ULP_LEVEL",
    "DRIFT_THRESHOLD_LEVEL",
    "DRIFT_STRUCTURAL",
    "DRIFT_TIMEOUT_BLOCKED",
    "ULP_THRESHOLD",
    "classify_drift_fingerprint",
    "classify_value_drift",
    "compare_structures",
    "compute_ulp_distance",
    "fingerprint_of",
    "BLOCKED_SUBCLASSES",
    "BLOCKED_ENV_MISSING_DEP",
    "BLOCKED_ENV_COLLECT_ERROR",
    "BLOCKED_RESOURCE_TIMEOUT",
    "BLOCKED_RESOURCE_OOM",
    "BLOCKED_INFRA_FAILURE",
    "attribute_blocked",
    "is_blocked",
    "can_rerun_scaled",
]
