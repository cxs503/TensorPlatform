"""覆盖率报表 (coverage report) — 端点量纲注解覆盖率 + sha256 可复算.

功能
----
1. 计算注册表中各端点的量纲注解覆盖率.
2. 生成 JSON 报表, 含 sha256 摘要 (可复算).
3. 支持基线对比 (单调上升检查).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .registry import AnnotationRegistry, FieldKey


@dataclass(frozen=True)
class EndpointCoverage:
    """单个端点的覆盖率."""
    endpoint: str
    total_fields: int
    annotated_fields: int
    missing_fields: Tuple[str, ...] = ()

    @property
    def coverage(self) -> float:
        if self.total_fields == 0:
            return 1.0
        return self.annotated_fields / self.total_fields

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "total_fields": self.total_fields,
            "annotated_fields": self.annotated_fields,
            "missing_fields": list(self.missing_fields),
            "coverage": round(self.coverage, 4),
        }


@dataclass(frozen=True)
class CoverageReport:
    """覆盖率报表."""
    endpoints: Tuple[EndpointCoverage, ...] = ()
    total_fields: int = 0
    annotated_fields: int = 0
    sha256: str = ""

    @property
    def coverage(self) -> float:
        if self.total_fields == 0:
            return 1.0
        return self.annotated_fields / self.total_fields

    @property
    def ok(self) -> bool:
        return self.coverage >= 1.0

    def to_dict(self) -> dict:
        return {
            "total_fields": self.total_fields,
            "annotated_fields": self.annotated_fields,
            "coverage": round(self.coverage, 4),
            "sha256": self.sha256,
            "endpoints": [ep.to_dict() for ep in self.endpoints],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def compute_coverage(
    registry: AnnotationRegistry,
    expected_fields: Optional[Dict[str, List[str]]] = None,
) -> CoverageReport:
    """计算量纲注解覆盖率.

    Parameters
    ----------
    registry : AnnotationRegistry
        量纲注解注册表.
    expected_fields : dict, optional
        {endpoint: [field_name, ...]} 期望的数值字段列表.
        若不提供, 则仅基于注册表中已有的注解计算.

    Returns
    -------
    CoverageReport
    """
    # 按端点分组
    by_endpoint: Dict[str, List[Tuple[str, bool]]] = {}

    if expected_fields:
        # 使用期望字段列表
        for endpoint, fields in expected_fields.items():
            by_endpoint.setdefault(endpoint, [])
            for field_name in fields:
                key = FieldKey(endpoint, field_name)
                has_annotation = registry.has(key)
                by_endpoint[endpoint].append((field_name, has_annotation))
    else:
        # 仅基于注册表
        for key in registry.keys():
            by_endpoint.setdefault(key.endpoint, []).append((key.field_name, True))

    endpoints: List[EndpointCoverage] = []
    total_fields = 0
    annotated_fields = 0

    for endpoint in sorted(by_endpoint.keys()):
        fields = by_endpoint[endpoint]
        ep_total = len(fields)
        ep_annotated = sum(1 for _, has_ann in fields if has_ann)
        missing = tuple(name for name, has_ann in fields if not has_ann)

        endpoints.append(EndpointCoverage(
            endpoint=endpoint,
            total_fields=ep_total,
            annotated_fields=ep_annotated,
            missing_fields=missing,
        ))
        total_fields += ep_total
        annotated_fields += ep_annotated

    # 构建报表内容 (不含 sha256)
    report_data = {
        "total_fields": total_fields,
        "annotated_fields": annotated_fields,
        "coverage": round(annotated_fields / total_fields, 4) if total_fields > 0 else 1.0,
        "endpoints": [ep.to_dict() for ep in endpoints],
    }

    # 计算 sha256
    canonical = json.dumps(report_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return CoverageReport(
        endpoints=tuple(endpoints),
        total_fields=total_fields,
        annotated_fields=annotated_fields,
        sha256=sha256,
    )


def coverage_sha256(report_json: str) -> str:
    """从 JSON 报表字符串重新计算 sha256 (用于验证)."""
    data = json.loads(report_json)
    # 移除已有的 sha256 字段
    data.pop("sha256", None)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_monotonic_increase(
    old_coverage: float,
    new_coverage: float,
) -> bool:
    """检查覆盖率是否单调上升."""
    return new_coverage >= old_coverage
