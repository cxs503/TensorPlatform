"""
破舱稳性模块 (Damage Stability)
================================================================
SOLAS 2009 (MSC.216(82)) Ch.II-1 Part B-1 对齐实现。

本文件是 ship-performance-platform `backend/api/damage_stability.py` 的替换件
(drop-in replacement)，修复验证报告 id=1093 指出的 5 项缺陷：

  F1  R 采用 Reg.6 要求分舱指数 R(Ls)：
        货船 Ls >= 80 m : R = 1 - 128/(Ls + 152)
        货船 Ls <  80 m : R = 1 - 152/(Ls + 152)
      删除旧式 min(0.5*Ls/100 + 0.5, 0.9)（货船 Ls>=80 m 恒等 0.9，Ls=100 时最坏 +82.9%）。
  F2  新增 Reg.7.2 生存概率 s：由破损后 GZ 曲线的 GZmax 与 Range 唯一决定，
      s = min(1, ((GZmax/0.12)*(Range/16))**0.25)；GZmax < 0.1 m 或 Range < 16 deg -> 0；
      GZmax >= 0.2 m 且 Range >= 20 deg -> 1。请求体中的 survival 一律忽略
      （旧实现 A = sum(p*s) 为纯回声，换船不变）。
  F3  破损几何进入计算（损失浮力法）：
        * 水线面损失 sum(mu * A_breach)，A_breach 由舱室几何与 damage_extent 的交集决定
        * 平行下沉 t' = nabla0 / (Aw - sum(mu * A_breach))
        * 纵倾由损失水线面对 LCF 的纵向力矩求 tau = -dx * nabla0 / I_L
        * 横倾由舱心横向力矩平衡（GZ(theta_e) = 0 的根）求得
      damage_extent x10 不再“逐项输出不变”。
  F4  删除二值 survival_probability = 1.0 if imo_compliant else 0.5，改用物理 s (s_factor)；
      survival_probability 保留为弃用别名，值恒等 s_factor。
  F5  衡准集对齐 Reg.7.2（横倾 theta_e <= 7 deg / GZmax >= 0.10 m / Range >= 16 deg /
      开口不浸没 / s > 0）；旧的 freeboard > 0.3 m 降级为 _diagnostics 中的非 SOLAS 参考项，
      不参与合规判定。

物理模型与适用边界（详见 docs/verif/damage_stability_repair.md）：
  * 损失浮力法：重量不变，进水舱不再提供浮力，故浮力体积恒为 nabla0；
    水线面按 sum(mu*A_breach) 损失，给出平行下沉解析解 t' = nabla0/(Aw - sum(mu*A_breach))。
  * GZ 曲线：壁式截面等截面近似 + 逐站垂向积分
        A(d,theta) = int [h - g] dy,             h = clamp(z_w(y), 0, D)
        y_B = int y(h-g) dy / A
        z_B = int [h^2/2 - (z_b g + g^2/2)] dy / A
        GZ(theta) = y_B cos(theta) + (z_B - KG) sin(theta) - GG0 sin(theta)
    对矩形截面驳船（本域验收基准）该积分精确；对一般船型是等宽壁式近似。
  * 自由液面修正 GG0 = sum(i_x) / nabla0（i_x 为破损自由液面面积惯性矩）。
  * 单位：长度 m、体积 m³、质量 t、角度 deg（对外）/ rad（内部）、GZ m。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["破舱稳性"])

RHO = 1.025          # 海水密度 t/m³
NY = 241             # 截面横向离散点数（梯形积分，壁式截面下误差 < 1e-3 m³）


def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    """兼容 numpy 1.x / 2.x 的梯形积分。"""
    fn = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(fn(y, x))


# ==================== 规范函数（SOLAS 2009）====================

def solas2009_required_index(Ls: float, ship_type: str = "cargo") -> float:
    """SOLAS 2009 Reg.6 要求的分舱指数 R。"""
    if ship_type == "passenger":
        raise NotImplementedError("passenger R 需要人数 N；本模块只支持货船")
    if ship_type != "cargo":
        raise ValueError(f"unknown ship_type: {ship_type}")
    denom = 128.0 if Ls >= 80.0 else 152.0
    return 1.0 - denom / (Ls + 152.0)


def solas2009_s_index(gz_max: float, gz_range_deg: float) -> float:
    """SOLAS 2009 Reg.7.2 生存概率 s（单区、K=1、无交叉注水、无开口项）。"""
    if gz_max < 0.1 or gz_range_deg < 16.0:
        return 0.0
    if gz_max >= 0.2 and gz_range_deg >= 20.0:
        return 1.0
    return min(1.0, ((gz_max / 0.12) * (gz_range_deg / 16.0)) ** 0.25)


# ==================== 数据模型 ====================

class Compartment(BaseModel):
    """舱室定义（几何字段可选；缺失时按容积推断等面积方形舱）"""
    name: str
    volume: float         # 容积 m³
    LCG: float            # 纵向重心位置 m（相对舯；本模块用于纵倾，随船体系一致即可）
    TCG: float            # 横向重心位置 m
    VCG: float            # 垂向重心位置 m
    permeability: float   # 渗透率 mu
    location: str = ""    # 位置描述
    # --- F3 新增：舱室几何（可选）---
    length: Optional[float] = None    # 舱长 m
    breadth: Optional[float] = None   # 舱宽 m
    height: Optional[float] = None    # 舱高 m


class ShipData(BaseModel):
    """船舶数据"""
    L: float
    B: float
    T: float
    D: float
    Cb: float
    displacement: float   # 排水量 t

    LCG: float
    TCG: float
    VCG: float            # KG (m)

    compartments: List[Compartment] = []


class DamageScenario(BaseModel):
    """破损场景"""
    damaged_compartments: List[str]
    damage_location: str = "side"                      # side/bottom/bow/stern
    damage_extent: Dict[str, float] = {}               # {length, width, height} m
    wave_height: float = 0.0
    wind_speed: float = 0.0


class DamageStabilityInput(BaseModel):
    """破舱稳性输入"""
    ship: ShipData
    damage: DamageScenario
    include_wave_effects: bool = False
    include_wind_effects: bool = False


class DamageStabilityOutput(BaseModel):
    """破舱稳性输出"""
    # 平衡状态
    final_draft: float
    final_trim: float
    final_heel: float

    # 进水量
    flooded_volume: float
    flooded_mass: float

    # 稳性指标
    GM_damage: float
    GZ_max: float
    GZ_range: float
    GZ_max_angle: float
    area_GZ: float

    # SOLAS 2009 Reg.7.2 生存概率（F2/F4：物理值，非二值）
    s_factor: float
    survival_probability: float   # 弃用别名，恒等 s_factor

    # 衡准（F5：Reg.7.2）
    imo_criteria: Dict[str, Dict]
    imo_compliant: bool

    time_to_sink: Optional[float] = None
    compartment_status: List[Dict] = []
    geometry_check: Dict = {}
    method: str = "破舱稳性分析（SOLAS 2009 Reg.6/7.2，损失浮力法）"


class ProbabilisticInput(BaseModel):
    """概率破舱稳性输入"""
    ship: ShipData
    damage_zones: List[Dict]


class ProbabilisticOutput(BaseModel):
    """概率破舱稳性输出"""
    A_value: float
    R_value: float
    compliant: bool
    zone_contributions: List[Dict]
    method: str = "概率破舱稳性（SOLAS 2009 Reg.6/7.2）"
    Ls: float = 0.0
    input_s_ignored: bool = False


# ==================== 核心实现 ====================

class DamageStabilityCalculator:
    """破舱稳性计算器：损失浮力法 + 壁式截面 GZ 积分 + SOLAS Reg.7.2 s"""

    def __init__(self):
        self.rho = RHO
        self._yb_cache: Dict[float, Tuple[float, float]] = {}

    # ---------- 几何 ----------
    @staticmethod
    def _intact_geometry(ship: ShipData) -> Dict[str, float]:
        """等宽壁式等效箱体：宽度 B_wp = Cw*B、吃水取输入 T（无损时平衡吃水恒为 T）。

        * 水线面积 Aw = Cw*L*B（真实水线面）
        * 等效排水体积 nabla_ws = Aw*T（壁式假设下的排水量；Cb=1 驳船时 = nabla0）
        * Cb 通过 Cw = (1+2Cb)/3 进入模型（一阶船型近似，见文档 §局限）
        """
        if ship.L <= 0 or ship.B <= 0 or ship.T <= 0 or ship.D <= 0:
            raise HTTPException(status_code=422, detail="L/B/T/D 必须为正")
        if not (0.0 < ship.Cb <= 1.05):
            raise HTTPException(status_code=422, detail="Cb 必须在 (0,1] 内")
        Cw = (1.0 + 2.0 * ship.Cb) / 3.0
        B_wp = Cw * ship.B
        Aw = Cw * ship.L * ship.B
        return {
            "Cw": Cw,
            "B_wp": B_wp,
            "nabla0": ship.Cb * ship.L * ship.B * ship.T,   # Cb·L·B·T（几何校验用）
            "nabla_ws": Aw * ship.T,                        # 壁式等效排水体积
            "Aw": Aw,
            "I_T0": ship.L * B_wp ** 3 / 12.0,
            "I_L0": ship.L ** 3 * B_wp / 12.0,
            "y": np.linspace(-B_wp / 2.0, B_wp / 2.0, NY),
        }

    @staticmethod
    def _geometry_check(ship: ShipData, g: Dict[str, float]) -> Dict:
        """Cb/L/B 一致性预检（平台精度标准：几何必须先验证）。"""
        nabla_disp = ship.displacement / RHO      # 排水量 t -> 体积 m³
        nabla_geo = g["nabla0"]
        dev = abs(nabla_disp - nabla_geo) / max(nabla_geo, 1e-9)
        return {
            "nabla_from_CbLBT": round(nabla_geo, 3),
            "nabla_wall_sided_equivalent": round(g["nabla_ws"], 3),
            "waterplane_breadth": round(g["B_wp"], 3),
            "nabla_from_displacement": round(nabla_disp, 3),
            "relative_deviation": round(dev, 4),
            "passed": dev <= 0.15,
            "note": "排水量与 Cb·L·B·T 偏差 >15% 时几何输入不可信（仅告警，不参与 SOLAS 合规判定）",
        }

    @staticmethod
    def _compartment_geometry(comp: Compartment, extent: Dict[str, float],
                              D: float) -> Dict[str, float]:
        h_c = float(comp.height) if comp.height else float(extent.get("height") or D)
        h_c = max(h_c, 1e-6)
        if comp.length and comp.breadth:
            l_c, b_c = float(comp.length), float(comp.breadth)
            Ac = l_c * b_c                       # 显式平面几何优先
        else:
            Ac = float(comp.volume) / h_c        # 由容积与舱高推断水平截面积
            l_c = math.sqrt(Ac)
            b_c = Ac / l_c
        z_b = max(0.0, comp.VCG - h_c / 2.0)
        return {"h": h_c, "Ac": Ac, "l": l_c, "b": b_c,
                "z_b": z_b, "z_t": z_b + h_c}

    # ---------- 破损几何 -> 进水量 / 水线面损失 ----------
    def _flooding_state(self, ship: ShipData, damage: DamageScenario) -> Dict[str, float]:
        """F3：由 damage_extent 与舱室几何求进水体积、水线面损失、液面惯性矩。"""
        extent = damage.damage_extent or {}
        ld = extent.get("length")
        bd = extent.get("width")
        hd = extent.get("height")

        V = mass = A_loss = 0.0
        mx = my = mz = 0.0
        i_x_free = 0.0
        y_sum = 0.0
        muA_x = 0.0
        z_low, z_high = math.inf, -math.inf
        detail = []

        for name in damage.damaged_compartments:
            comp = next((c for c in ship.compartments if c.name == name), None)
            if comp is None:
                raise HTTPException(status_code=422,
                                    detail=f"破损舱室不存在：{name}")
            geo = self._compartment_geometry(comp, extent, ship.D)
            fl = 1.0 if ld is None else min(1.0, max(0.0, float(ld) / geo["l"]))
            fb = 1.0 if bd is None else min(1.0, max(0.0, float(bd) / geo["b"]))
            fh = 1.0 if hd is None else min(1.0, max(0.0, float(hd) / geo["h"]))

            mu = float(comp.permeability)
            A_breach = geo["Ac"] * fl * fb                 # 破口覆盖的水平面积
            h_eff = geo["h"] * fh                          # 破口浸没到的舱内高度
            vol = mu * min(A_breach * h_eff, float(comp.volume))   # 受舱容上限约束
            m = self.rho * vol

            V += vol
            mass += m
            A_loss += mu * A_breach                        # 水线面损失（F3 核心）
            mx += vol * comp.LCG
            my += vol * comp.TCG
            mz += vol * comp.VCG
            l_eff = geo["l"] * fl
            b_eff = geo["b"] * fb
            i_x_free += mu * l_eff * b_eff ** 3 / 12.0      # 自由液面惯性矩
            y_sum += mu * A_breach * comp.TCG
            muA_x += mu * A_breach * comp.LCG
            z_low = min(z_low, geo["z_b"])
            z_high = max(z_high, geo["z_t"])
            detail.append({"name": name, "A_breach": A_breach, "flooded_volume": vol,
                           "breach_factor": {"length": fl, "width": fb, "height": fh}})

        if V > 0:
            cg = (mx / V, my / V, mz / V)
        else:
            cg = (ship.LCG, 0.0, 0.0)
        return {
            "volume": V, "mass": mass, "A_loss": A_loss, "i_x_free": i_x_free,
            "cg": cg, "y_flood": (y_sum / A_loss if A_loss > 0 else 0.0),
            "muA_x": muA_x, "x_flood": (muA_x / A_loss if A_loss > 0 else ship.LCG),
            "z_b": (z_low if z_low is not math.inf else 0.0),
            "z_t": (z_high if z_high > -math.inf else 0.0),
            "detail": detail,
        }

    # ---------- 平衡吃水 / 纵倾 ----------
    @staticmethod
    def _parallel_sinkage_keel_draft(ship: ShipData, g: Dict[str, float],
                                     flood: Dict[str, float]) -> float:
        """F3：损失浮力平行下沉终吃水 t' = nabla0 / (Aw - sum(mu*A_breach))"""
        Aw_eff = g["Aw"] - flood["A_loss"]
        if Aw_eff <= 0.0:
            raise HTTPException(status_code=422,
                                detail="破损水线面损失超过水线面积，船已倾覆/无法平衡")
        # nabla_ws = Aw*T（壁式等效体积）；Cb=1 驳船时即 nabla0，与解析解逐位一致
        return g["nabla_ws"] / Aw_eff

    @staticmethod
    def _trim(ship: ShipData, g: Dict[str, float], flood: Dict[str, float],
              t_prime: float) -> float:
        """F3：由损失水线面对 LCF 的纵向力矩求纵倾（m，+ 表示艏倾）。

        损失水线面形心相对原 LCF 的纵向偏移 dx = sum(mu*A_i*(x_F - x_i)) / Aw_eff，
        为让浮心回到 LCG 之下需要纵倾角 tau = -dx * nabla0 / I_L（rad），
        转换为垂线间纵倾 trim = tau * L（m）。
        """
        A_loss = float(flood["A_loss"])
        if A_loss <= 0.0:
            return 0.0
        x_F = ship.LCG if ship.LCG else ship.L / 2.0
        x_flood = float(flood["x_flood"])
        Aw_eff = g["Aw"] - A_loss
        if Aw_eff <= 0.0:
            return 0.0
        dx = A_loss * (x_F - x_flood) / Aw_eff
        tau = -dx * g["nabla0"] / g["I_L0"]
        return tau * ship.L

    # ---------- 壁式截面 GZ ----------
    def _section_integrals(self, ship: ShipData, g: Dict[str, float], d: float,
                           theta: float, flood: Dict[str, float]) -> Tuple[float, float, float]:
        """返回 (浮力面积 A, int y(h-g)dy, int [h^2/2 - (z_b g + g^2/2)]dy)。"""
        y = g["y"]
        cos_t = math.cos(theta)
        tan_t = math.tan(theta)
        z_w = d / cos_t + y * tan_t
        h = np.clip(z_w, 0.0, ship.D)
        A_loss = flood["A_loss"]
        if A_loss > 0.0:
            # 2D 等效：沿船长平均的破损宽度 b_f = mu*A_breach / L，横向位于损失面积形心
            b_f = min(A_loss / ship.L, g["B_wp"] * 0.98)
            half = g["B_wp"] / 2.0
            y_f = float(np.clip(flood["y_flood"], -(half - b_f / 2.0), half - b_f / 2.0))
            z_b = flood["z_b"]
            z_t = flood["z_t"]
            mask = (y >= y_f - b_f / 2.0) & (y <= y_f + b_f / 2.0)
            g_depth = np.clip(np.minimum(h, z_t) - z_b, 0.0, None) if mask.any() else np.zeros_like(h)
            g_depth = np.where(mask, g_depth, 0.0)
        else:
            z_b = 0.0
            g_depth = np.zeros_like(h)
        hh = h - g_depth
        A = _trapz(hh, y)
        My = _trapz(y * hh, y)
        Mz = _trapz(0.5 * h * h - (z_b * g_depth + 0.5 * g_depth * g_depth), y)
        return A, My, Mz

    def _solve_waterline(self, ship: ShipData, g: Dict[str, float], theta: float,
                         flood: Dict[str, float], target_A: float) -> float:
        lo, hi = -2.0 * ship.D, 4.0 * ship.D
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            A, _, _ = self._section_integrals(ship, g, mid, theta, flood)
            if A < target_A:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def _yb_zb(self, ship: ShipData, g: Dict[str, float], theta_deg: float,
               flood: Dict[str, float]) -> Tuple[float, float]:
        """壁式截面：给定横倾角求平衡水线下的浮心 (y_B, z_B)。按角度记忆化。"""
        key = round(float(theta_deg), 6)
        cache = self._yb_cache
        if key in cache:
            return cache[key]
        theta = math.radians(theta_deg)
        target_A = g["nabla_ws"] / ship.L
        d = self._solve_waterline(ship, g, theta, flood, target_A)
        A, My, Mz = self._section_integrals(ship, g, d, theta, flood)
        if A <= 0.0:
            val = (0.0, 0.0)
        else:
            val = (My / A, Mz / A)
        cache[key] = val
        return val

    def _gz(self, ship: ShipData, g: Dict[str, float], theta_deg: float,
            KG: float, GG0: float, flood: Dict[str, float]) -> float:
        """GZ(theta) = y_B cos(theta) + (z_B - KG) sin(theta) - GG0 sin(theta)。"""
        theta = math.radians(theta_deg)
        y_B, z_B = self._yb_zb(ship, g, theta_deg, flood)
        return (y_B * math.cos(theta) + (z_B - KG) * math.sin(theta)
                - GG0 * math.sin(theta))

    @staticmethod
    def _deck_edge_angle(ship: ShipData, g: Dict[str, float], t_prime: float) -> float:
        """甲板边（最低限界开口）浸没角：tan(theta) = 2(D - t')/B_wp —— 稳定范围上限。"""
        fb = ship.D - t_prime
        if fb <= 0.0:
            return 0.0
        return math.degrees(math.atan(2.0 * fb / g["B_wp"]))

    def _find_heel(self, ship: ShipData, g: Dict[str, float], KG: float,
                   GG0: float, flood: Dict[str, float],
                   scan: float = 60.0, step: float = 2.0) -> float:
        """F3：横倾 = GZ(theta)=0 的平衡根（取 |theta| 最小者）。"""
        angles = np.arange(-scan, scan + 1e-9, step)
        vals = [(float(a), self._gz(ship, g, float(a), KG, GG0, flood)) for a in angles]
        roots: List[float] = []
        for (a0, v0), (a1, v1) in zip(vals[:-1], vals[1:]):
            if v0 == 0.0:
                roots.append(a0)
            elif v0 * v1 < 0.0:
                lo, hi, flo = a0, a1, v0
                for _ in range(30):
                    mid = 0.5 * (lo + hi)
                    fm = self._gz(ship, g, mid, KG, GG0, flood)
                    if flo * fm <= 0.0:
                        hi = mid
                    else:
                        lo, flo = mid, fm
                roots.append(0.5 * (lo + hi))
        if not roots:
            if not vals:
                return 0.0
            return min(vals, key=lambda av: abs(av[1]))[0]
        return min(roots, key=abs)

    def _gz_curve(self, ship: ShipData, g: Dict[str, float], KG: float, GG0: float,
                  flood: Dict[str, float], heel0: float,
                  cap: float) -> Dict[str, float]:
        """残存 GZ 曲线（heel0 -> 甲板边浸没角 cap）+ GZmax / Range / 面积。"""
        if cap <= heel0:
            return {"gz_max": 0.0, "gz_max_angle": heel0, "range": 0.0, "area": 0.0,
                    "range_end": heel0}
        step = 1.0
        angles = np.arange(heel0, cap + 1e-9, step)
        if angles.size == 0 or angles[-1] < cap - 1e-9:
            angles = np.append(angles, cap)
        gz = [self._gz(ship, g, float(a), KG, GG0, flood) for a in angles]
        arr = np.asarray(gz)
        i_max = int(np.argmax(arr))
        gz_max = float(arr[i_max])
        a_max = float(angles[i_max])
        # 消失角：GZmax 之后首个 <=0 的穿越（二分细化），否则取甲板边浸没角
        a_van = float(cap)
        for i in range(i_max, arr.size - 1):
            if arr[i] > 0.0 >= arr[i + 1]:
                lo, hi, flo = float(angles[i]), float(angles[i + 1]), float(arr[i])
                for _ in range(30):
                    mid = 0.5 * (lo + hi)
                    fm = self._gz(ship, g, mid, KG, GG0, flood)
                    if flo * fm <= 0.0:
                        hi = mid
                    else:
                        lo, flo = mid, fm
                a_van = 0.5 * (lo + hi)
                break
        rng = max(0.0, a_van - heel0)
        area = 0.0
        for i in range(len(angles) - 1):
            area += 0.5 * (gz[i] + gz[i + 1]) * (angles[i + 1] - angles[i]) * math.pi / 180.0
        return {"gz_max": max(0.0, gz_max) if a_max >= heel0 else 0.0,
                "gz_max_angle": a_max, "range": rng, "area": area, "range_end": a_van}

    # ---------- 主分析 ----------
    def analyze(self, input_data: DamageStabilityInput) -> DamageStabilityOutput:
        ship = input_data.ship
        damage = input_data.damage

        self._yb_cache = {}
        g = self._intact_geometry(ship)
        geo_check = self._geometry_check(ship, g)
        flood = self._flooding_state(ship, damage)

        # --- F3: 终吃水（损失浮力平行下沉）与纵倾 ---
        t_prime = self._parallel_sinkage_keel_draft(ship, g, flood)
        trim = self._trim(ship, g, flood, t_prime)

        # KG 保持船自身（损失浮力法：重量不变），自由液面另计
        KG = ship.VCG
        GG0 = flood["i_x_free"] / g["nabla_ws"] if g["nabla_ws"] > 0 else 0.0

        # --- F3: 横倾（GZ 平衡根）---
        heel = self._find_heel(ship, g, KG, GG0, flood)

        # --- GZ 曲线 / GZmax / Range（上限 = 甲板边/限界开口浸没角）---
        cap = self._deck_edge_angle(ship, g, t_prime)
        curve = self._gz_curve(ship, g, KG, GG0, flood, heel, cap)

        # --- F2: Reg.7.2 生存概率 ---
        s = solas2009_s_index(curve["gz_max"], curve["range"])

        # --- 初稳性（损失浮力法：水线面惯性矩扣除破损区）---
        A_loss = flood["A_loss"]
        b_f_local = A_loss / ship.L
        y_f = flood["y_flood"]
        I_T_d = g["I_T0"] - (A_loss * y_f ** 2 + ship.L * b_f_local ** 3 / 12.0)
        BM_d = max(I_T_d, 0.0) / g["nabla_ws"] if g["nabla_ws"] > 0 else 0.0
        KB = ship.T / 2.0
        GM_d = KB + BM_d - KG - GG0

        # --- F5: Reg.7.2 衡准集 ---
        residual_fb = ship.D - t_prime
        criteria: Dict[str, Dict] = {
            "heel_angle": {
                "value": round(abs(heel), 2), "required": 7.0,
                "passed": bool(abs(heel) <= 7.0),
                "source": "SOLAS 2009 Reg.7.2 (K/theta_e <= 7 deg)",
                "description": "破损后平衡横倾角",
            },
            "GZ_max": {
                "value": round(curve["gz_max"], 3), "required": 0.10,
                "passed": bool(curve["gz_max"] >= 0.10),
                "source": "SOLAS 2009 Reg.7.2 (GZmax < 0.10 m -> s = 0)",
                "description": "残存 GZ 最大值",
            },
            "GZ_range": {
                "value": round(curve["range"], 2), "required": 16.0,
                "passed": bool(curve["range"] >= 16.0),
                "source": "SOLAS 2009 Reg.7.2 (Range < 16 deg -> s = 0)",
                "description": "自 theta_e 起残存稳性范围",
            },
            "openings_immersion": {
                "value": round(residual_fb, 3), "required": 0.0,
                "passed": bool(residual_fb > 0.0),
                "source": "SOLAS 2009 Reg.7.2 (最终水线不得淹没限界开口/甲板边)",
                "description": "残存干舷 D - t'（开口浸没代理量）",
            },
            "s_factor": {
                "value": round(s, 4), "required": 0.0,
                "passed": bool(s > 0.0),
                "source": "SOLAS 2009 Reg.7.2",
                "description": "分区生存概率",
            },
            "_diagnostics": {
                "freeboard_legacy": {
                    "value": round(residual_fb, 3), "required": 0.3,
                    "passed": bool(residual_fb > 0.3),
                    "source": "NON-SOLAS 参考项（旧实现的 freeboard>0.3 m，不参与合规判定）",
                },
                "GM_damage": {"value": round(GM_d, 3), "source": "损失浮力法初稳性高（诊断量）"},
                "GG0_free_surface": {"value": round(GG0, 4), "source": "自由液面修正量"},
                "damage_geometry": {
                    "lost_waterplane_area": round(A_loss, 3),
                    "effective_waterplane_area": round(g["Aw"] - A_loss, 3),
                    "flooded_volume": round(flood["volume"], 3),
                    "breach_detail": flood["detail"],
                },
            },
        }
        solas = {k: v for k, v in criteria.items() if not k.startswith("_")}
        imo_compliant = all(bool(v.get("passed")) for v in solas.values())

        compartment_status = []
        for comp in ship.compartments:
            is_damaged = comp.name in damage.damaged_compartments
            flooded = next((d["flooded_volume"] for d in flood["detail"]
                            if d["name"] == comp.name), 0.0)
            compartment_status.append({
                "name": comp.name,
                "status": "damaged" if is_damaged else "intact",
                "flooded_volume": round(flooded, 2) if is_damaged else 0.0,
            })

        return DamageStabilityOutput(
            final_draft=round(t_prime, 3),
            final_trim=round(trim, 3),
            final_heel=round(heel, 2),
            flooded_volume=round(flood["volume"], 2),
            flooded_mass=round(flood["mass"], 2),
            GM_damage=round(GM_d, 3),
            GZ_max=round(curve["gz_max"], 3),
            GZ_range=round(curve["range"], 2),
            GZ_max_angle=round(curve["gz_max_angle"], 2),
            area_GZ=round(curve["area"], 4),
            s_factor=round(s, 4),
            survival_probability=round(s, 4),   # F4：弃用别名，恒等 s
            imo_criteria=criteria,
            imo_compliant=imo_compliant,
            time_to_sink=None,
            compartment_status=compartment_status,
            geometry_check=geo_check,
        )


