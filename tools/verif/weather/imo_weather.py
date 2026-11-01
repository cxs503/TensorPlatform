#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IMO 完整稳性（天气衡准）独立参考实现 —— 零平台依赖。

被复核对象::

    POST /api/v1/dynamic-stability/weather-criterion   (summary="天气衡准检查")
    实现: backend/api/dynamic_stability.py :: WeatherCriterionChecker.check

规范依据（IS Code 2008, 经 MSC.267(85) 通过，Part A 第 2.3 节
"Severe wind and rolling criterion (weather criterion)"）：

  2.3.1  假定船舶承受垂直于中线的定常风压 P = 504 Pa，对应定常风倾力臂

              lw1 = P * A * Z / (1000 * g * Delta)          [m]

          A  水线以上受风面积 [m^2]，Z 由 A 形心到水线以下侧向面积形心
          （或近似到 T/2）的垂向距离 [m]，Delta 排水量 [t]，g=9.80665。

  2.3.2  船舶自定常风平衡角 theta0 出发，因波浪作用向上风侧横摇到
          theta1（横摇回摆角）。

  2.3.3  随后受阵风作用，阵风风倾力臂

              lw2 = 1.5 * lw1                              [m]

  2.3.4  衡准:                                            b >= a

          a = A 区面积 = 自 theta1 到 GZ 曲线与 lw2 的*上风侧交点* thetaC 之间，
              lw2 高出 GZ 的积分（阵风无法静态抵御的那部分能量）
          b = B 区面积 = 自 thetaC 到 *下风侧交点* theta2 之间，
              GZ 高出 lw2 的积分（可抵消阵风功的复原能量）
          theta2 取 min(GZ 与 lw2 的下风侧交点, 进水角 theta_f, 50 deg)

  本模块只用定义式（CONF_HIGH），**不**转录 IS Code 表 2.3.4 (X1/X2/s/k 表)
  来反算回摆角 theta1——表值属 CONF_MED，一旦转录错误会污染判决。故 theta1
  以扫描方式(0..30 deg)给出判据族，任何 theta1 下结论一致才判 MOCK。
  这不损失判决力：复核目标是"平台表达是否等于规范表达"，与 theta1 表值
  无关（平台的实现里根本没有 theta1 与 lw2）。

两条互不共享数值路径的积分（实现自校）：
  ROUTE_LINEAR : GZ 分段线性插值 + 交点精确求解 + 分段梯形（对线性段精确）
  ROUTE_PCHIP  : GZ 单调三次(PCHIP)插值 + 每段 Gauss-Legendre 数值积分
两者之差 = 插值模型敏感度，用于确认判据不是数值伪影。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

G_STANDARD = 9.80665       # m/s^2, ISO 80000-3
P_STD = 504.0              # Pa, IS Code 2008 A.2.3.1
GUST_FACTOR = 1.5          # lw2 = 1.5 * lw1, A.2.3.3
THETA2_CAP_DEG = 50.0      # A.2.3.4

CONF_HIGH = "CONF_HIGH"


# --------------------------------------------------------------------------
# 几何 / 风倾力臂
# --------------------------------------------------------------------------
def wind_heeling_lever(P: float, A: float, Z: float, displacement_t: float,
                       g: float = G_STANDARD) -> float:
    """定常风倾力臂 lw1 [m]（IS Code 2008 A.2.3.1 定义式）。

    P [Pa], A [m^2], Z [m], displacement [t] -> lw1 [m]。
    量纲: Pa*m^2*m = N*m，除以 (kg * m/s^2) 得 m。
    """
    if displacement_t <= 0:
        raise ValueError("displacement must be > 0")
    return P * A * Z / (1000.0 * g * displacement_t)


def mechanical_check_lw1() -> Dict[str, float]:
    """lw1 的解析自检（手算可复现的一组数）。"""
    P, A, Z, D = 504.0, 500.0, 9.0, 8000.0
    lw1 = wind_heeling_lever(P, A, Z, D)
    # 手算: 504*500*9 = 2 268 000 N*m ; 1000*9.80665*8000 = 78 453 200 kg*m/s^2
    hand = 2268000.0 / 78453200.0
    return {"lw1": lw1, "hand": hand, "rel_err": abs(lw1 - hand) / hand}


