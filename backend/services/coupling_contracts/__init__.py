"""跨求解器多物理场耦合的接口契约标准化 (coupling interface contract standard).

本包是**域中立**的机制层, 提供四件事:

============================  ==================================================
:mod:`dimension`              量纲代数 (SI 七基本量 + 精确有理数秩/零空间)
:mod:`units`                  单位制登记 (SI/英制/混合/航海), 表达式解析, 可追溯换算
:mod:`frames`                 坐标系/刚体变换, 正交性与 det=+1 校验, 帧图最短路
:mod:`buckingham`             Buckingham π 定理: 无量纲群基 + 声明群一致性校验
:mod:`contract`               契约声明/加载/整包校验 (端口-单位-坐标系-量纲-π)
:mod:`versioning`             版本兼容判定 + 可执行迁移 (v1 -> v2 载荷)
:mod:`quantity`               交换载荷封装 (值/单位/坐标系/量纲) 与归一化
============================  ==================================================

领域数据 (九大学科的端口字段) 不进代码, 由 ``contracts/coupling/v*/`` 的 JSON
注入; 代码里不出现任何学科词条, 因此同一套机制可以被任何求解器复用。

例子
----
>>> from services.coupling_contracts import units, frames, contract
>>> round(units.convert(1.0, "kN", "lbf"), 6)   # 单位归一
224.808943
>>> g = frames.FrameGraph(); g.add_frame(frames.Frame("a")); g.add_frame(frames.Frame("b"))
>>> g.add_relative("b", "a", frames.RigidTransform(frames.rot_z(1.5707963267948966)))
>>> [round(x, 12) for x in g.transform_point((1.0, 0.0, 0.0), "b", "a")]
[6e-17, 1.0, 0.0]
"""
from __future__ import annotations

from .buckingham import (
    ConsistencyReport,
    GroupCheck,
    PiGroup,
    PiResult,
    buckingham_pi,
    check_declared_groups,
    check_homogeneous_relation,
    group_dim,
)
from .contract import (
    Contract,
    Edge,
    Port,
    ValidationReport,
    Violation,
    contract_from_dict,
    contract_versions,
    load_contract,
    load_edges,
    load_registry,
    load_registry_layers,
    validate_contract,
    validate_registry,
)
from .dimension import (
    BASE_SYMBOLS,
    Dim,
    DimensionError,
    ZERO,
    add_check,
    build_matrix,
    dim,
    dim_from_dict,
    dim_to_dict,
    div,
    format_dim,
    is_dimensionless,
    mul,
    parse_dim,
    pow_dim,
)
from .frames import (
    Frame,
    FrameError,
    FrameGraph,
    Mat3,
    ORTHO_TOL,
    RigidTransform,
    det,
    det_error,
    euler_zup,
    from_quaternion,
    load_frames,
    orthonormality_error,
    to_quaternion,
)
from .quantity import Quantity, QuantityError, decode, encode, make, normalize_batch
from .units import (
    ConversionTrace,
    Unit,
    UnitExpr,
    UnknownUnitError,
    convert,
    convert_between_systems,
    convert_traced,
    conversion_factor,
    dim_of_unit,
    from_si,
    parse_unit,
    preferred_unit,
    si_unit_name,
    to_si,
    unit_of,
    units_of_system,
    check_registry_consistency,
    UNITS,
    SYSTEMS,
)
from .versioning import (
    Change,
    CompatReport,
    Migration,
    MigrationResult,
    VersionError,
    apply_migrations,
    check_compatibility,
    diff_contracts,
    migration_from_dict,
)

__all__ = [
    # dimension
    "BASE_SYMBOLS", "Dim", "DimensionError", "ZERO", "add_check", "build_matrix",
    "dim", "dim_from_dict", "dim_to_dict", "div", "format_dim",
    "is_dimensionless", "mul", "parse_dim", "pow_dim",
    # units
    "UNITS", "SYSTEMS", "ConversionTrace", "Unit", "UnitExpr", "UnknownUnitError",
    "convert", "convert_between_systems", "convert_traced", "conversion_factor",
    "dim_of_unit", "from_si", "parse_unit", "preferred_unit", "si_unit_name",
    "to_si", "unit_of", "units_of_system", "check_registry_consistency",
    # frames
    "Frame", "FrameError", "FrameGraph", "Mat3", "ORTHO_TOL", "RigidTransform",
    "det", "det_error", "euler_zup", "from_quaternion", "load_frames",
    "orthonormality_error", "to_quaternion",
    # buckingham
    "ConsistencyReport", "GroupCheck", "PiGroup", "PiResult", "buckingham_pi",
    "check_declared_groups", "check_homogeneous_relation", "group_dim",
    # contract
    "Contract", "Edge", "Port", "ValidationReport", "Violation",
    "contract_from_dict", "contract_versions", "load_contract", "load_edges",
    "load_registry", "load_registry_layers", "validate_contract", "validate_registry",
    # versioning
    "Change", "CompatReport", "Migration", "MigrationResult", "VersionError",
    "apply_migrations", "check_compatibility", "diff_contracts", "migration_from_dict",
    # quantity
    "Quantity", "QuantityError", "decode", "encode", "make", "normalize_batch",
]

__version__ = "1.0.0"