class ProbabilisticDamageStability:
    """概率破舱稳性（F1 Reg.6 R + F2 Reg.7.2 s）"""

    def __init__(self):
        self.calc = DamageStabilityCalculator()

    def calculate_A(self, input_data: ProbabilisticInput) -> ProbabilisticOutput:
        ship = input_data.ship
        if not input_data.damage_zones:
            raise HTTPException(status_code=422, detail="damage_zones 不能为空")

        A = 0.0
        ignored = False
        contribs: List[Dict] = []
        for i, zone in enumerate(input_data.damage_zones):
            if "survival" in zone:
                ignored = True          # F2：请求体直传 s 一律忽略
            p = float(zone.get("probability", 0.1))

            # 由该区的破损几何重算 s（不允许直传）
            comps = list(zone.get("damaged_compartments") or [])
            if not comps:
                # 未指定舱室 -> 取容积最大的舱（显式记录来源，绝不回声用户 s）
                if not ship.compartments:
                    raise HTTPException(status_code=422,
                                        detail="zone 未给出 damaged_compartments 且 ship 无舱室")
                comps = [max(ship.compartments, key=lambda c: c.volume).name]
                s_source = "assumed_largest_compartment"
            else:
                s_source = "reg7.2_from_GZ"

            scenario = DamageScenario(
                damaged_compartments=comps,
                damage_location=str(zone.get("damage_location", "side")),
                damage_extent=dict(zone.get("damage_extent") or {}),
            )
            res = self.calc.analyze(DamageStabilityInput(ship=ship, damage=scenario))
            s = res.s_factor
            contribution = p * s
            A += contribution
            contribs.append({
                "zone": zone.get("name", f"Zone {i + 1}"),
                "p": round(p, 4),
                "s": round(s, 4),
                "contribution": round(contribution, 4),
                "s_source": s_source,
                "gz_max": res.GZ_max,
                "gz_range": res.GZ_range,
                "heel": res.final_heel,
                "final_draft": res.final_draft,
            })

        Ls = ship.L
        R = solas2009_required_index(Ls, "cargo")
        return ProbabilisticOutput(
            A_value=round(A, 4),
            R_value=round(R, 4),
            compliant=bool(A >= R),
            zone_contributions=contribs,
            Ls=Ls,
            input_s_ignored=ignored,
        )


