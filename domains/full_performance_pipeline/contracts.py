"""Interface contract layer for the full-performance integration pipeline.

本模块只做**与领域无关**的机制：字段/单位/类型的契约声明、JSON-Schema 导出、
以及“上游输出 schema 是否满足下游输入 schema”的逐字段判据（含显式单位换算登记）。

领域内容（船型、环境、公式、证据等级）由 `modules.py` / `synthetic.py` 注入。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------- evidence grades
REAL = "REAL"
MOCK = "MOCK"
STUB = "STUB"
WITHHELD = "WITHHELD"
NOT_SUPPORTED = "NOT_SUPPORTED"
GRADES: Tuple[str, ...] = (REAL, MOCK, STUB, WITHHELD, NOT_SUPPORTED)
#: 证据强度序（用于模块级取最弱项）
GRADE_RANK = {REAL: 4, WITHHELD: 3, STUB: 2, MOCK: 1, NOT_SUPPORTED: 0}


@dataclass(frozen=True)
class Field:
    """一个数据字段：名字 + 物理单位 + 类型。单位是契约判据的一部分。"""

    name: str
    unit: str
    kind: str = "float"  # float | int | array | obj
    desc: str = ""

    def as_json_schema(self) -> Dict[str, Any]:
        types = {"float": "number", "int": "integer", "array": "array", "obj": "object"}
        out: Dict[str, Any] = {
            "type": types.get(self.kind, "number"),
            "x-unit": self.unit,
            "description": self.desc,
        }
        if self.kind == "array":
            out["items"] = {"type": "number"}
        return out


@dataclass(frozen=True)
class Contract:
    """模块契约：requires=输入字段表，provides=输出字段表。"""

    module_id: str
    title: str
    version: str
    requires: Tuple[Field, ...]
    provides: Tuple[Field, ...]
    note: str = ""

    def input_schema(self) -> Dict[str, Any]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"{self.module_id}.input",
            "type": "object",
            "required": [f.name for f in self.requires],
            "properties": {f.name: f.as_json_schema() for f in self.requires},
        }

    def output_schema(self) -> Dict[str, Any]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"{self.module_id}.output",
            "type": "object",
            "required": [f.name for f in self.provides],
            "properties": {f.name: f.as_json_schema() for f in self.provides},
        }

    def provided_unit(self, name: str) -> Optional[str]:
        for f in self.provides:
            if f.name == name:
                return f.unit
        return None

    def required(self, name: str) -> Optional[Field]:
        for f in self.requires:
            if f.name == name:
                return f
        return None


# ---------------------------------------------------------------- unit adapters
KNOT_TO_MS = 0.5144444444444445
MS_TO_KNOT = 1.0 / KNOT_TO_MS
HOUR_TO_S = 3600.0

#: 显式换算登记表：只有登记在此的 (from,to) 才允许自动接线，其余一律判 unit_mismatch。
ADAPTERS: Dict[Tuple[str, str], Any] = {
    ("kn", "m/s"): lambda v: v * KNOT_TO_MS,
    ("m/s", "kn"): lambda v: v * MS_TO_KNOT,
    ("h", "s"): lambda v: v * HOUR_TO_S,
    ("s", "h"): lambda v: v / HOUR_TO_S,
    ("deg", "rad"): lambda v: v * 3.141592653589793 / 180.0,
    ("rad", "deg"): lambda v: v * 180.0 / 3.141592653589793,
    ("N", "kN"): lambda v: v / 1000.0,
    ("kN", "N"): lambda v: v * 1000.0,
    ("t", "kg"): lambda v: v * 1000.0,
    ("kg", "t"): lambda v: v / 1000.0,
    ("%", "-"): lambda v: v / 100.0,
    ("-", "%"): lambda v: v * 100.0,
}


def convert(value: Any, src_unit: str, dst_unit: str) -> Any:
    """按登记表换算；单位相同则原样返回。未登记换算抛 KeyError（=契约违例）。"""
    if src_unit == dst_unit:
        return value
    fn = ADAPTERS.get((src_unit, dst_unit))
    if fn is None:
        raise KeyError(f"NO_ADAPTER:{src_unit}->{dst_unit}")
    if isinstance(value, (list, tuple)):
        return [fn(v) for v in value]
    return fn(value)


# ---------------------------------------------------------------- edge verdicts
NATIVE = "native"          # 名字+单位+类型全匹配，直连
ADAPTED = "adapted"        # 单位经登记表换算后匹配
UNIT_MISMATCH = "unit_mismatch"
TYPE_MISMATCH = "type_mismatch"
MISSING = "missing"        # 上游根本不提供该字段


_KIND_OK = {
    ("float", "float"), ("int", "float"), ("float", "int"), ("int", "int"),
    ("array", "array"), ("obj", "obj"),
}


@dataclass
class EdgeCheck:
    producer: str
    consumer: str
    field: str
    status: str
    src_unit: str = ""
    dst_unit: str = ""
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "producer": self.producer,
            "consumer": self.consumer,
            "field": self.field,
            "status": self.status,
            "src_unit": self.src_unit,
            "dst_unit": self.dst_unit,
            "detail": self.detail,
        }


def check_edge(
    producer: Contract,
    consumer: Contract,
    mapping: Optional[Mapping[str, str]] = None,
    extra_provided: Iterable[str] = (),
) -> Dict[str, Any]:
    """校验“上游输出 → 下游输入”的字段级契约。

    mapping: 下游字段名 → 上游字段名（改名接线，需在契约里显式声明，不能隐式猜）。
    extra_provided: 上游之外的可注入字段（如环境/船型常量由配置层提供），用于区分
    “链路缺失”与“配置注入”。
    """
    mapping = dict(mapping or {})
    proto = {f.name: f for f in producer.provides}
    checks: List[EdgeCheck] = []
    for need in consumer.requires:
        src_name = mapping.get(need.name, need.name)
        src = proto.get(src_name)
        if src is None:
            if src_name in set(extra_provided):
                checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                        ADAPTED, "config", need.unit, "由配置层注入"))
            else:
                checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                        MISSING, "-", need.unit,
                                        f"上游无字段 {src_name}"))
            continue
        if (src.kind, need.kind) not in _KIND_OK:
            checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                    TYPE_MISMATCH, src.unit, need.unit,
                                    f"{src.kind}->{need.kind}"))
            continue
        if src.unit == need.unit:
            checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                    NATIVE, src.unit, need.unit, "直连"))
        else:
            try:
                convert(1.0, src.unit, need.unit)
            except KeyError:
                checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                        UNIT_MISMATCH, src.unit, need.unit, "无登记换算"))
            else:
                checks.append(EdgeCheck(producer.module_id, consumer.module_id, need.name,
                                        ADAPTED, src.unit, need.unit,
                                        f"{src.unit}->{need.unit}"))
    counts: Dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    total = len(checks)
    passed = counts.get(NATIVE, 0) + counts.get(ADAPTED, 0)
    return {
        "producer": producer.module_id,
        "consumer": consumer.module_id,
        "fields": total,
        "passed": passed,
        "contract_ok": passed == total,
        "counts": counts,
        "checks": [c.as_dict() for c in checks],
    }


def validate_payload(contract: Contract, payload: Mapping[str, Any], side: str = "output") -> Dict[str, Any]:
    """运行期载荷校验：必需字段齐备 + 数值可转 float + 无 NaN/Inf。"""
    spec: Sequence[Field] = contract.provides if side == "output" else contract.requires
    problems: List[str] = []
    for f in spec:
        if f.name not in payload:
            problems.append(f"missing:{f.name}")
            continue
        v = payload[f.name]
        if f.kind in ("float", "int"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                problems.append(f"not_number:{f.name}")
                continue
            if v != v or v in (float("inf"), float("-inf")):
                problems.append(f"non_finite:{f.name}")
    return {"side": side, "module_id": contract.module_id, "ok": not problems,
            "fields": len(spec), "problems": problems}