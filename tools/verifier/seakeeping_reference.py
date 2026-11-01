"""耐波域独立参考核（seakeeping reference kernel）。

用途：为 `/calculations/seakeeping`、`/ai-models/predict/seakeeping`、
`/model-test/generate/seakeeping`、`/dynamic-stability/roll-motion` 等耐波端点
提供 **不依赖平台实现** 的解析/半解析基准，使 D5 证据分级判决有可复算依据。

三层参考（严格性递减，判决时优先用前层）：

L1 精确解析恒等式（无自由参数，可作 hard criterion）
    - 深水色散关系  omega^2 = g k,  lambda = g T^2 / (2 pi)
    - 遭遇频率      omega_e = |omega - k U cos(beta)|, beta = 相对艏向角
      （head seas beta=180° -> omega_e = omega + kU；following beta=0° -> |omega - kU|）
    - 垂荡 RAO 的两个精确极限
          lambda/L -> 0 :  RAO_3 -> 0     （短波：船体滤掉短波，垂荡趋于静止）
          lambda/L -> inf : RAO_3 -> 1    （长波：船随波面整体上下，无相对运动）
      因此 |RAO_3| <= Q3 = 1/(2 zeta_3) （共振放大上限），且常值 RAO 同时违反两端。
    - 横摇 RAO 的两个精确极限（以波面有效波倾为输入）
          omega -> 0 :   RAO_4 -> 1   （长波：船随波面倾斜，横摇角=波倾角）
          omega -> inf:  RAO_4 -> 0
      共振 |RAO_4(omega_n)| = 1/(2 zeta_4)（精确，任何线性阻尼系统）。
    - 甲板垂向加速度  a_z = omega_e^2 * eta_3a  （简谐运动二阶导，精确）

L2 教科书单自由度（SDOF）线性响应核
    |H(omega)| = K_eq / sqrt((omega_n^2 - omega^2)^2 + (2 zeta omega_n omega)^2)
    - 垂荡/纵摇：K_eq = omega_n^2，再乘 Froude-Krylov 吃水衰减 exp(-k T)
      -> 自动满足 L1 的两端极限；共振放大 1/(2 zeta)
    - 横摇：K_eq = omega_n^2（输入为波倾），即 |H| = 1/sqrt((1-nu^2)^2+(2 zeta nu)^2)

L3 几何经验估计（Bhattacharyya / Journée 工程近似，含自由参数）
    - 水线面系数     C_wp = (1 + 2 Cb) / 3
    - 垂荡固有频率   omega_n3 = sqrt(g A_w / (nabla (1 + a33))),  A_w = C_wp L B
    - 纵摇固有频率   omega_n5 = sqrt(g I_L / (nabla k_yy^2 (1 + a55))), I_L = C_wp L^3 B / 12
    - 横摇固有周期   T_4 = 2 pi k_xx / sqrt(g GM),  k_xx = 0.35 B（IMO/RMS 经验回转半径）
    L3 只用于给出"量级是否合理"，绝对精度记 WITHHELD（未对船模试验标定）。

注意（诚实边界）：本参考核 **不是** 切片理论求解器。它能判死的是
"违反精确极限/无频率依赖/无艏向依赖/量纲不通"这类结构性错误；
它不能用于判定真实 RAO 曲线的细节误差 < 3%。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

G = 9.80665
TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------
# L1 精确解析
# --------------------------------------------------------------------------
def deep_water_wavelength(period_s: float) -> float:
    """深水波长 lambda = g T^2 / (2 pi) [m]。"""
    return G * period_s ** 2 / TWO_PI


def deep_water_period(wavelength_m: float) -> float:
    """深水周期 T = sqrt(2 pi lambda / g) [s]。"""
    return math.sqrt(TWO_PI * wavelength_m / G)


def deep_water_wavenumber(period_s: float) -> float:
    """深水波数 k = 2 pi / lambda = omega^2 / g [1/m]。"""
    return TWO_PI / deep_water_wavelength(period_s)


def circular_frequency(period_s: float) -> float:
    return TWO_PI / period_s


def encounter_frequency(period_s: float, speed_mps: float, heading_deg: float) -> float:
    """遭遇频率 omega_e = |omega - k U cos(beta)|。

    heading_deg 约定与平台 `/calculations/seakeeping` 一致：**相对艏向角**，
    180° = 顶浪(head seas)，0° = 顺浪(following seas)，90°/270° = 横浪。
    顺浪且 kU > omega 时 omega_e 取绝对值（波浪追越船体，遭遇周期符号翻转）。
    """
    omega = circular_frequency(period_s)
    k = omega ** 2 / G
    return abs(omega - k * speed_mps * math.cos(math.radians(heading_deg)))


def vertical_accel_amplitude(heave_amplitude_m: float, omega_e: float) -> float:
    """简谐垂荡 z = eta cos(omega_e t) 的加速度幅值 omega_e^2 * eta [m/s^2]。"""
    return omega_e ** 2 * heave_amplitude_m


def sdof_amplitude(w: float, wn: float, zeta: float) -> float:
    """SDOF 位移放大因子 |H| = wn^2 / sqrt((wn^2-w^2)^2 + (2 zeta wn w)^2)。

    w -> 0   : |H| -> 1
    w -> inf : |H| -> 0
    w = wn   : |H| = 1 / (2 zeta)  （精确）
    """
    if wn <= 0.0 or zeta <= 0.0:
        raise ValueError("wn and zeta must be positive")
    num = wn ** 2
    den = math.hypot(wn ** 2 - w ** 2, 2.0 * zeta * wn * w)
    return num / den


def roll_rao(w: float, wn: float, zeta: float) -> float:
    """横摇 RAO（输入为波面有效波倾角）：|H| = 1 / sqrt((1-nu^2)^2 + (2 zeta nu)^2)。

    w -> 0  : |H| -> 1（随波倾倾斜）
    w = wn  : |H| = 1/(2 zeta)
    w -> inf: |H| -> 0
    """
    if wn <= 0.0 or zeta <= 0.0:
        raise ValueError("wn and zeta must be positive")
    nu = w / wn
    return 1.0 / math.hypot(1.0 - nu ** 2, 2.0 * zeta * nu)


def heave_rao(period_s: float, wn: float, zeta: float, draft_m: float) -> float:
    """垂荡 RAO（相对波幅），含 Froude-Krylov 吃水衰减 exp(-k T)。

    lambda/L -> 0 => k T 大 => RAO -> 0
    lambda/L -> inf => k T -> 0, w -> 0 => RAO -> 1
    """
    w = circular_frequency(period_s)
    k = w ** 2 / G
    return sdof_amplitude(w, wn, zeta) * math.exp(-k * draft_m)


def heave_rao_asymptotes(lam_over_L: float) -> tuple[float, float]:
    """垂荡 RAO 的精确两端极限（L1 判据用）。

    返回 (short_wave_limit, long_wave_limit)：
      lambda/L -> 0  : 0   —— 船体把短波滤掉，垂荡趋于静止
      lambda/L -> inf: 1   —— 船随波面整体运动，无相对运动
    两者均为线性势流理论的精确结论，不含任何经验参数。
    """
    _ = lam_over_L  # 端点极限与 lambda/L 无关，仅在两端成立
    return 0.0, 1.0


# --------------------------------------------------------------------------
# L3 几何经验估计
# --------------------------------------------------------------------------
def waterplane_coefficient(cb: float) -> float:
    """水线面系数工程近似 C_wp = (1 + 2 Cb)/3。"""
    return (1.0 + 2.0 * cb) / 3.0


@dataclass
class Hull:
    length: float
    beam: float
    draft: float
    displacement_t: float
    cb: float
    gm: float | None = None       # 若有直接给定 GM
    kg: float | None = None       # 若无 GM 但有 KG（配合 KB/BM 估计）
    kb: float | None = None
    depth: float | None = None    # 若都无，用 KB=0.53T, BM=Cwp B^2/(12 Cb T), KG=0.55D 估计

    # 交运模型系数（含自由参数）
    a33: float = 0.5      # 垂荡附加质量 / 排水量
    a55: float = 1.0      # 纵摇附加惯性 / 纵摇惯性
    a44: float = 0.25     # 横摇附加惯性 / 横摇惯性
    kxx_ratio: float = 0.35   # 横摇回转半径 / B（IMO/RMS）
    kyy_ratio: float = 0.26   # 纵摇回转半径 / L
    zeta3: float = 0.15
    zeta5: float = 0.10
    zeta4: float = 0.10

    @property
    def volume(self) -> float:
        return self.displacement_t / 1.025  # 海水密度近似 1.025 t/m^3

    @property
    def aw(self) -> float:
        return waterplane_coefficient(self.cb) * self.length * self.beam

    def gm_estimate(self) -> float:
        """GM 估计：优先直给；其次 KB+BM-KG；最后 KB=0.53T, KG=0.70D（D 缺省 1.35T）。

        KB=0.53T、KG=0.70D、D=1.35T 为干货船/油船工程统计带的中值；
        绝对精度未标定，GM 应尽量由调用方直给（L3 记 WITHHELD）。
        """
        if self.gm is not None:
            return self.gm
        kb = self.kb if self.kb is not None else 0.53 * self.draft
        bm = waterplane_coefficient(self.cb) * self.beam ** 2 / (12.0 * self.cb * self.draft)
        kg = self.kg if self.kg is not None else 0.70 * (self.depth or 1.35 * self.draft)
        return kb + bm - kg

    def roll_natural_period(self) -> float:
        kxx = self.kxx_ratio * self.beam
        return TWO_PI * kxx / math.sqrt(G * self.gm_estimate())

    def roll_natural_frequency(self) -> float:
        return TWO_PI / self.roll_natural_period()

    def heave_natural_frequency(self) -> float:
        # omega_n = sqrt(rho g A_w / (nabla + a33 nabla))，rho*nabla = displacement
        return math.sqrt(G * self.aw / (self.volume * (1.0 + self.a33)))

    def pitch_natural_frequency(self) -> float:
        il = waterplane_coefficient(self.cb) * self.length ** 3 * self.beam / 12.0
        kyy = self.kyy_ratio * self.length
        iyy = self.volume * kyy ** 2
        return math.sqrt(G * il / (iyy * (1.0 + self.a55)))

    def roll_rao(self, period_s: float, zeta: float | None = None) -> float:
        return roll_rao(circular_frequency(period_s), self.roll_natural_frequency(),
                        zeta if zeta is not None else self.zeta4)

    def heave_rao(self, period_s: float) -> float:
        return heave_rao(period_s, self.heave_natural_frequency(), self.zeta3, self.draft)

    def pitch_rao(self, period_s: float) -> float:
        """纵摇 RAO [rad/m]，含吃水衰减与 wave slope 归一。"""
        w = circular_frequency(period_s)
        k = w ** 2 / G
        return sdof_amplitude(w, self.pitch_natural_frequency(), self.zeta5) * math.exp(-k * self.draft) * k


def encounter_period(period_s: float, speed_mps: float, heading_deg: float) -> float:
    return TWO_PI / encounter_frequency(period_s, speed_mps, heading_deg)


def heading_roll_factor(heading_deg: float) -> float:
    """横摇的艏向权重 |sin(beta)|（横浪最大、顶浪/顺浪最小）。

    这是耐波性教科书/经验结论（横摇主要由波倾横向分量激励）。
    平台实测形状与此一致（见 docs/verifier/SEAKEEPING_REVIEW.md K5）。
    """
    return abs(math.sin(math.radians(heading_deg)))


def spectral_moments(source: Iterable[tuple[float, float]]) -> tuple[float, float]:
    """由 (omega, S(omega)) 离散点梯形积分得 m0, m2（用于有义值与加速度统计）。"""
    pts = sorted(source)
    m0 = m2 = 0.0
    for (w1, s1), (w2, s2) in zip(pts, pts[1:]):
        dw = w2 - w1
        m0 += 0.5 * (s1 + s2) * dw
        m2 += 0.5 * (s1 * w1 ** 2 + s2 * w2 ** 2) * dw
    return m0, m2


def rayleigh_third_height(m0: float) -> float:
    """有义值 H_1/3 = 4 sqrt(m0)（窄带 Rayleigh）。"""
    return 4.0 * math.sqrt(max(m0, 0.0))