# --------------------------------------------------------------------------
# GZ 曲线
# --------------------------------------------------------------------------
@dataclass
class GZCurve:
    """右倾力臂曲线 GZ(theta)，theta 单位 deg，单调升序采样。"""

    angles_deg: np.ndarray
    gz_m: np.ndarray

    def __post_init__(self) -> None:
        a = np.asarray(self.angles_deg, dtype=float)
        g = np.asarray(self.gz_m, dtype=float)
        if a.shape != g.shape or a.ndim != 1 or a.size < 3:
            raise ValueError("GZ curve needs >=3 matched samples")
        if np.any(np.diff(a) <= 0):
            raise ValueError("angles must be strictly increasing")
        self.angles_deg, self.gz_m = a, g

    @property
    def theta_f_deg(self) -> Optional[float]:
        """稳性消失角：GZ 由正变负的最小交点；全程为正则 None。"""
        a, g = self.angles_deg, self.gz_m
        for i in range(a.size - 1):
            if g[i] > 0.0 >= g[i + 1]:
                if g[i] == g[i + 1]:
                    return float(a[i + 1])
                t = g[i] / (g[i] - g[i + 1])
                return float(a[i] + t * (a[i + 1] - a[i]))
            if i == 0 and g[0] <= 0.0 and np.any(g > 0.0):
                continue
        return None

    @property
    def theta_max_gz_deg(self) -> float:
        return float(self.angles_deg[int(np.argmax(self.gz_m))])

    def gz_at(self, theta_deg: float) -> float:
        return float(np.interp(theta_deg, self.angles_deg, self.gz_m))

    def crossings(self, level: float) -> List[float]:
        """GZ(theta) = level 的全部交点（deg，升序），分段线性精确求解。"""
        a, g = self.angles_deg, self.gz_m - level
        out: List[float] = []
        for i in range(a.size - 1):
            y0, y1 = g[i], g[i + 1]
            if y0 == 0.0:
                out.append(float(a[i]))
            if y0 * y1 < 0.0:
                t = y0 / (y0 - y1)
                out.append(float(a[i] + t * (a[i + 1] - a[i])))
        if g[-1] == 0.0:
            out.append(float(a[-1]))
        # 去重（相邻数值重复）
        dedup: List[float] = []
        for x in out:
            if not dedup or abs(x - dedup[-1]) > 1e-9:
                dedup.append(x)
        return dedup

    # ---------------- 积分路径 A: 分段线性 + 精确梯形 ----------------
    def _area_between(self, level: float, lo: float, hi: float) -> float:
        """integral_{lo}^{hi} (GZ(theta) - level) dtheta [m*rad]，theta 为 deg。"""
        if hi <= lo:
            return 0.0
        a, g = self.angles_deg, self.gz_m
        nodes = [lo]
        for x in a:
            if lo < x < hi:
                nodes.append(float(x))
        nodes.append(hi)
        total = 0.0
        for x0, x1 in zip(nodes[:-1], nodes[1:]):
            y0 = np.interp(x0, a, g) - level
            y1 = np.interp(x1, a, g) - level
            total += 0.5 * (y0 + y1) * (x1 - x0) * np.pi / 180.0
        return total

    @property
    def area_total(self) -> float:
        return self._area_between(0.0, float(self.angles_deg[0]), float(self.angles_deg[-1]))


# ---------------- 积分路径 B: PCHIP + Gauss-Legendre ----------------
def _pchip_interp(xq: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """单调三次 Hermite（Fritsch-Carlson 斜率），不依赖 scipy。"""
    xq = np.atleast_1d(np.asarray(xq, dtype=float))
    h = np.diff(x)
    delta = np.diff(y) / h
    n = x.size
    m = np.zeros(n)
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] > 0:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
        else:
            m[i] = 0.0
    idx = np.clip(np.searchsorted(x, xq) - 1, 0, n - 2)
    t = (xq - x[idx]) / h[idx]
    h00 = 2 * t**3 - 3 * t**2 + 1
    h10 = t**3 - 2 * t**2 + t
    h01 = -2 * t**3 + 3 * t**2
    h11 = t**3 - t**2
    return h00 * y[idx] + h10 * h[idx] * m[idx] + h01 * y[idx + 1] + h11 * h[idx] * m[idx + 1]