# ==================== API 路由 ====================

@router.post("/analyze", response_model=DamageStabilityOutput, summary="破舱稳性分析")
async def api_analyze(input_data: DamageStabilityInput):
    """破舱稳性分析：损失浮力法 + SOLAS 2009 Reg.7.2 生存概率 s。"""
    return DamageStabilityCalculator().analyze(input_data)


@router.post("/probabilistic", response_model=ProbabilisticOutput, summary="概率破舱稳性")
async def api_probabilistic(input_data: ProbabilisticInput):
    """概率破舱稳性：R 按 Reg.6，A = sum(p_i * s_i)，s_i 由 GZ 曲线按 Reg.7.2 计算。"""
    return ProbabilisticDamageStability().calculate_A(input_data)


@router.get("/imo-criteria", summary="SOLAS 2009 破舱稳性衡准（Reg.7.2）")
async def get_imo_criteria():
    """F5：衡准集对齐 SOLAS 2009 Reg.7.2（旧 freeboard>0.3 m 已降级为诊断项）。"""
    return {
        "regulation": "SOLAS 2009 Ch.II-1 Part B-1 Reg.6 / Reg.7.2 (MSC.216(82))",
        "criteria": [
            {
                "name": "Heel angle",
                "key": "heel_angle",
                "description": "破损后平衡横倾角",
                "requirement": "theta_e <= 7 deg (Reg.7.2)",
            },
            {
                "name": "Maximum GZ",
                "key": "GZ_max",
                "description": "残存静稳性力臂最大值",
                "requirement": "GZmax >= 0.10 m (否则 s = 0)",
            },
            {
                "name": "Stability range",
                "key": "GZ_range",
                "description": "自 theta_e 起的 GZ 正值范围",
                "requirement": "Range >= 16 deg (否则 s = 0)",
            },
            {
                "name": "Openings immersion",
                "key": "openings_immersion",
                "description": "最终水线不得淹没限界开口",
                "requirement": "残存干舷 D - t' > 0",
            },
            {
                "name": "Survival factor s",
                "key": "s_factor",
                "description": "Reg.7.2 生存概率",
                "requirement": "s > 0，s = min(1,((GZmax/0.12)*(Range/16))**0.25)",
            },
        ],
        "deprecated_non_solas": [
            {
                "name": "freeboard",
                "requirement": "freeboard > 0.3 m",
                "reason": "非 SOLAS 2009 Reg.7 判据，降级为诊断量，不参与合规判定",
            },
            {
                "name": "GM",
                "requirement": "GM > 0.05 m",
                "reason": "非 Reg.7.2 判据；GM 影响 GZmax/Range，已内含于 s",
            },
        ],
        "probabilistic_method": {
            "R": "1 - 128/(Ls+152) for cargo Ls>=80 m; 1 - 152/(Ls+152) for Ls<80 m (Reg.6)",
            "A": "sum(p_i * s_i)",
            "s": "Reg.7.2, computed from the damaged GZ curve; request-body s is ignored",
            "criterion": "A >= R",
        },
    }