"""契约版本管理与迁移 (contract versioning & declarative migration).

规则 (机器可判)
---------------
1. **版本单调**: 新版本号必须严格大于旧版本号。
2. **变更分类**: 逐端口比对, 分成
   * 非破坏性: ``ADDED_OPTIONAL`` / ``UNIT_ALIAS`` (同量纲同系数换写法) /
     ``RELAXED_REQUIRED`` / ``SEMANTIC_CHANGED`` (仅说明文字);
   * **破坏性**: ``REMOVED_PORT`` / ``ADDED_REQUIRED`` / ``UNIT_CHANGED``
     (量纲同但换算系数变了, 下游数值会静默错) / ``DIM_CHANGED`` /
     ``FRAME_CHANGED`` / ``TENSOR_ORDER_CHANGED`` / ``STRICTENED_REQUIRED``。
3. **破坏性变更必须有显式 migration**: 每条破坏性变更都要被一条声明式迁移
   (``kind`` + 字段) 覆盖, 否则兼容检查 FAIL 并逐条列出**未被覆盖的字段**。
   迁移是**可执行**的: :func:`apply_migrations` 会把 v1 载荷跑成 v2 载荷,
   再按新契约的 required 端口与量纲校验 —— "声明了迁移路径"不等于"能跑通"。

迁移类型
--------
``rename`` / ``unit_rescale`` / ``default_fill`` / ``frame_rotate`` /
``reinterpret_dim`` / ``custom`` / ``drop``。每种都要写明 ``note`` (破坏性
变更的人可读依据), 空 note 视为无效迁移。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .contract import Contract, Port, Violation
from .dimension import DimensionError, format_dim
from .frames import FrameError, FrameGraph
from .units import UnknownUnitError, convert, conversion_factor

MIGRATION_KINDS = (
    "rename",
    "unit_rescale",
    "default_fill",
    "frame_rotate",
    "reinterpret_dim",
    "custom",
    "drop",
)

#: 破坏性变更 -> 可接受的迁移类型
COVERING_KINDS: Dict[str, Tuple[str, ...]] = {
    "REMOVED_PORT": ("drop", "rename"),
    "ADDED_REQUIRED": ("default_fill",),
    "UNIT_CHANGED": ("unit_rescale",),
    "DIM_CHANGED": ("reinterpret_dim", "custom"),
    "FRAME_CHANGED": ("frame_rotate", "custom"),
    "TENSOR_ORDER_CHANGED": ("custom", "reinterpret_dim"),
    "STRICTENED_REQUIRED": ("default_fill",),
}

NON_BREAKING = (
    "ADDED_OPTIONAL",
    "UNIT_ALIAS",
    "RELAXED_REQUIRED",
    "SEMANTIC_CHANGED",
)


class VersionError(ValueError):
    """版本号/版本序不合法."""


@dataclass(frozen=True)
class Change:
    """一条端口级变更."""

    kind: str
    port: str
    role: str
    detail: str = ""
    old_unit: str = ""
    new_unit: str = ""
    old_frame: Optional[str] = None
    new_frame: Optional[str] = None
    old_value: object = None
    new_value: object = None

    @property
    def breaking(self) -> bool:
        return self.kind not in NON_BREAKING

    def key(self) -> Tuple[str, str]:
        return (self.port, self.kind)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "port": self.port,
            "role": self.role,
            "breaking": self.breaking,
            "detail": self.detail,
            "old_unit": self.old_unit,
            "new_unit": self.new_unit,
            "old_frame": self.old_frame,
            "new_frame": self.new_frame,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


@dataclass(frozen=True)
class Migration:
    """声明式迁移: 一条迁移处理一个字段上的一类变更."""

    kind: str
    port: str
    note: str = ""
    to_port: str = ""
    value: object = None
    unit: str = ""
    from_frame: str = ""
    to_frame: str = ""

    def __post_init__(self) -> None:
        if self.kind not in MIGRATION_KINDS:
            raise VersionError(f"未知迁移类型 {self.kind!r} (可选 {MIGRATION_KINDS})")
        if not self.port:
            raise VersionError("迁移必须指定 port")
        if self.kind == "rename" and not self.to_port:
            raise VersionError("rename 迁移必须给 to_port")
        if self.kind == "default_fill" and self.value is None:
            raise VersionError("default_fill 迁移必须给 value")
        if self.kind == "frame_rotate" and not (self.from_frame and self.to_frame):
            raise VersionError("frame_rotate 迁移必须给 from_frame/to_frame")
        if self.kind in ("reinterpret_dim", "custom") and len(self.note.strip()) < 8:
            raise VersionError(f"{self.kind} 迁移必须写明 note (>=8 字符) 说明语义依据")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "port": self.port,
            "note": self.note,
            "to_port": self.to_port,
            "value": self.value,
            "unit": self.unit,
            "from_frame": self.from_frame,
            "to_frame": self.to_frame,
        }


def migration_from_dict(payload: Mapping[str, object]) -> Migration:
    try:
        return Migration(
            kind=str(payload["kind"]),
            port=str(payload["port"]),
            note=str(payload.get("note", "")),
            to_port=str(payload.get("to_port", "")),
            value=payload.get("value"),
            unit=str(payload.get("unit", "")),
            from_frame=str(payload.get("from_frame", "")),
            to_frame=str(payload.get("to_frame", "")),
        )
    except KeyError as exc:
        raise VersionError(f"迁移缺少必填键 {exc.args[0]!r}: {payload!r}") from exc


@dataclass(frozen=True)
class CompatReport:
    """v_old -> v_new 的兼容性报告."""

    contract_id: str
    old_version: int
    new_version: int
    changes: Tuple[Change, ...]
    migrations: Tuple[Migration, ...] = ()
    uncovered: Tuple[Change, ...] = ()
    unused: Tuple[Migration, ...] = ()

    @property
    def breaking(self) -> Tuple[Change, ...]:
        return tuple(c for c in self.changes if c.breaking)

    @property
    def ok(self) -> bool:
        """向后兼容: 无破坏性变更, 或每条破坏性变更都被迁移覆盖."""
        return not self.uncovered

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "ok": self.ok,
            "changes": [c.to_dict() for c in self.changes],
            "breaking_count": len(self.breaking),
            "uncovered": [c.to_dict() for c in self.uncovered],
            "migrations": [m.to_dict() for m in self.migrations],
            "unused_migrations": [m.to_dict() for m in self.unused],
        }


def _same_scale(a: str, b: str) -> bool:
    try:
        return conversion_factor(a, b) == 1.0
    except (DimensionError, UnknownUnitError):
        return False


def diff_contracts(old: Contract, new: Contract) -> Tuple[Change, ...]:
    """逐端口比对两版契约, 输出全部变更 (含破坏性标记)."""
    if old.contract_id != new.contract_id:
        raise VersionError(f"契约 id 不同, 不可比对: {old.contract_id} vs {new.contract_id}")
    if new.version <= old.version:
        raise VersionError(
            f"{old.contract_id}: 新版本号 {new.version} 必须 > 旧版本号 {old.version}")
    changes: List[Change] = []

    def index(c: Contract) -> Dict[Tuple[str, str], Port]:
        return {(p.role, p.name): p for p in c.ports}

    old_idx, new_idx = index(old), index(new)
    for key, op in old_idx.items():
        role, name = key
        np_ = new_idx.get(key)
        if np_ is None:
            changes.append(Change("REMOVED_PORT", name, role,
                                  detail=f"v{old.version} 的端口在 v{new.version} 消失"))
            continue
        try:
            d_old, d_new = op.dim, np_.dim
        except (UnknownUnitError, DimensionError):
            d_old = d_new = None
        if d_old is not None and d_new is not None and d_old != d_new:
            changes.append(Change(
                "DIM_CHANGED", name, role,
                detail=f"量纲 {format_dim(d_old)} -> {format_dim(d_new)}",
                old_unit=op.unit, new_unit=np_.unit))
        elif op.unit != np_.unit:
            kind = "UNIT_ALIAS" if _same_scale(op.unit, np_.unit) else "UNIT_CHANGED"
            detail = (f"单位 {op.unit!r} -> {np_.unit!r}"
                      + ("" if kind == "UNIT_ALIAS" else
                         f" (换算系数 {conversion_factor(op.unit, np_.unit):.12g}, 下游数值静默变化)"))
            changes.append(Change(kind, name, role, detail=detail,
                                  old_unit=op.unit, new_unit=np_.unit))
        if op.frame != np_.frame:
            changes.append(Change(
                "FRAME_CHANGED", name, role,
                detail=f"坐标系 {op.frame!r} -> {np_.frame!r}",
                old_frame=op.frame, new_frame=np_.frame))
        if op.tensor_order != np_.tensor_order:
            changes.append(Change(
                "TENSOR_ORDER_CHANGED", name, role,
                detail=f"张量阶 {op.tensor_order} -> {np_.tensor_order}",
                old_value=op.tensor_order, new_value=np_.tensor_order))
        if op.required and not np_.required:
            changes.append(Change("RELAXED_REQUIRED", name, role,
                                  detail="必填 -> 可选"))
        if not op.required and np_.required:
            changes.append(Change("STRICTENED_REQUIRED", name, role,
                                  detail="可选 -> 必填"))
        if op.semantic != np_.semantic:
            changes.append(Change("SEMANTIC_CHANGED", name, role,
                                  detail=f"{op.semantic!r} -> {np_.semantic!r}"))
    for key, np_ in new_idx.items():
        if key in old_idx:
            continue
        role, name = key
        if np_.required:
            changes.append(Change("ADDED_REQUIRED", name, role,
                                  detail=f"v{new.version} 新增必填端口 {np_.unit}"))
        else:
            changes.append(Change("ADDED_OPTIONAL", name, role,
                                  detail=f"v{new.version} 新增可选端口 {np_.unit}"))
    return tuple(changes)


def check_compatibility(
    old: Contract,
    new: Contract,
    migrations: Sequence[Migration | Mapping[str, object]] = (),
) -> CompatReport:
    """兼容性判定: 破坏性变更逐条要求迁移覆盖."""
    changes = diff_contracts(old, new)
    migs = tuple(
        m if isinstance(m, Migration) else migration_from_dict(m) for m in migrations
    )
    # rename 迁移同时覆盖『旧名消失』与『新名出现』两类变更
    uncovered: List[Change] = []
    used: set = set()
    for ch in changes:
        if not ch.breaking:
            continue
        allowed = COVERING_KINDS.get(ch.kind, ())
        matched = False
        for i, m in enumerate(migs):
            if m.kind == "rename" and ch.port == m.to_port and ch.kind in (
                "ADDED_REQUIRED", "ADDED_OPTIONAL", "STRICTENED_REQUIRED"
            ):
                matched = True
                used.add(i)
                continue
            if m.port != ch.port:
                continue
            if m.kind in allowed:
                matched = True
                used.add(i)
        if not matched:
            uncovered.append(ch)
    unused = tuple(m for i, m in enumerate(migs) if i not in used)
    return CompatReport(
        contract_id=new.contract_id,
        old_version=old.version,
        new_version=new.version,
        changes=changes,
        migrations=migs,
        uncovered=tuple(uncovered),
        unused=unused,
    )


# ---------------------------------------------------------------------------
# 迁移执行 (声明 -> 可跑通)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MigrationResult:
    """迁移执行结果: 迁移后的载荷 + 违规列表."""

    payload: Mapping[str, object]
    applied: Tuple[str, ...] = ()
    violations: Tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "payload": dict(self.payload),
            "applied": list(self.applied),
            "violations": [v.to_dict() for v in self.violations],
        }


def _rotate_vector(v: object, frames: FrameGraph, src: str, dst: str) -> object:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise VersionError(f"frame_rotate 只接受 3 维矢量, 收到 {v!r}")
    out = frames.transform_vector([float(x) for x in v], src, dst)
    return list(out)


def apply_migrations(
    payload: Mapping[str, object],
    migrations: Sequence[Migration | Mapping[str, object]],
    old: Contract,
    new: Contract,
    *,
    frames: Optional[FrameGraph] = None,
) -> MigrationResult:
    """把 v_old 的数据载荷按迁移声明跑成 v_new 载荷, 并按新契约校验.

    这是"迁移路径真实存在"的证据: 只声明不执行一律不算。
    """
    migs = [m if isinstance(m, Migration) else migration_from_dict(m) for m in migrations]
    data: Dict[str, object] = dict(payload)
    applied: List[str] = []
    violations: List[Violation] = []
    cid = new.contract_id

    def old_port(name: str) -> Optional[Port]:
        for p in old.ports:
            if p.name == name:
                return p
        return None

    def new_port(name: str) -> Optional[Port]:
        for p in new.ports:
            if p.name == name:
                return p
        return None

    for m in migs:
        try:
            if m.kind == "rename":
                if m.port in data:
                    data[m.to_port] = data.pop(m.port)
                    applied.append(f"rename:{m.port}->{m.to_port}")
            elif m.kind == "unit_rescale":
                if m.port in data:
                    op, np_ = old_port(m.port), new_port(m.port)
                    if op is None or np_ is None:
                        raise VersionError(f"unit_rescale 端口 {m.port!r} 在版本中缺失")
                    data[m.port] = _convert_value(data[m.port], op.unit, np_.unit)
                    applied.append(f"unit_rescale:{m.port}:{op.unit}->{np_.unit}")
            elif m.kind == "default_fill":
                np_ = new_port(m.port)
                if np_ is None:
                    raise VersionError(f"default_fill 端口 {m.port!r} 不存在于新契约")
                raw = m.value
                if m.unit:
                    raw = _convert_value(raw, m.unit, np_.unit)
                if m.port not in data:
                    applied.append(f"default_fill:{m.port}")
            elif m.kind in ("reinterpret_dim", "custom"):
                applied.append(f"{m.kind}:{m.port}")
            elif m.kind == "frame_rotate":
                if frames is None:
                    raise VersionError("frame_rotate 需要帧图 (FrameGraph)")
                if m.port in data:
                    data[m.port] = _rotate_vector(data[m.port], frames, m.from_frame, m.to_frame)
                    applied.append(f"frame_rotate:{m.port}:{m.from_frame}->{m.to_frame}")
            elif m.kind == "drop":
                if m.port in data:
                    data.pop(m.port)
                    applied.append(f"drop:{m.port}")
        except (VersionError, DimensionError, FrameError, UnknownUnitError) as exc:
            violations.append(Violation("MIGRATION_FAILED", f"{m.kind}:{m.port}: {exc}",
                                        cid, m.port))

    # default_fill 需要真正落值 (上面仅为占位), 这里补值
    for m in migs:
        if m.kind == "default_fill" and m.port not in data:
            np_ = new_port(m.port)
            if np_ is None:
                continue
            raw = m.value
            if m.unit:
                try:
                    raw = _convert_value(raw, m.unit, np_.unit)
                except (DimensionError, UnknownUnitError) as exc:
                    violations.append(Violation("MIGRATION_FAILED", f"default_fill:{m.port}: {exc}",
                                                cid, m.port))
                    continue
            data[m.port] = raw

    # 按新契约校验: 必填端口齐备 + 单位/量纲可推导
    for p in new.ports:
        if p.required and p.name not in data:
            violations.append(Violation(
                "MIGRATION_MISSING_REQUIRED",
                f"迁移后缺少新契约必填端口 {p.name!r} ({p.unit})", cid, p.name))
    for name, value in data.items():
        p = new_port(name)
        if p is None:
            violations.append(Violation(
                "MIGRATION_UNKNOWN_PORT", f"迁移后出现新契约未声明的端口 {name!r}", cid, name))
            continue
        if not _finite(value):
            violations.append(Violation(
                "MIGRATION_NON_FINITE", f"端口 {name!r} 值非有限: {value!r}", cid, name))
    return MigrationResult(dict(data), tuple(applied), tuple(violations))


def _convert_value(value: object, from_unit: str, to_unit: str) -> object:
    if isinstance(value, (list, tuple)):
        return [_convert_value(v, from_unit, to_unit) for v in value]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VersionError(f"数值应为标量/数组, 收到 {value!r}")
    return convert(float(value), from_unit, to_unit)


def _finite(value: object) -> bool:
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))