def area_between_pchip(curve: GZCurve, level: float, lo: float, hi: float,
                       n_gauss: int = 24) -> float:
    """ROUTE_PCHIP: 每采样段内 Gauss-Legendre 积分（PCHIP 插值）。"""
    if hi <= lo:
        return 0.0
    # Gauss-Legendre 节点/权重（[-1,1]）
    xi, w = np.polynomial.legendre.leggauss(n_gauss)
    a, g = curve.angles_deg, curve.gz_m
    nodes = [lo] + [float(x) for x in a if lo < x < hi] + [hi]
    total = 0.0
    for x0, x1 in zip(nodes[:-1], nodes[1:]):
        half = 0.5 * (x1 - x0)
        xs = 0.5 * (x0 + x1) + half * xi
        ys = _pchip_interp(xs, a, g) - level
        total += half * float(np.sum(w * ys)) * np.pi / 180.0
    return total


# --------------------------------------------------------------------------
# 天气衡准判据
# --------------------------------------------------------------------------
def weather_criterion(curve: GZCurve, lw1: float, theta1_deg: float,
                      theta_f_deg: Optional[float] = None,
                      theta_cap_deg: float = THETA2_CAP_DEG,
                      route: str = "linear") -> Dict[str, object]:
    """按 IS Code 2008 A.2.3 计算面积 a / b 与判据 b >= a。

    返回 dict，含 theta0（定常风平衡角）、thetaC、theta2、area_a、area_b、ratio。
    """
    if lw1 < 0:
        raise ValueError("lw1 must be >= 0")
    lw2 = GUST_FACTOR * lw1
    tf = curve.theta_f_deg if theta_f_deg is None else float(theta_f_deg)

    cross = curve.crossings(lw2)
    if not cross:
        # GZ 从未达到阵风力臂 -> 无法静平衡，b = 0，判不满足
        hi = tf if tf is not None else float(curve.angles_deg[-1])
        area_a = max(0.0, -int_gz_minus_level(curve, lw2, theta1_deg, hi, route))
        return {"lw1": lw1, "lw2": lw2, "theta0": None, "thetaC": None,
                "theta2": None, "area_a": area_a, "area_b": 0.0,
                "ratio": 0.0, "compliant": False, "note": "GZ never reaches lw2"}

    theta_c = cross[0]
    leeward = [x for x in cross if x > theta_c]
    theta_2 = leeward[0] if leeward else None
    if theta_2 is None:
        theta_2 = min(curve.angles_deg[-1], theta_cap_deg)
    else:
        theta_2 = min(theta_2, theta_cap_deg)
    if tf is not None:
        theta_2 = min(theta_2, tf)

    # theta0: 定常风(lw1)平衡角（若 lw1 从未被 GZ 超越则取曲线末端）
    c1 = curve.crossings(lw1)
    theta_0 = c1[0] if c1 else None

    if theta_2 <= theta1_deg:
        # 回摆角已越过下风侧交点：阵风全程无法静态抵御
        area_a = max(0.0, -int_gz_minus_level(curve, lw2, theta1_deg, theta_2, route))
        area_b = 0.0
    else:
        # a 区: 自 theta1 到上风侧交点 thetaC，lw2 高出 GZ 的部分（无法静态抵御）
        area_a = max(0.0, -int_gz_minus_level(curve, lw2, theta1_deg,
                                              min(theta_c, theta_2), route))
        # b 区: 自 thetaC 到下风侧交点 theta2，GZ 高出 lw2 的复原能量
        area_b = max(0.0, int_gz_minus_level(curve, lw2, theta_c, theta_2, route))
    ratio = (area_b / area_a) if area_a > 1e-12 else float("inf")
    return {"lw1": lw1, "lw2": lw2, "theta0": theta_0, "thetaC": theta_c,
            "theta2": theta_2, "area_a": area_a, "area_b": area_b,
            "ratio": ratio, "compliant": bool(area_b >= area_a),
            "note": f"route={route}"}


def int_gz_minus_level(curve: GZCurve, level: float, lo: float, hi: float,
                       route: str = "linear") -> float:
    """signed integral of (GZ(theta) - level) dtheta over [lo, hi] [m*rad]。

    两条路径：'linear' = 分段线性插值 + 分段梯形（对线性段精确）；
    'pchip' = PCHIP 插值 + Gauss-Legendre（独立数值路径，用于敏感度自校）。
    """
    if hi <= lo:
        return 0.0
    if route == "pchip":
        return area_between_pchip(curve, level, lo, hi)
    return curve._area_between(level, lo, hi)


