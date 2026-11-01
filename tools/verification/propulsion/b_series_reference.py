"""Wageningen B-series 敞水性能 —— 独立参考实现（canonical, 用于验证对账）

目的
----
本模块是**验证席的参考侧**（reference side），不服务线上请求。它把
Oosterveld & van Oossanen (1975) 的 B 系列多项式回归系数固化为不可变数据，
并提供敞水性能函数，用来裁决平台各处“Wageningen B 系列”实现是否真的在算
B 系列。

参考来源（provenance）
----------------------
Oosterveld, M.W.C. & van Oossanen, P. (1975).
"Further Computer-Analyzed Data of the Wageningen B-Screw Series."
International Shipbuilding Progress, Vol. 22, No. 251, pp. 251-268.

系数表与平台官方数据文件（逐项一致，本文件内嵌为纯数据，避免跨仓依赖）：
    backend/data/official/Wageningen_Bseries_coeffs.dat  (46 行 × 10 列)
列格式：

    CQ  s t u v   CT  s t u v

多项式形式：

    K_T = Σ C_T · J^s · (P/D)^t · (Ae/Ao)^u · Z^v
    K_Q = Σ C_Q · J^s · (P/D)^t · (Ae/Ao)^u · Z^v
    η_0 = J/(2π) · K_T/K_Q

适用域（回归式的原始拟合域，超出即为外插，须标注）：
    Z ∈ [2, 7]，P/D ∈ [0.5, 1.4]，Ae/Ao ∈ [0.3, 1.05]，J ∈ [0, 1.5]

物理不变量（本模块同时提供，供验证器使用）
------------------------------------------
* 动量理论（Rankine–Froude 作动盘）效率上限：
      C_T = T / (½ ρ A_0 V_A²) = 8 K_T / (π J²)
      η_ideal = 2 / (1 + sqrt(1 + C_T))
  这是定理级上界：任何真实螺旋桨都必须满足 η_0 < η_ideal。它是**不依赖系数表**的
  独立判据，因此可用来裁决系数表是否被伪造。
"""

from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Tuple

__all__ = [
    "CT_TERMS",
    "CQ_TERMS",
    "kt_kq",
    "open_water",
    "eta_ideal",
    "ct_from_kt",
    "zero_thrust_J",
    "provenance",
]

# ---------------------------------------------------------------------------
# 1. 系数表（不可变数据；勿改，改动即失去参考意义）
#    每项 (C, s, t, u, v)
# ---------------------------------------------------------------------------
CT_TERMS: Tuple[Tuple[float, int, int, int, int], ...] = (
    (0.00880496, 0, 0, 0, 0),
    (-0.204554, 1, 0, 0, 0),
    (0.166351, 0, 1, 0, 0),
    (0.158114, 0, 2, 0, 0),
    (-0.147581, 2, 0, 1, 0),
    (-0.481497, 1, 1, 1, 0),
    (0.415437, 0, 2, 1, 0),
    (0.0144043, 0, 0, 0, 1),
    (-0.0530054, 2, 0, 0, 1),
    (0.0143481, 0, 1, 0, 1),
    (0.0606826, 1, 1, 0, 1),
    (-0.0125894, 0, 0, 1, 1),
    (0.0109689, 1, 0, 1, 1),
    (-0.133698, 0, 3, 0, 0),
    (0.00638407, 0, 6, 0, 0),
    (-0.00132718, 2, 6, 0, 0),
    (0.168496, 3, 0, 1, 0),
    (-0.0507214, 0, 0, 2, 0),
    (0.0854559, 2, 0, 2, 0),
    (-0.0504475, 3, 0, 2, 0),
    (0.010465, 1, 6, 2, 0),
    (-0.00648272, 2, 6, 2, 0),
    (-0.00841728, 0, 3, 0, 1),
    (0.0168424, 1, 3, 0, 1),
    (-0.00102296, 3, 3, 0, 1),
    (-0.0317791, 0, 3, 1, 1),
    (0.018604, 1, 0, 2, 1),
    (-0.00410798, 0, 2, 2, 1),
    (-0.000606848, 0, 0, 0, 2),
    (-0.0049819, 1, 0, 0, 2),
    (0.0025983, 2, 0, 0, 2),
    (-0.000560528, 3, 0, 0, 2),
    (-0.00163652, 1, 2, 0, 2),
    (-0.000328787, 1, 6, 0, 2),
    (0.000116502, 2, 6, 0, 2),
    (0.000690904, 0, 0, 1, 2),
    (0.00421749, 0, 3, 1, 2),
    (0.0000565229, 3, 6, 1, 2),
    (-0.00146564, 0, 3, 2, 2),
)

