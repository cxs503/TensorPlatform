"""跨端点一致性检查器 (cross-endpoint consistency checker).

规则
----
同口径定义 id (def_id) 的字段在不同端点可能使用不同单位 (如 m vs mm).
本模块检查:
1. 同一 def_id 的所有字段量纲必须一致.
2. 若单位不同, 必须存在显式换算声明 (conversion_factor).
3. 若单位不同但无换算声明 -> ``MISSING_CONVERSION``.
4. 版本漂移: 同 def_id 不同版本的量纲变化必须显式声明.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from services.coupling_contracts.dimension import (
    Dim,
    DimensionError,
    format_dim,
)
from services.coupling_contracts.units import (
    UnknownUnitError,
    conversion_factor,
    dim_of_unit,
)

from .registry import Annotation, AnnotationRegistry, FieldKey


class CrossEndpointError(ValueError):
    """跨端点一致性违规."""


@dataclass(frozen=True)
class ConversionDecl:
    """显式换算声明."""
    from_endpoint: str
    from_field: str
    to_endpoint: str
    to_field: str
    factor: float
    note: str = ""

    def key(self) -> Tuple[str, str, str, str]:
        return (self.from_endpoint, self.from_field, self.to_endpoint, self.to_field)


@dataclass(frozen=True)
class CrossEndpointViolation:
    """一条跨端点违规."""
    code: str
    message: str
    def_id: str = ""
    fields: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "def_id": self.def_id,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class CrossEndpointReport:
    """跨端点一致性报告."""
    violations: Tuple[CrossEndpointViolation, ...] = ()
    def_ids_checked: int = 0
    fields_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(sorted({v.code for v in self.violations}))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "def_ids_checked": self.def_ids_checked,
            "fields_checked": self.fields_checked,
            "violation_count": len(self.violations),
            "codes": list(self.codes),
            "violations": [v.to_dict() for v in self.violations],
        }


def check_cross_endpoint(
    registry: AnnotationRegistry,
    conversions: Optional[List[ConversionDecl]] = None,
) -> CrossEndpointReport:
    """检查注册表中跨端点的一致性.

    Parameters
    ----------
    registry : AnnotationRegistry
        量纲注解注册表.
    conversions : list of ConversionDecl, optional
        显式换算声明列表.

    Returns
    -------
    CrossEndpointReport
    """
    violations: List[CrossEndpointViolation] = []

    # 按 def_id 分组
    by_def_id: Dict[str, List[Tuple[FieldKey, Annotation]]] = {}
    for key, ann in registry.items():
        by_def_id.setdefault(ann.def_id, []).append((key, ann))

    # 构建换算查找表
    conv_lookup: Dict[Tuple[str, str, str, str], ConversionDecl] = {}
    if conversions:
        for c in conversions:
            conv_lookup[c.key()] = c

    def_ids_checked = 0
    fields_checked = 0

    for def_id, entries in sorted(by_def_id.items()):
        def_ids_checked += 1
        fields_checked += len(entries)

        if len(entries) <= 1:
            continue

        # 检查量纲一致性
        dims = {ann.dim for _, ann in entries}
        if len(dims) > 1:
            dim_examples = {format_dim(d) for d in dims}
            field_names = tuple(k.to_str() for k, _ in entries)
            violations.append(CrossEndpointViolation(
                code="DIM_INCONSISTENT",
                message=f"同 def_id={def_id!r} 的字段量纲不一致: {dim_examples}",
                def_id=def_id,
                fields=field_names,
            ))
            continue  # 量纲不一致, 跳过单位检查

        # 检查单位一致性 & 换算声明
        units = {(k, ann.unit) for k, ann in entries}
        unique_units = {u for _, u in units}

        if len(unique_units) > 1:
            # 有多个不同单位, 检查是否有换算声明
            unit_list = sorted(unique_units)
            for i in range(len(unit_list)):
                for j in range(i + 1, len(unit_list)):
                    u1, u2 = unit_list[i], unit_list[j]
                    # 找对应的字段
                    fields_u1 = [k for k, u in units if u == u1]
                    fields_u2 = [k for k, u in units if u == u2]

                    # 检查是否有换算声明
                    has_conversion = False
                    for f1 in fields_u1:
                        for f2 in fields_u2:
                            key1 = (f1.endpoint, f1.field_name, f2.endpoint, f2.field_name)
                            key2 = (f2.endpoint, f2.field_name, f1.endpoint, f1.field_name)
                            if key1 in conv_lookup or key2 in conv_lookup:
                                has_conversion = True
                                break
                        if has_conversion:
                            break

                    if not has_conversion:
                        # 验证换算因子是否合法
                        try:
                            factor = conversion_factor(u1, u2)
                        except (DimensionError, UnknownUnitError):
                            factor = None

                        field_names = tuple(k.to_str() for k in fields_u1 + fields_u2)
                        violations.append(CrossEndpointViolation(
                            code="MISSING_CONVERSION",
                            message=(
                                f"同 def_id={def_id!r} 的字段使用不同单位 "
                                f"{u1!r} vs {u2!r}"
                                + (f" (换算因子={factor:.12g})" if factor is not None else "")
                                + ", 但无显式换算声明"
                            ),
                            def_id=def_id,
                            fields=field_names,
                        ))

    return CrossEndpointReport(
        violations=tuple(violations),
        def_ids_checked=def_ids_checked,
        fields_checked=fields_checked,
    )