def criterion_sweep(curve: GZCurve, lw1: float,
                    theta1_grid: Sequence[float] = (0, 5, 10, 15, 20, 25, 30),
                    route: str = "linear") -> List[Dict[str, object]]:
    out = []
    for t1 in theta1_grid:
        r = weather_criterion(curve, lw1, float(t1), route=route)
        r["theta1"] = float(t1)
        out.append(r)
    return out


def ratio_vs_wind_area(curve: GZCurve, P: float, A_grid: Sequence[float], Z: float,
                       displacement_t: float, theta1_deg: float,
                       g: float = G_STANDARD) -> List[Dict[str, float]]:
    rows = []
    for A in A_grid:
        lw1 = wind_heeling_lever(P, A, Z, displacement_t, g)
        r = weather_criterion(curve, lw1, theta1_deg)
        rows.append({"A": float(A), "lw1": lw1, "lw2": r["lw2"],
                     "ratio": r["ratio"], "compliant": r["compliant"]})
    return rows


# --------------------------------------------------------------------------
# 平台表达（复刻 /api/v1/dynamic-stability/weather-criterion 的算式）
# --------------------------------------------------------------------------
def platform_expression(gz_angles: Sequence[float], gz_values: Sequence[float],
                        P: float, A: float, Z: float, displacement_t: float,
                        g: float = 9.81, downflooding_deg: float = 50.0) -> Dict[str, float]:
    """逐行复刻平台 WeatherCriterionChecker.check 的算式（取证用，非判据）。

        area_b = 梯形积分 GZ 曲线全区间 (0 .. 最后采样角)
        wind_moment = P*A*Z ; wind_arm = wind_moment/(Delta*1000*g)
        area_a = wind_arm * 50deg
        ratio  = area_b / area_a ; compliant = ratio >= 1.0

    与 IS Code 2008 的差异见 docs/verif/weather_criterion_verification.md。
    """
    a = np.asarray(gz_angles, dtype=float)
    gz = np.asarray(gz_values, dtype=float)
    area_b = 0.0
    for i in range(a.size - 1):
        d_theta = (a[i + 1] - a[i]) * np.pi / 180.0
        area_b += 0.5 * (gz[i] + gz[i + 1]) * d_theta
    wind_moment = P * A * Z
    wind_arm = wind_moment / (displacement_t * 1000.0 * g)
    area_a = wind_arm * downflooding_deg * np.pi / 180.0
    ratio = area_b / area_a if area_a > 0 else 10.0
    return {"area_b": area_b, "wind_moment": wind_moment, "wind_arm": wind_arm,
            "area_a": area_a, "ratio": ratio, "compliant": bool(ratio >= 1.0)}


# --------------------------------------------------------------------------
# 自检用解析闭式基准
# --------------------------------------------------------------------------
def linear_gz_area_analytic(theta_lo: float, theta_hi: float, gz_slope: float,
                            level: float, theta_origin: float = 0.0) -> float:
    """GZ(theta)=gz_slope*(theta-theta_origin) 时 integral (GZ-level) dtheta 的闭式解。

    返回 [m*rad]，theta 以 deg 输入。
    """
    def F(t: float) -> float:
        # 原函数（换算到 rad 因子的解析积分）: ∫[gz_slope*(θ-θ0) - level] dθ
        # θ 用 deg，dθ_rad = dθ_deg * π/180
        return (0.5 * gz_slope * (t - theta_origin) ** 2 - level * (t - theta_origin)) * np.pi / 180.0
    return F(theta_hi) - F(theta_lo)


if __name__ == "__main__":  # pragma: no cover
    demo = GZCurve(np.array([0, 10, 20, 30, 40, 50, 60, 70, 77.5]),
                   np.array([0, 0.30, 0.62, 0.85, 0.92, 0.80, 0.55, 0.20, 0.0]))
    lw1 = wind_heeling_lever(P_STD, 1200.0, 9.0, 6000.0)
    print("lw1 =", lw1, "lw2 =", GUST_FACTOR * lw1)
    for row in criterion_sweep(demo, lw1):
        print(f"theta1={row['theta1']:5.1f}  a={row['area_a']:.5f}  "
              f"b={row['area_b']:.5f}  ratio={row['ratio']:.3f}  compliant={row['compliant']}")
    print("platform:", platform_expression(demo.angles_deg, demo.gz_m,
                                            P_STD, 1200.0, 9.0, 6000.0))