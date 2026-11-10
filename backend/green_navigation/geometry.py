"""几何先验门：Cb / L / B / T / Δ / DWT 自洽性校验。

为什么需要这道门
----------------
阻力-推进-能效链的**所有**下游量都按 ``V^(2~3)`` 放大几何误差：Cb 或吃水错
1%，阻力错 ~2%，功率错 ~3%，EEDI 错 ~3%。工程上出现过的真实事故是「Cb·L·B·T
算出的排水量与设计排水量差 15%」（型线表与主尺度表不同版本），此时算出来的
EEDI 是"精致地错"。因此本模块把几何自洽当**准入条件**：不合格直接拒算
（``strict=True``），而不是在下游产生一个看起来正常的数字。

判据（全部可机器复算）
----------------------
1. Cb ∈ [0.30, 0.90]：Holtrop 回归适用域 + 物理范围。
2. L/B ∈ [4.0, 12.0]、B/T ∈ [2.0, 6.5]：常规单体排水型船带。
3. Δ_calc = Cb·L·B·T·ρ_sea 与输入排水量的相对偏差 < ``tol``（缺省 5%）。
4. DWT/Δ ∈ [0.60, 0.95]：载重吨占满载排水量的常规份额。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RHO_SEA_T_PER_M3 = 1.025  # 海水密度 [t/m³]

CB_RANGE = (0.30, 0.90)
LB_RANGE = (4.0, 12.0)
BT_RANGE = (2.0, 6.5)
DWT_FRACTION_RANGE = (0.60, 0.95)


@dataclass
class GeometryCheck:
    """几何自洽性检查结果（可 JSON 序列化）。"""

    ok: bool
    cb: float
    length_m: float
    beam_m: float
    draft_m: float
    displacement_calc_t: float
    checks: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "cb": self.cb,
            "L_m": self.length_m,
            "B_m": self.beam_m,
            "T_m": self.draft_m,
            "displacement_calc_t": self.displacement_calc_t,
            "checks": self.checks,
            "warnings": self.warnings,
        }


class GeometryError(ValueError):
    """几何先验不自洽（下游一律不得继续算）。"""


def validate_geometry(
    length_m: float,
    beam_m: float,
    draft_m: float,
    cb: float,
    displacement_t: Optional[float] = None,
    dwt_t: Optional[float] = None,
    tol: float = 0.05,
    strict: bool = True,
) -> GeometryCheck:
    """校验主尺度自洽性；``strict=True`` 时不通过即抛 :class:`GeometryError`。"""
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def _record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    if min(length_m, beam_m, draft_m) <= 0:
        raise GeometryError("L/B/T 必须为正")
    if not CB_RANGE[0] <= cb <= CB_RANGE[1]:
        raise GeometryError(f"Cb={cb} 超出物理/回归适用域 {CB_RANGE}")

    lb = length_m / beam_m
    bt = beam_m / draft_m
    _record("L/B", LB_RANGE[0] <= lb <= LB_RANGE[1], f"L/B={lb:.3f} ∈ {LB_RANGE}")
    _record("B/T", BT_RANGE[0] <= bt <= BT_RANGE[1], f"B/T={bt:.3f} ∈ {BT_RANGE}")

    displacement_calc_t = cb * length_m * beam_m * draft_m * RHO_SEA_T_PER_M3
    if displacement_t is not None:
        rel = abs(displacement_t - displacement_calc_t) / displacement_calc_t
        _record(
            "displacement",
            rel <= tol,
            f"Δ_in={displacement_t:.0f} t vs Cb·L·B·T·ρ={displacement_calc_t:.0f} t, rel={rel*100:.2f}% (tol {tol*100:.0f}%)",
        )
        if rel > tol:
            warnings.append(
                f"排水量偏差 {rel*100:.2f}% > {tol*100:.0f}%：Cb/L/B/T 与排水量不同版本，须先核对型线表"
            )
    if dwt_t is not None and dwt_t > 0:
        frac = dwt_t / displacement_calc_t
        _record(
            "DWT/displacement",
            DWT_FRACTION_RANGE[0] <= frac <= DWT_FRACTION_RANGE[1],
            f"DWT/Δ={frac:.3f} ∈ {DWT_FRACTION_RANGE}",
        )

    ok = all(c["passed"] for c in checks)
    if not ok and strict:
        failed = [c["detail"] for c in checks if not c["passed"]]
        raise GeometryError("几何先验不自洽: " + "; ".join(failed))
    return GeometryCheck(
        ok=ok,
        cb=cb,
        length_m=length_m,
        beam_m=beam_m,
        draft_m=draft_m,
        displacement_calc_t=displacement_calc_t,
        checks=checks,
        warnings=warnings,
    )