CQ_TERMS: Tuple[Tuple[float, int, int, int, int], ...] = (
    (0.00379368, 0, 0, 0, 0),
    (0.00886523, 2, 0, 0, 0),
    (-0.032241, 1, 1, 0, 0),
    (0.00344778, 0, 2, 0, 0),
    (-0.0408811, 0, 1, 1, 0),
    (-0.108009, 1, 1, 1, 0),
    (-0.0885381, 2, 1, 1, 0),
    (0.188561, 0, 2, 1, 0),
    (-0.00370871, 1, 0, 0, 1),
    (0.00513696, 0, 1, 0, 1),
    (0.0209449, 1, 1, 0, 1),
    (0.00474319, 2, 1, 0, 1),
    (-0.00723408, 2, 0, 1, 1),
    (0.00438388, 1, 1, 1, 1),
    (-0.0269403, 0, 2, 1, 1),
    (0.0558082, 3, 0, 1, 0),
    (0.0161886, 0, 3, 1, 0),
    (0.00318086, 1, 3, 1, 0),
    (0.015896, 0, 0, 2, 0),
    (0.0471729, 1, 0, 2, 0),
    (0.0196283, 3, 0, 2, 0),
    (-0.0502782, 0, 1, 2, 0),
    (-0.030055, 3, 1, 2, 0),
    (0.0417122, 2, 2, 2, 0),
    (-0.0397722, 0, 3, 2, 0),
    (-0.00350024, 0, 6, 2, 0),
    (-0.0106854, 3, 0, 0, 1),
    (0.00110903, 3, 3, 0, 1),
    (-0.000313912, 0, 6, 0, 1),
    (0.0035985, 3, 0, 1, 1),
    (-0.00142121, 0, 6, 1, 1),
    (-0.00383637, 1, 0, 2, 1),
    (0.0126803, 0, 2, 2, 1),
    (-0.00318278, 2, 3, 2, 1),
    (0.00334268, 0, 6, 2, 1),
    (-0.00183491, 1, 1, 0, 2),
    (0.000112451, 3, 2, 0, 2),
    (-0.0000297228, 3, 6, 0, 2),
    (0.000269551, 1, 0, 1, 2),
    (0.00083265, 2, 0, 1, 2),
    (0.00155334, 0, 2, 1, 2),
    (0.000302683, 0, 6, 1, 2),
    (-0.0001843, 0, 0, 2, 2),
    (-0.000425399, 0, 3, 2, 2),
    (0.0000869243, 3, 3, 2, 2),
    (-0.0004689, 0, 6, 2, 2),
    (0.0000554194, 1, 6, 2, 2),
)

PROVENANCE = {
    "reference": "Oosterveld & van Oossanen (1975), Int. Shipbuilding Progress 22(251), 251-268",
    "coefficient_file": "backend/data/official/Wageningen_Bseries_coeffs.dat",
    "n_ct_terms": len(CT_TERMS),
    "n_cq_terms": len(CQ_TERMS),
    "validity": {"Z": [2, 7], "P_D": [0.5, 1.4], "Ae_Ao": [0.3, 1.05], "J": [0.0, 1.5]},
}


def provenance() -> dict:
    """返回参考实现的来源与适用域元数据。"""
    return dict(PROVENANCE)


