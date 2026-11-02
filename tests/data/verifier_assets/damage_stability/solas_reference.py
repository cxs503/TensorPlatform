"""
破舱稳性独立参考实现（只读验证用，不参与平台计算）
Independent reference implementation for damage-stability verification.

依据文献/规范：
  R1  SOLAS Ch.II-1 Part B-1 Reg.6 (MSC.216(82))  —— 达到分舱指数 R(L_s)
        cargo ship, L_s >= 80 m :  R = 1 - 128 / (L_s + 152)
        cargo ship, L_s <  80 m :  R = 1 - 152 / (L_s + 152)
        passenger ship          :  R = 1 - 5000 / (L_s + 2.5 N + 15225)
  R2  SOLAS Ch.II-1 Part B-1 Reg.7.2 (MSC.216(82)) —— 生存概率 s 的中间阶段形式
        s_int = [ min(GZmax/0.12, 1.0) * min(Range/16, 1.0) ] ** 0.25  （单区、无交叉注水、
        θe <= 7deg => K = 1；开口浸没项按 0 处理）
        截断：GZmax < 0.1 m 或 Range < 16 deg -> s = 0
              GZmax >= 0.2 m 且 Range >= 20 deg -> s = 1
  R3  损失浮力法（盒式驳船、矩形舱、自由注水）—— 平行下沉解析解
        t' = ∇0 / (A_w - μ A_c)，A_c = V_c / H_c （舱室水平截面积）
        横倾下界：tanθ >= M_T / (Δ_t * GM_max)，M_T = w * tcg，w = ρ μ V_c
        GM_max = KB_max + BM  （KG 取水线下界 0 时的最大可能稳性高，盒式驳船
                  KB_max = T'/2、BM = B^2/(12 T') 之上再加 1 m 余量以放宽上界）

这些都是可在纸面复算的闭式量，故可作为「机器可裁」判据。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RHO = 1.025  # t/m^3 海水


# ---------- R1: 要求分舱指数 ----------
def solas2009_required_index(Ls: float, ship_type: str = "cargo") -> float:
    """SOLAS 2009 要求的分舱指数 R。"""
    if ship_type == "cargo":
        denom = 128.0 if Ls >= 80.0 else 152.0
        return 1.0 - denom / (Ls + 152.0)
    if ship_type == "passenger":
        # N = 总人数；参考实现仅保留接口，验证场景为货船
        raise NotImplementedError("passenger R needs N; not used in this verification")
    raise ValueError(ship_type)


def platform_required_index(Ls: float) -> float:
    """平台实现（damage_stability.py::ProbabilisticDamageStability.calculate_A）
    的 R 公式：R = min(0.5 * Ls/100 + 0.5, 0.9)。
    此处镜像复刻，用于把「公式本身」和「运行实例」分别举证。"""
    return min(0.5 * Ls / 100.0 + 0.5, 0.9)


# ---------- R2: 生存概率 s ----------
def solas2009_s_intermediate(gz_max: float, range_deg: float) -> float:
    """Reg.7.2 中间阶段 s（单区、K=1、无开口项）。"""
    if gz_max < 0.1 or range_deg < 16.0:
        return 0.0
    if gz_max >= 0.2 and range_deg >= 20.0:
        return 1.0
    # Reg.7.2 原文在中间带内不做逐项截断，只对最终 s 截断于 1；逐项 min() 会
    # 把 0.1<=GZmax<0.2 的中间带压成常数，故此处只对结果截断。
    return min(1.0, ((gz_max / 0.12) * (range_deg / 16.0)) ** 0.25)


def attained_index(p: list[float], s: list[float]) -> float:
    """A = Σ p_i s_i（Reg.6 定义，单区求和）。"""
    return float(sum(pi * si for pi, si in zip(p, s)))


# ---------- R3: 盒式驳船损失浮力解析 ----------
@dataclass
class BoxBarge:
    L: float
    B: float
    T: float
    KG: float

    @property
    def nabla0(self) -> float:
        return self.L * self.B * self.T

    @property
    def delta0(self) -> float:
        return RHO * self.nabla0

    @property
    def Aw(self) -> float:
        return self.L * self.B

    @property
    def BM(self) -> float:
        return (self.L * self.B ** 3 / 12.0) / self.nabla0

    @property
    def GM_intact(self) -> float:
        return self.T / 2.0 + self.BM - self.KG


def damage_draft_box(barge: BoxBarge, Vc: float, Hc: float, mu: float) -> float:
    """矩形舱自由注水后的平行下沉终吃水（损失浮力法，忽略纵倾）。"""
    Ac = Vc / Hc
    Aw_eff = barge.Aw - mu * Ac
    if Aw_eff <= 0.0:
        raise ValueError("lost buoyancy exceeds waterplane area")
    return barge.nabla0 / Aw_eff


def heel_lower_bound_deg(barge: BoxBarge, Vc: float, mu: float, tcg: float) -> float:
    """非对称注水的横倾下界（度）。给出「物理上不可能为 0」的可裁门限。"""
    w = RHO * mu * Vc
    mt = w * abs(tcg)
    if mt == 0.0:
        return 0.0
    t_prime = damage_draft_box(barge, Vc, barge.T, mu)
    kb_max = t_prime / 2.0
    bm = barge.B ** 2 / (12.0 * t_prime)
    gm_max = kb_max + bm + 1.0          # +1 m 余量：把 GM 上界放宽，下界才稳健
    delta_t = barge.delta0 + w
    return math.degrees(math.atan(mt / (delta_t * gm_max)))


def platform_gz_heuristic(GM: float, heel_deg: float) -> float:
    """镜像复刻平台破损 GZ 经验式：GZ = GM sinθ (1 - 0.5 θ^2)，θ 用弧度。"""
    rad = math.radians(heel_deg)
    return GM * math.sin(rad) * (1.0 - 0.5 * rad ** 2)