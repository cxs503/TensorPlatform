"""量纲注解层 (dimension annotation layer) — 零领域语义的数值载荷量纲一致性机制.

本包在 coupling_contracts 量纲代数之上提供五件事:

============================  ==================================================
:mod:`registry`               注解注册表: 字段 -> (dim, unit, def_id, version)
:mod:`algebra`                代数检查器: 加减/比较/乘除/幂/指数/对数的量纲规则
:mod:`cross_endpoint`         跨端点一致性: 同口径字段跨端点单位换算校验
:mod:`schema_gate`            契约门: OpenAPI schema + 响应样本量纲扫描
:mod:`coverage`               覆盖率报表: 端点量纲注解覆盖率 + sha256 可复算
============================  ==================================================

领域词条/领域判据一律不进代码, 领域量列表只经配置数据包注入.
"""
from __future__ import annotations

from .algebra import (
    AlgebraError,
    AlgebraResult,
    check_add,
    check_compare,
    check_div,
    check_exp,
    check_log,
    check_mul,
    check_pow,
    check_sub,
)
from .coverage import (
    CoverageReport,
    compute_coverage,
    coverage_sha256,
)
from .cross_endpoint import (
    CrossEndpointError,
    CrossEndpointReport,
    check_cross_endpoint,
)
from .registry import (
    Annotation,
    AnnotationRegistry,
    AnnotationError,
    FieldKey,
    register,
    validate_annotation,
)
from .schema_gate import (
    GateResult,
    scan_openapi,
    scan_response_samples,
)

__all__ = [
    # registry
    "Annotation", "AnnotationRegistry", "AnnotationError", "FieldKey",
    "register", "validate_annotation",
    # algebra
    "AlgebraError", "AlgebraResult",
    "check_add", "check_sub", "check_compare", "check_mul", "check_div",
    "check_pow", "check_exp", "check_log",
    # cross_endpoint
    "CrossEndpointError", "CrossEndpointReport", "check_cross_endpoint",
    # schema_gate
    "GateResult", "scan_openapi", "scan_response_samples",
    # coverage
    "CoverageReport", "compute_coverage", "coverage_sha256",
]

__version__ = "1.0.0"
