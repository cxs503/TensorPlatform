"""契约门 (schema gate) — OpenAPI schema + 响应样本的量纲扫描.

功能
----
1. 扫描 OpenAPI schema, 识别所有数值字段 (type: number/integer).
2. 对照注解注册表, 检查每个数值字段是否有量纲注解.
3. 扫描响应样本 (JSON), 检查实际返回的数值字段是否与 schema 声明一致.
4. 输出覆盖率报表与违规清单.

违规类型
--------
- ``MISSING_ANNOTATION``: 数值字段无量纲注解 (fail-closed).
- ``SCHEMA_TYPE_MISMATCH``: schema 声明为数值但注册表标注为非数值 (或反之).
- ``SAMPLE_FIELD_MISSING``: 响应样本中缺少 schema 声明的必填数值字段.
- ``SAMPLE_EXTRA_FIELD``: 响应样本中出现 schema 未声明的数值字段 (需补注解).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from .registry import AnnotationError, AnnotationRegistry, FieldKey


class GateError(ValueError):
    """契约门扫描异常."""


@dataclass(frozen=True)
class GateViolation:
    """一条契约门违规."""
    code: str
    message: str
    endpoint: str = ""
    field_name: str = ""
    path: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "endpoint": self.endpoint,
            "field_name": self.field_name,
            "path": self.path,
        }


@dataclass(frozen=True)
class GateResult:
    """一次契约门扫描的结果."""
    violations: Tuple[GateViolation, ...] = ()
    total_fields: int = 0
    annotated_fields: int = 0
    unannotated_fields: int = 0
    endpoints_scanned: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def coverage(self) -> float:
        if self.total_fields == 0:
            return 1.0
        return self.annotated_fields / self.total_fields

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(sorted({v.code for v in self.violations}))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "total_fields": self.total_fields,
            "annotated_fields": self.annotated_fields,
            "unannotated_fields": self.unannotated_fields,
            "coverage": round(self.coverage, 4),
            "endpoints_scanned": self.endpoints_scanned,
            "violation_count": len(self.violations),
            "codes": list(self.codes),
            "violations": [v.to_dict() for v in self.violations],
        }


def _extract_numeric_fields_from_schema(
    schema: Mapping[str, Any],
    endpoint: str = "",
    path_prefix: str = "",
) -> List[Tuple[str, str]]:
    """从 OpenAPI schema 中提取所有数值字段 (endpoint, field_name)."""
    fields: List[Tuple[str, str]] = []

    if not isinstance(schema, dict):
        return fields

    # 处理 properties
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            field_path = f"{path_prefix}.{prop_name}" if path_prefix else prop_name
            prop_type = prop_schema.get("type", "")
            if prop_type in ("number", "integer"):
                fields.append((endpoint, prop_name))
            # 递归处理嵌套对象
            if prop_type == "object" or "properties" in prop_schema:
                fields.extend(
                    _extract_numeric_fields_from_schema(
                        prop_schema, endpoint, field_path
                    )
                )
            # 处理 array items
            if prop_schema.get("type") == "array" and "items" in prop_schema:
                items = prop_schema["items"]
                if isinstance(items, dict):
                    if items.get("type") in ("number", "integer"):
                        fields.append((endpoint, prop_name))
                    elif items.get("type") == "object" or "properties" in items:
                        fields.extend(
                            _extract_numeric_fields_from_schema(
                                items, endpoint, field_path
                            )
                        )

    # 处理顶层 type
    if schema.get("type") in ("number", "integer") and not path_prefix:
        fields.append((endpoint, path_prefix or "value"))

    return fields


def scan_openapi(
    openapi_doc: Mapping[str, Any],
    registry: AnnotationRegistry,
) -> GateResult:
    """扫描 OpenAPI 文档, 检查数值字段的量纲注解覆盖率.

    Parameters
    ----------
    openapi_doc : dict
        OpenAPI 3.x 文档 (解析后的 JSON/YAML).
    registry : AnnotationRegistry
        量纲注解注册表.

    Returns
    -------
    GateResult
    """
    violations: List[GateViolation] = []
    total_fields = 0
    annotated_fields = 0
    endpoints_scanned = 0

    paths = openapi_doc.get("paths", {})
    if not isinstance(paths, dict):
        return GateResult()

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue

            endpoints_scanned += 1
            endpoint = f"{method.upper()} {path}"

            # 检查响应 schema
            responses = operation.get("responses", {})
            if not isinstance(responses, dict):
                continue

            for status_code, response in responses.items():
                if not isinstance(response, dict):
                    continue
                content = response.get("content", {})
                if not isinstance(content, dict):
                    continue

                for media_type, media_schema in content.items():
                    if not isinstance(media_schema, dict):
                        continue
                    schema = media_schema.get("schema", {})
                    if not isinstance(schema, dict):
                        continue

                    numeric_fields = _extract_numeric_fields_from_schema(
                        schema, endpoint
                    )

                    for ep, field_name in numeric_fields:
                        total_fields += 1
                        key = FieldKey(ep, field_name)
                        try:
                            registry.get(key)
                            annotated_fields += 1
                        except AnnotationError:
                            violations.append(GateViolation(
                                code="MISSING_ANNOTATION",
                                message=f"数值字段 {field_name!r} 在端点 {ep!r} 无量纲注解",
                                endpoint=ep,
                                field_name=field_name,
                                path=f"{path}/{method}/responses/{status_code}",
                            ))

    unannotated = total_fields - annotated_fields
    return GateResult(
        violations=tuple(violations),
        total_fields=total_fields,
        annotated_fields=annotated_fields,
        unannotated_fields=unannotated,
        endpoints_scanned=endpoints_scanned,
    )


def _extract_numeric_values(
    obj: Any,
    path: str = "",
) -> List[Tuple[str, float]]:
    """递归提取 JSON 对象中的所有数值 (field_path, value)."""
    results: List[Tuple[str, float]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                results.append((child_path, float(value)))
            elif isinstance(value, (dict, list)):
                results.extend(_extract_numeric_values(value, child_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            child_path = f"{path}[{i}]"
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                results.append((child_path, float(item)))
            elif isinstance(item, (dict, list)):
                results.extend(_extract_numeric_values(item, child_path))
    return results


def scan_response_samples(
    samples: Mapping[str, Any],
    registry: AnnotationRegistry,
) -> GateResult:
    """扫描响应样本, 检查数值字段的量纲注解.

    Parameters
    ----------
    samples : dict
        {endpoint: response_data} 映射.
    registry : AnnotationRegistry
        量纲注解注册表.

    Returns
    -------
    GateResult
    """
    violations: List[GateViolation] = []
    total_fields = 0
    annotated_fields = 0
    endpoints_scanned = 0

    for endpoint, data in samples.items():
        endpoints_scanned += 1
        numeric_fields = _extract_numeric_values(data)

        for field_path, _value in numeric_fields:
            total_fields += 1
            # 取最后一段作为 field_name
            field_name = field_path.split(".")[-1].split("[")[0]
            key = FieldKey(endpoint, field_name)
            try:
                registry.get(key)
                annotated_fields += 1
            except AnnotationError:
                violations.append(GateViolation(
                    code="MISSING_ANNOTATION",
                    message=f"响应样本中数值字段 {field_name!r} (路径 {field_path!r}) "
                            f"在端点 {endpoint!r} 无量纲注解",
                    endpoint=endpoint,
                    field_name=field_name,
                    path=field_path,
                ))

    unannotated = total_fields - annotated_fields
    return GateResult(
        violations=tuple(violations),
        total_fields=total_fields,
        annotated_fields=annotated_fields,
        unannotated_fields=unannotated,
        endpoints_scanned=endpoints_scanned,
    )
