"""主机油耗图（SFOC map）：``SFOC = SFOC_min + a (P/P_MCR - p*)² + b (n/n_MCR - n*)²``。

为什么用这个形式
----------------
大型低速二冲程机的 SFOC 对负荷呈**凸抛物线**（最优点在 75–85% MCR：低负荷
涡轮增压器效率下降、高负荷燃烧持续期拉长），对转速的偏离（定距桨船在部分
负荷时 n ∝ P^(1/3)，转速与负荷强相关）叠加二阶惩罚。因此任务的 4 参数形式
是合理的经验模型。本模块**不假设**系数，全部由最小二乘从
``data/sfoc_reference.json`` 的厂家部分负荷表标定。

可辨识性（重要）
----------------
在定距桨定律 ``n/n_MCR = (P/P_MCR)^(1/3)`` 下，``(P-p*)²`` 与 ``(n-n*)²`` 几乎
共线：实测设计矩阵条件数 ≫ 1e8，``b`` 无法从一维部分负荷曲线上辨识出来。
本模块因此**默认 ``b ≡ 0``**，并把条件数与"b 未辨识"写进 :class:`SfocFit`；
若调用方提供二维标定点（同一功率下多个转速），可用 ``identify_n_term=True``
同时辨识 b，此时才声明 b 为 REAL。这一条是为了避免"4 参数拟合 1 维曲线然后
宣称 4 个参数都被标定"这类假能力。

证据等级
--------
* ``a`` / ``sfoc_min`` 相对转录参考表的残差：REAL（可逐点复算，判据 <5%）。
* 参考表自身绝对精度：WITHHELD（厂家容差 ±5%，离线未回读原文）。
* ``b`` 项：默认 WITHHELD（不可辨识），非 0 时要求二维标定点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class SfocMapError(ValueError):
    """SFOC 图不可用（负荷/转速越界或标定不足）。"""


@dataclass(frozen=True)
class SfocMap:
    """标定后的 SFOC 图。"""

    sfoc_min_g_kwh: float
    a: float
    b: float = 0.0
    p_opt: float = 0.8
    n_opt: float = 0.9
    n_term_identified: bool = False
    fit: Dict[str, Any] = field(default_factory=dict)

    def sfoc(self, p_over_mcr: float, n_over_mcr: Optional[float] = None) -> float:
        """给定负荷（及转速比，缺省按定距桨定律 n ∝ P^(1/3)）返回 SFOC [g/kWh]。"""
        if p_over_mcr <= 0.0:
            raise SfocMapError(f"负荷必须为正，得到 {p_over_mcr}")
        n = propeller_law_speed_ratio(p_over_mcr) if n_over_mcr is None else n_over_mcr
        if n <= 0.0:
            raise SfocMapError(f"转速比必须为正，得到 {n}")
        return (self.sfoc_min_g_kwh
                + self.a * (p_over_mcr - self.p_opt) ** 2
                + self.b * (n - self.n_opt) ** 2)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sfoc_min_g_kwh": self.sfoc_min_g_kwh,
            "a": self.a,
            "b": self.b,
            "p_opt": self.p_opt,
            "n_opt": self.n_opt,
            "n_term_identified": self.n_term_identified,
            "fit": self.fit,
            "evidence": {
                "a_and_min": "REAL (residual vs transcribed part-load table)",
                "b_term": "REAL" if self.n_term_identified else "WITHHELD (unidentifiable under propeller law)",
                "reference_table": "WITHHELD_PROVENANCE (maker tolerance ±5%)",
            },
        }


def propeller_law_speed_ratio(p_over_mcr: float) -> float:
    """定距桨定律：``P ∝ n³`` ⇒ ``n/n_MCR = (P/P_MCR)^(1/3)``。"""
    if p_over_mcr <= 0.0:
        raise SfocMapError("负荷必须为正")
    return p_over_mcr ** (1.0 / 3.0)


# ---------------------------------------------------------------------------
# 小型线性代数（避免引入 numpy 依赖；仅 2x2 / 3x3 正规方程）
# ---------------------------------------------------------------------------

def _solve(matrix: Sequence[Sequence[float]], rhs: Sequence[float]) -> Tuple[List[float], float]:
    """Gauss 消元解 ``A x = b``，返回 (x, 1-范数条件数估计)。"""
    n = len(rhs)
    a = [list(map(float, row)) + [float(rhs[i])] for i, row in enumerate(matrix)]
    # 条件数估计：行均衡后的最大/最小主元
    pivots: List[float] = []
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-300:
            raise SfocMapError("标定矩阵奇异：给定数据无法辨识所请求的参数")
        a[col], a[pivot] = a[pivot], a[col]
        pivots.append(abs(a[col][col]))
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))
        x[r] = acc / a[r][r]
    cond = max(pivots) / min(pivots) if min(pivots) > 0 else float("inf")
    return x, cond


def _lstsq(rows: Sequence[Sequence[float]], y: Sequence[float]) -> Tuple[List[float], float]:
    """正规方程最小二乘（``rows`` 每行是设计矩阵一行）。"""
    k = len(rows[0])
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    aty = [sum(r[i] * yi for r, yi in zip(rows, y)) for i in range(k)]
    return _solve(ata, aty)


# ---------------------------------------------------------------------------
# 标定
# ---------------------------------------------------------------------------

def fit_sfoc_map(
    points: Iterable[Dict[str, Any]],
    p_opt: float = 0.8,
    n_opt: float = 0.9,
    identify_n_term: bool = False,
) -> SfocMap:
    """从 ``[{load, sfoc_g_kwh, (n_over_mcr)}]`` 标定 SFOC 抛物面。

    ``identify_n_term=True`` 时必须给出 ``n_over_mcr`` 且至少 3 个不同负荷 ×
    不同转速组合，否则共线导致矩阵奇异（显式报错，不给假参数）。
    """
    pts = list(points)
    if len(pts) < 3:
        raise SfocMapError("标定点不足（需要 ≥3 个负荷点）")
    loads = [float(p["load"]) for p in pts]
    sfocs = [float(p["sfoc_g_kwh"]) for p in pts]
    for lo in loads:
        if not 0.0 < lo <= 1.2:
            raise SfocMapError(f"负荷 {lo} 超出 (0, 1.2]")

    def n_of(p: Dict[str, Any]) -> float:
        return float(p.get("n_over_mcr", propeller_law_speed_ratio(float(p["load"]))))

    rows: List[List[float]] = []
    for p in pts:
        load = float(p["load"])
        n = n_of(p)
        row = [1.0, (load - p_opt) ** 2]
        if identify_n_term:
            row.append((n - n_opt) ** 2)
        rows.append(row)
    coeffs, cond = _lstsq(rows, sfocs)
    model = SfocMap(
        sfoc_min_g_kwh=coeffs[0],
        a=coeffs[1],
        b=coeffs[2] if identify_n_term else 0.0,
        p_opt=p_opt,
        n_opt=n_opt,
        n_term_identified=identify_n_term,
    )
    # 可辨识性探针：若不约束，把 b 一起拟合时正规方程的条件数是多少（用于如实声明共线）
    cond_if_b = None
    if not identify_n_term:
        rows3 = [[1.0, (float(p["load"]) - p_opt) ** 2, (n_of(p) - n_opt) ** 2] for p in pts]
        try:
            _, cond_if_b = _lstsq(rows3, sfocs)
        except SfocMapError:
            cond_if_b = float("inf")
    resid = [model.sfoc(float(p["load"]), n_of(p)) - float(p["sfoc_g_kwh"]) for p in pts]
    max_abs = max(abs(r) for r in resid)
    max_rel = max(abs(r) / float(p["sfoc_g_kwh"]) * 100.0 for r, p in zip(resid, pts))
    return SfocMap(
        sfoc_min_g_kwh=model.sfoc_min_g_kwh,
        a=model.a,
        b=model.b,
        p_opt=p_opt,
        n_opt=n_opt,
        n_term_identified=identify_n_term,
        fit={
            "n_points": len(pts),
            "max_abs_residual_g_kwh": max_abs,
            "max_rel_residual_pct": max_rel,
            "normal_equation_condition_number": cond,
            "condition_number_if_b_fitted": cond_if_b,
            "identify_n_term": identify_n_term,
            "collinearity_note": (
                "定距桨定律下 (P-p*)² 与 (n-n*)² 近似共线：若强行三参数拟合，正规方程"
                f"条件数 {cond_if_b:.3e}（病态），b 不可辨识 → 默认置 0"
                if not identify_n_term else "二维标定点提供转速自由度"
            ),
        },
    )


def validate_sfoc_map(model: SfocMap, points: Iterable[Dict[str, Any]],
                      tol_pct: float = 5.0) -> Dict[str, Any]:
    """逐点复核 SFOC 图 vs 参考表（判据：相对误差 < ``tol_pct``%）。"""
    rows = []
    worst = 0.0
    for p in points:
        load = float(p["load"])
        n = float(p.get("n_over_mcr", propeller_law_speed_ratio(load)))
        ref = float(p["sfoc_g_kwh"])
        got = model.sfoc(load, n)
        rel = abs(got - ref) / ref * 100.0
        worst = max(worst, rel)
        rows.append({"load": load, "n_over_mcr": n, "sfoc_ref": ref,
                     "sfoc_model": got, "rel_err_pct": rel})
    in_band = [r for r in rows if 0.75 <= r["load"] <= 0.90]
    return {
        "points": rows,
        "max_rel_err_pct": worst,
        "max_rel_err_pct_075_090": max(r["rel_err_pct"] for r in in_band),
        "within_tol": worst < tol_pct,
        "tol_pct": tol_pct,
    }


# ---------------------------------------------------------------------------
# 燃油/CO₂ 换算
# ---------------------------------------------------------------------------

def fuel_rate_g_per_h(power_kw: float, sfoc_g_kwh: float) -> float:
    """功率 [kW] × SFOC [g/kWh] = 油耗 [g/h]。"""
    if power_kw < 0 or sfoc_g_kwh <= 0:
        raise SfocMapError("功率须非负、SFOC 须为正")
    return power_kw * sfoc_g_kwh


def co2_g_per_h(power_kw: float, sfoc_g_kwh: float, cf: float) -> float:
    """CO₂ 排放强度 [g/h] = 油耗 [g/h] × 碳因子 [gCO₂/gFuel]。"""
    if cf <= 0:
        raise SfocMapError("碳因子必须为正")
    return fuel_rate_g_per_h(power_kw, sfoc_g_kwh) * cf