def _eval(terms, J: float, P_D: float, Ae_Ao: float, Z: float) -> float:
    total = 0.0
    for coef, s, t, u, v in terms:
        total += coef * (J ** s) * (P_D ** t) * (Ae_Ao ** u) * (Z ** v)
    return total


class OpenWaterPoint(NamedTuple):
    J: float
    Kt: float
    Kq: float
    eta0: Optional[float]          # K_T <= 0（越过零推力点）时无意义，置 None
    eta_ideal: Optional[float]     # 动量理论上界（同一 J 与 K_T 下）


def kt_kq(J: float, P_D: float, Ae_Ao: float, Z: float) -> Tuple[float, float]:
    """B 系列敞水推力/转矩系数。J = V_A/(nD)，P_D 为螺距比，Ae_Ao 为盘面比，Z 叶数。"""
    if J < 0:
        raise ValueError(f"J 必须 >= 0，收到 {J}")
    return (
        _eval(CT_TERMS, J, P_D, Ae_Ao, Z),
        _eval(CQ_TERMS, J, P_D, Ae_Ao, Z),
    )


def ct_from_kt(Kt: float, J: float) -> float:
    """由 K_T 与 J 反算作动盘推力载荷系数 C_T = 8 K_T / (π J²)。"""
    if J <= 0:
        raise ValueError("J=0（系柱工况）下 C_T 未定义")
    return 8.0 * Kt / (math.pi * J * J)


def eta_ideal(Kt: float, J: float) -> Optional[float]:
    """Rankine–Froude 作动盘理想效率 2/(1+sqrt(1+C_T))；J=0 或 K_T<=0 时无上界意义。"""
    if J <= 0 or Kt <= 0:
        return None
    ct = ct_from_kt(Kt, J)
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


def open_water(J: float, P_D: float, Ae_Ao: float, Z: float) -> OpenWaterPoint:
    """单点敞水性能，含 η0 与动量理论界的自洽打包（η0 仅在 K_T>0 时给出）。"""
    Kt, Kq = kt_kq(J, P_D, Ae_Ao, Z)
    eta0 = (J / (2.0 * math.pi)) * (Kt / Kq) if (Kt > 0.0 and Kq > 1e-12) else None
    return OpenWaterPoint(J=J, Kt=Kt, Kq=Kq, eta0=eta0, eta_ideal=eta_ideal(Kt, J))


def curve(P_D: float, Ae_Ao: float, Z: float, Js: Optional[List[float]] = None) -> List[OpenWaterPoint]:
    """敞水曲线；默认 J = 0, 0.05, ..., 1.20。"""
    if Js is None:
        Js = [round(0.05 * i, 2) for i in range(25)]
    return [open_water(J, P_D, Ae_Ao, Z) for J in Js]


def zero_thrust_J(P_D: float, Ae_Ao: float, Z: float, hi: float = 1.6) -> float:
    """零推力进速比 J0（K_T(J0)=0），二分法，精度 1e-6。"""
    lo = 0.0
    if kt_kq(hi, P_D, Ae_Ao, Z)[0] > 0:
        raise ValueError("K_T 在搜索区间上界仍为正，无法定界零推力点")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if kt_kq(mid, P_D, Ae_Ao, Z)[0] > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


if __name__ == "__main__":  # pragma: no cover - 手工巡检入口
    print("provenance:", PROVENANCE)
    for tag, (pd, ae, z) in {
        "B4-70": (1.0, 0.70, 4),
        "B4-40": (1.0, 0.40, 4),
        "B4-55": (0.8, 0.55, 4),
    }.items():
        print(f"\n{tag} (P/D={pd}, Ae/Ao={ae}, Z={z})  J0={zero_thrust_J(pd, ae, z):.4f}")
        for p in curve(pd, ae, z, Js=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]):
            eta = "n/a " if p.eta0 is None else f"{p.eta0:.4f}"
            bd = "n/a " if p.eta_ideal is None else f"{p.eta_ideal:.4f}"
            print(f"  J={p.J:.2f} Kt={p.Kt:+.5f} Kq={p.Kq:.6f} eta0={eta} eta_ideal={bd}")