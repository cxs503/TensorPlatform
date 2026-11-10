"""注解注册表 (annotation registry) — 数值字段的量纲/单位/口径/版本声明.

每个数值字段挂一条注解:
    FieldKey(endpoint, field_name) -> Annotation(dim, unit, def_id, version)

设计要点
--------
1. **零领域语义**: 注册表本身不含任何领域词条; 领域量列表由外部 JSON 注入.
2. **fail-closed**: 新增数值字段无量纲注解 -> 注册表查询返回 AnnotationError.
3. **版本漂移检测**: 同 def_id 不同版本的量纲向量变化必须显式声明, 否则报
   ``VERSION_DRIFT``.
4. **口径定义 id**: 同一物理口径 (如"长度") 在不同端点可能用不同单位 (m/mm),
   但 def_id 相同; 跨端点一致性由 cross_endpoint 模块负责.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from services.coupling_contracts.dimension import (
    Dim,
    DimensionError,
    ZERO,
    dim as _dim,
    format_dim,
    is_dimensionless,
    parse_dim,
)
from services.coupling_contracts.units import (
    UnknownUnitError,
    dim_of_unit,
    parse_unit,
    unit_of,
)


class AnnotationError(ValueError):
    """注解不合法 / 缺失."""


@dataclass(frozen=True)
class FieldKey:
    """字段唯一标识: 端点 + 字段名."""
    endpoint: str
    field_name: str

    def __hash__(self) -> int:
        return hash((self.endpoint, self.field_name))

    def to_str(self) -> str:
        return f"{self.endpoint}.{self.field_name}"


@dataclass(frozen=True)
class Annotation:
    """一个字段的量纲注解.

    Attributes
    ----------
    dim : Dim
        量纲向量 (M, L, T, I, Th, N, J).
    unit : str
        SI 单位串 (如 "m", "kN", "m/s").
    def_id : str
        口径定义 id (同一物理口径在不同端点共享同一 def_id).
    version : int
        注解版本号 (>=1).
    angle_mode : str, optional
        角度制式: ``"rad"`` (默认, 无量纲) / ``"deg"`` (度制, 需显式换算).
        仅当量纲为无量纲且字段语义为角度时使用.
    """
    dim: Dim
    unit: str
    def_id: str
    version: int
    angle_mode: str = "rad"

    def __post_init__(self) -> None:
        if self.version < 1:
            raise AnnotationError(f"version 必须 >= 1, 收到 {self.version}")
        if self.angle_mode not in ("rad", "deg"):
            raise AnnotationError(f"angle_mode 必须是 'rad' 或 'deg', 收到 {self.angle_mode!r}")

    def to_dict(self) -> dict:
        return {
            "dim": format_dim(self.dim),
            "unit": self.unit,
            "def_id": self.def_id,
            "version": self.version,
            "angle_mode": self.angle_mode,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "Annotation":
        try:
            dim_str = str(d["dim"])
            dim_val = parse_dim(dim_str)
        except (KeyError, DimensionError) as exc:
            raise AnnotationError(f"注解 dim 字段不可解析: {exc}") from exc
        unit = str(d.get("unit", ""))
        if not unit:
            raise AnnotationError("注解缺少 unit 字段")
        def_id = str(d.get("def_id", ""))
        if not def_id:
            raise AnnotationError("注解缺少 def_id 字段")
        version = int(d.get("version", 1))
        angle_mode = str(d.get("angle_mode", "rad"))
        return cls(dim=dim_val, unit=unit, def_id=def_id, version=version, angle_mode=angle_mode)


def validate_annotation(ann: Annotation) -> List[str]:
    """校验一条注解的内在一致性.

    返回违规原因列表; 空列表 = 通过.
    """
    reasons: List[str] = []
    # 1. 单位量纲与声明量纲必须一致
    try:
        resolved = dim_of_unit(ann.unit)
    except (UnknownUnitError, DimensionError) as exc:
        reasons.append(f"UNKNOWN_UNIT: {exc}")
        return reasons  # 无法继续
    if resolved != ann.dim:
        reasons.append(
            f"DIM_MISMATCH: 声明 dim={format_dim(ann.dim)} "
            f"与单位 {ann.unit!r} 推导 {format_dim(resolved)} 不一致"
        )
    # 2. 角度模式检查: 只有无量纲字段才能用 angle_mode
    if ann.angle_mode != "rad" and not is_dimensionless(ann.dim):
        reasons.append(
            f"ANGLE_ON_DIMENSIONED: angle_mode={ann.angle_mode!r} "
            f"但量纲 {format_dim(ann.dim)} 非无量纲"
        )
    # 3. 单位必须在登记表中
    try:
        unit_of(ann.unit)
    except UnknownUnitError:
        # 尝试解析复合单位
        try:
            parse_unit(ann.unit)
        except DimensionError:
            reasons.append(f"UNIT_NOT_REGISTERED: {ann.unit!r}")
    return reasons


class AnnotationRegistry:
    """量纲注解注册表.

    线程安全 (读写分离): 注册表一旦构建即为不可变; 新版本通过构建新实例实现.
    """

    def __init__(self, annotations: Optional[Mapping[FieldKey, Annotation]] = None) -> None:
        self._data: Dict[FieldKey, Annotation] = dict(annotations) if annotations else {}

    def get(self, key: FieldKey) -> Annotation:
        """获取字段注解; 缺失 -> :class:`AnnotationError` (fail-closed)."""
        ann = self._data.get(key)
        if ann is None:
            raise AnnotationError(
                f"MISSING_ANNOTATION: 字段 {key.to_str()!r} 无量纲注解 "
                f"(fail-closed: 新增数值字段必须挂注解)"
            )
        return ann

    def register(self, key: FieldKey, ann: Annotation) -> None:
        """注册一条注解 (覆盖已有)."""
        reasons = validate_annotation(ann)
        if reasons:
            raise AnnotationError(f"注解校验失败 [{key.to_str()}]: {'; '.join(reasons)}")
        self._data[key] = ann

    def bulk_register(self, items: Mapping[FieldKey, Annotation]) -> None:
        """批量注册."""
        for key, ann in items.items():
            self.register(key, ann)

    def has(self, key: FieldKey) -> bool:
        return key in self._data

    def keys(self) -> List[FieldKey]:
        return list(self._data.keys())

    def values(self) -> List[Annotation]:
        return list(self._data.values())

    def items(self) -> List[Tuple[FieldKey, Annotation]]:
        return list(self._data.items())

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict:
        return {
            key.to_str(): ann.to_dict()
            for key, ann in sorted(self._data.items(), key=lambda x: x[0].to_str())
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Mapping[str, object]]) -> "AnnotationRegistry":
        reg = cls()
        for key_str, ann_dict in d.items():
            parts = key_str.split(".", 1)
            if len(parts) != 2:
                raise AnnotationError(f"字段键格式错误: {key_str!r} (需 endpoint.field_name)")
            endpoint, field_name = parts
            ann = Annotation.from_dict(ann_dict)
            reg.register(FieldKey(endpoint, field_name), ann)
        return reg

    @classmethod
    def from_json_file(cls, path: str) -> "AnnotationRegistry":
        import pathlib
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def check_version_drift(self, def_id: str) -> List[dict]:
        """检查同一 def_id 在不同版本间的量纲漂移.

        返回漂移报告列表; 空列表 = 无漂移.
        """
        # 收集同一 def_id 的所有注解, 按 version 排序
        by_version: Dict[int, List[Tuple[FieldKey, Annotation]]] = {}
        for key, ann in self._data.items():
            if ann.def_id == def_id:
                by_version.setdefault(ann.version, []).append((key, ann))

        if len(by_version) <= 1:
            return []

        drifts: List[dict] = []
        versions = sorted(by_version.keys())
        for i in range(1, len(versions)):
            v_prev, v_curr = versions[i - 1], versions[i]
            prev_anns = {k: a for k, a in by_version[v_prev]}
            curr_anns = {k: a for k, a in by_version[v_curr]}

            # 检查共同字段的量纲变化
            common_keys = set(prev_anns.keys()) & set(curr_anns.keys())
            for key in sorted(common_keys, key=lambda k: k.to_str()):
                prev_dim = prev_anns[key].dim
                curr_dim = curr_anns[key].dim
                if prev_dim != curr_dim:
                    drifts.append({
                        "def_id": def_id,
                        "field": key.to_str(),
                        "old_version": v_prev,
                        "new_version": v_curr,
                        "old_dim": format_dim(prev_dim),
                        "new_dim": format_dim(curr_dim),
                        "reason": "VERSION_DRIFT",
                    })

        return drifts


def register(
    registry: AnnotationRegistry,
    endpoint: str,
    field_name: str,
    dim: Dim,
    unit: str,
    def_id: str,
    version: int = 1,
    angle_mode: str = "rad",
) -> None:
    """便捷注册函数."""
    key = FieldKey(endpoint, field_name)
    ann = Annotation(dim=dim, unit=unit, def_id=def_id, version=version, angle_mode=angle_mode)
    registry.register(key, ann)
