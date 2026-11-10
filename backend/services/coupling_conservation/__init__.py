"""多物理场耦合守恒契约验证框架 (multi-physics coupling conservation contracts).

四件通用原语 (零领域语义: 量名/单位/节点名/系数全部由调用方注入):

``flux``        精确有理数守恒台账: 界面通量平衡, 残差 = |Σ outward| / Σ|v|
``exchange``    场交换契约检查: 量纲 / 守恒量类别 / 张量阶 / 坐标系 / 符号 / 单位
``clock``       时钟语义裁决: explicit / implicit / substep 三协议的因果性、
                对齐性、迭代单调性与数据竞争
``composition`` 多对耦合组合: 链式转接与"链式残差 == 直达残差"传递性裁决
``report``      机器可读验收报告 (判据 + 数字 + 闸门)

与 :mod:`services.coupling_contracts` 的分工: 后者是**声明层** (单位/量纲/坐标系/
契约版本), 本包是**验证层** (运行期守恒/时钟/组合)。两者共用同一份量纲底座
(:mod:`services.coupling_contracts.dimension`), 因此不存在两套量纲表漂移。

用法::

    from services.coupling_conservation import (
        FluxSample, check_balance, dim, Fraction, EXPLICIT, adjudicate,
        ClockEvent, TransferLaw, CouplingChain, check_transitivity,
    )
"""
from __future__ import annotations

from fractions import Fraction

from services.coupling_contracts.dimension import Dim, DimensionError, dim, format_dim

from .clock import (
    EXPLICIT,
    IMPLICIT,
    PROTOCOLS,
    SUBSTEP,
    ClockAdjudication,
    ClockEvent,
    ClockViolation,
    adjudicate,
    substep_schedule,
)
from .composition import (
    CouplingChain,
    TransitivityResult,
    TransferLaw,
    additivity_report,
    check_transitivity,
)
from .exchange import (
    CODES,
    INWARD,
    OUTWARD,
    ExchangeCheck,
    ExchangePort,
    ExchangeViolation,
    check_exchange,
    check_exchange_set,
    describe_dim_delta,
    dim_delta,
    port_from_dict,
)
from .flux import (
    DEFAULT_TOLERANCE,
    FluxLedger,
    FluxSample,
    InterfaceBalance,
    abs_error,
    as_fraction,
    check_balance,
    rel_residual,
    split_conserved,
)
from .report import (
    SCHEMA_ID,
    AcceptanceReport,
    Criterion,
    acceptance_gate,
    load_report,
)

__all__ = [
    # dimension 直出 (单一事实来源)
    "Dim", "DimensionError", "dim", "format_dim", "Fraction",
    # flux
    "FluxSample", "FluxLedger", "InterfaceBalance", "check_balance", "split_conserved",
    "rel_residual", "abs_error", "as_fraction", "DEFAULT_TOLERANCE",
    # exchange
    "ExchangePort", "ExchangeCheck", "ExchangeViolation", "check_exchange",
    "check_exchange_set", "port_from_dict", "dim_delta", "describe_dim_delta",
    "CODES", "OUTWARD", "INWARD",
    # clock
    "ClockEvent", "ClockViolation", "ClockAdjudication", "adjudicate",
    "substep_schedule", "EXPLICIT", "IMPLICIT", "SUBSTEP", "PROTOCOLS",
    # composition
    "TransferLaw", "CouplingChain", "TransitivityResult", "check_transitivity",
    "additivity_report",
    # report
    "Criterion", "AcceptanceReport", "acceptance_gate", "load_report", "SCHEMA_ID",
]