"""七域模块实现（下）：砰击(N08)/拖曳(N09)/甲板积水(N10) + 证据门自检用的占位模块。

占位模块 `PlaceholderModule` 不参与正式流程，只用于**证明证据门能识别 MOCK/STUB**
（客户端回归：把任一真实模块包成 MOCK 后，报告的证据统计必须掉级）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from .contracts import MOCK, REAL, STUB, WITHHELD, Contract, Field
from .modules_core import G, Module, RHO_W


# =====================================================================  N08 砰击
class SlammingModule(Module):
    """砰击：von Karman/Wagner 附加质量率冲击压力 + 高斯超越概率。"""

    contract = Contract(
        module_id="N08-slamming",
        title="砰击：底部冲击压力与发生概率",
        version="1.0.0",
        requires=(
            Field("deadrise_deg", "deg", "float", "首部升角"),
            Field("beam_m", "m", "float", "型宽（冲击弦长）"),
            Field("wave_height_m", "m", "float", "有义波高"),
            Field("wave_peak_period_s", "s", "float", "谱峰周期"),
            Field("ship_speed_ms", "m/s", "float", "航速"),
            Field("sigma_v_ratio", "1", "float", "σ_v/(Hs·ω_p) 幅值比（配置注入）"),
            Field("design_fractile", "1", "float", "设计冲击分位 k（v_entry=k·σ_v）"),
            Field("threshold_velocity_ms", "m/s", "float", "砰击阈值速度（规则/配置注入）"),
        ),
        provides=(
            Field("peak_pressure_kpa", "kPa", "float", "峰值冲击压力"),
            Field("peak_force_per_m_N_per_m", "N/m", "float", "单位长度峰值冲击力"),
            Field("slamming_probability", "1", "float", "单周期超越概率"),
            Field("encounters_per_hour", "1/h", "float", "每小时砰击次数"),
            Field("relative_velocity_ms", "m/s", "float", "相对速度标准差 σ_v"),
            Field("entry_velocity_ms", "m/s", "float", "设计入水速度 k·σ_v"),
        ),
    )
    evidence = {
        "peak_pressure_kpa": (WITHHELD,
                              "附加质量率模型 p=ρπ²V²/(2tanβ)（c(t) 线性增长、峰值取 c=B/2）；"
                              "峰值系数缺试验标定；V² 标度与升角单调性可精确校验"),
        "peak_force_per_m_N_per_m": (WITHHELD, "同上（p·B）"),
        "slamming_probability": (WITHHELD, "相对速度取高斯，σ_v 由配置幅值比给出（未标定）；阈值速度取配置值"),
        "encounters_per_hour": (WITHHELD, "同上 × 遭遇周期"),
        "relative_velocity_ms": (REAL, "σ_v=ratio·Hs·ω_p 定义式"),
        "entry_velocity_ms": (REAL, "设计入水速度 k·σ_v 定义式"),
    }

    @staticmethod
    def peak_pressure_pa(v: float, deadrise_deg: float) -> float:
        """附加质量率推导：m_a=ρπc²，ċ=(π/2)V/tanβ，峰值时刻 c=B/2

        → F'=2ρπcċV=ρπ²V²B/(2tanβ)，p=F'/B=ρπ²V²/(2tanβ)。
        """
        beta = math.radians(min(89.0, max(1.0, deadrise_deg)))
        return RHO_W * math.pi ** 2 * v * v / (2.0 * math.tan(beta))

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        hs = float(inputs["wave_height_m"])
        tp = float(inputs["wave_peak_period_s"])
        wp = 2.0 * math.pi / max(tp, 1e-6)
        sigma_v = max(1e-9, float(inputs["sigma_v_ratio"]) * hs * wp)
        v_entry = float(inputs["design_fractile"]) * sigma_v
        p = self.peak_pressure_pa(v_entry, float(inputs["deadrise_deg"]))
        v_th = float(inputs["threshold_velocity_ms"])
        prob = 0.5 * math.erfc(v_th / sigma_v / math.sqrt(2.0))   # 单周期超越概率
        tep = 2.0 * math.pi / wp
        return {
            "peak_pressure_kpa": p / 1000.0,
            "peak_force_per_m_N_per_m": p * float(inputs["beam_m"]),
            "slamming_probability": prob,
            "encounters_per_hour": prob / tep * 3600.0,
            "relative_velocity_ms": sigma_v,
            "entry_velocity_ms": v_entry,
        }


# =====================================================================  N09 拖曳
class TowingModule(Module):
    """拖曳：被拖阻力 → 悬链线顶端张力 → 拖轮系柱拉力利用率。

    悬链线取解析解（等高端点），由弧长—垂度关系反解跨距；对 ODE 积分与浅垂度
    极限 H≈wS²/(8h) 分别校验。
    """

    contract = Contract(
        module_id="N09-towing",
        title="拖曳：悬链线张力与系柱拉力利用率",
        version="1.0.0",
        requires=(
            Field("total_resistance_N", "N", "float", "被拖船总阻力（来自 N01）"),
            Field("towline_length_m", "m", "float", "拖缆长度"),
            Field("towline_wet_weight_N_per_m", "N/m", "float", "拖缆水下单位长度重量"),
            Field("bollard_pull_kN", "kN", "float", "拖轮系柱拉力"),
            Field("safety_factor", "1", "float", "许用安全系数"),
        ),
        provides=(
            Field("required_tow_force_N", "N", "float", "所需拖力"),
            Field("tow_force_margin_N", "N", "float", "设计拖力（含裕度）"),
            Field("horizontal_tension_N", "N", "float", "底端水平张力 H"),
            Field("top_tension_N", "N", "float", "顶端总张力"),
            Field("span_m", "m", "float", "等高端点跨距"),
            Field("sag_m", "m", "float", "垂度"),
            Field("bollard_utilization", "1", "float", "系柱拉力利用率"),
            Field("tow_ok", "int", "int", "1=满足安全系数"),
        ),
    )
    evidence = {
        "required_tow_force_N": (REAL, "静力平衡 H=R 定义式"),
        "tow_force_margin_N": (REAL, "1.1 倍设计裕度（显式常量）"),
        "horizontal_tension_N": (REAL, "静力平衡；对悬链线 ODE 积分校验"),
        "top_tension_N": (REAL, "T=√(H²+(wL/2)²) 解析；对 ODE 校验"),
        "span_m": (REAL, "弧长-垂度关系反解，正解回代校验 <1e-9"),
        "sag_m": (REAL, "h=(H/w)(cosh(wS/2H)−1) 解析"),
        "bollard_utilization": (REAL, "T/BP 比值定义式"),
        "tow_ok": (REAL, "判据 BP/SF ≥ T 布尔可验"),
    }

    MARGIN = 1.1

    @staticmethod
    def catenary_span(length: float, h_tension: float, w: float) -> float:
        """弧长 L 与水平张力 H → 等高端点跨距 S：L/2=(H/w)·sinh(wS/2H)。"""
        if w <= 0 or length <= 0:
            return length
        a = h_tension / w
        return 2.0 * a * math.asinh(length / (2.0 * a))

    @staticmethod
    def catenary_sag(span: float, h_tension: float, w: float) -> float:
        if w <= 0:
            return 0.0
        return (h_tension / w) * (math.cosh(w * span / (2.0 * h_tension)) - 1.0)

    @staticmethod
    def arc_length(span: float, h_tension: float, w: float) -> float:
        if w <= 0:
            return span
        a = h_tension / w
        return 2.0 * a * math.sinh(span / (2.0 * a))

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        r = max(float(inputs["total_resistance_N"]), 1.0)
        w = float(inputs["towline_wet_weight_N_per_m"])
        length = float(inputs["towline_length_m"])
        s = self.catenary_span(length, r, w)
        sag = self.catenary_sag(s, r, w)
        top = math.hypot(r, w * length / 2.0)
        bp = float(inputs["bollard_pull_kN"]) * 1000.0
        return {
            "required_tow_force_N": r,
            "tow_force_margin_N": self.MARGIN * r,
            "horizontal_tension_N": r,
            "top_tension_N": top,
            "span_m": s,
            "sag_m": sag,
            "bollard_utilization": top / max(bp, 1.0),
            "tow_ok": int(bp / max(float(inputs["safety_factor"]), 1e-9) >= top),
        }


# =====================================================================  N10 甲板积水
class DeckWaterModule(Module):
    """甲板积水：积水量/深度 → 自由液面修正 → 有效 GM → 静平衡横倾角。"""

    contract = Contract(
        module_id="N10-deck-water",
        title="甲板积水：自由液面修正与稳性裕度",
        version="1.0.0",
        requires=(
            Field("deck_length_m", "m", "float", "积水区长度 l"),
            Field("deck_breadth_m", "m", "float", "积水区宽度 b"),
            Field("bulwark_height_m", "m", "float", "舷墙高度（积水上限）"),
            Field("accumulated_volume_m3", "m3", "float", "积水体积"),
            Field("lateral_offset_ratio", "1", "float", "积水形心横向偏移/半宽 0..1"),
            Field("displacement_t", "t", "float", "排水量"),
            Field("kg_m", "m", "float", "重心高度 KG"),
            Field("gm_solid_m", "m", "float", "固体重量 GM"),
            Field("gm_required_m", "m", "float", "稳性要求 GM（规则注入值）"),
            Field("heel_limit_deg", "deg", "float", "许用横倾角"),
        ),
        provides=(
            Field("water_mass_t", "t", "float", "积水质量"),
            Field("ponding_depth_m", "m", "float", "积水深度"),
            Field("overflow_flag", "int", "int", "1=积水超舷墙（溢流）"),
            Field("free_surface_correction_m", "m", "float", "自由液面修正 ΔGM"),
            Field("gm_effective_m", "m", "float", "有效 GM"),
            Field("kg_effective_m", "m", "float", "计入自由液面后等价 KG"),
            Field("equilibrium_heel_deg", "deg", "float", "静平衡横倾角"),
            Field("stability_margin_m", "m", "float", "GM 裕度"),
            Field("heel_ok", "int", "int", "1=横倾在许用内"),
        ),
    )
    evidence = {
        "water_mass_t": (REAL, "m=ρV 定义式"),
        "ponding_depth_m": (REAL, "V/(l·b) 几何式；对二重积分体积校验"),
        "overflow_flag": (REAL, "深度 > 舷墙高度 布尔判据"),
        "free_surface_correction_m": (REAL, "ΔGM=ρ_w·i/(ρ·Δ)，i=lb³/12；对数值二重积分校验 <1e-9"),
        "gm_effective_m": (REAL, "GM_f=GM_solid−ΔGM 代数和"),
        "kg_effective_m": (REAL, "KG_eff=KG+ΔGM"),
        "equilibrium_heel_deg": (REAL, "tanθ=ρ_w V y_off/(Δ·GM_f) 静平衡解；小角解析校验"),
        "stability_margin_m": (REAL, "GM_f−GM_req 差值"),
        "heel_ok": (REAL, "θ ≤ θ_limit 布尔判据"),
    }

    @staticmethod
    def free_surface_inertia(length: float, breadth: float) -> float:
        """矩形积水区自由液面惯性矩 i=l·b³/12（m⁴）。"""
        return length * breadth ** 3 / 12.0

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        l = float(inputs["deck_length_m"])
        b = float(inputs["deck_breadth_m"])
        v = max(0.0, float(inputs["accumulated_volume_m3"]))
        disp_m3 = float(inputs["displacement_t"]) * 1000.0 / RHO_W
        depth = v / (l * b)
        i_fs = self.free_surface_inertia(l, b)
        dgm = RHO_W * i_fs / (RHO_W * disp_m3)
        gm_f = float(inputs["gm_solid_m"]) - dgm
        y_off = float(inputs["lateral_offset_ratio"]) * b / 2.0
        if gm_f <= 1e-6:
            heel = 90.0      # GM≤0：无静平衡恢复力矩，判为倾覆极限（不给出伪角度）
        else:
            heel = math.degrees(math.atan(RHO_W * v * y_off / (RHO_W * disp_m3 * gm_f)))
        return {
            "water_mass_t": RHO_W * v / 1000.0,
            "ponding_depth_m": depth,
            "overflow_flag": int(depth > float(inputs["bulwark_height_m"])),
            "free_surface_correction_m": dgm,
            "gm_effective_m": gm_f,
            "kg_effective_m": float(inputs["kg_m"]) + dgm,
            "equilibrium_heel_deg": heel,
            "stability_margin_m": gm_f - float(inputs["gm_required_m"]),
            "heel_ok": int(heel <= float(inputs["heel_limit_deg"])),
        }


# ============================================================ 证据门自检占位模块
class PlaceholderModule(Module):
    """把任一真实模块包成 MOCK（硬编码常数）或 STUB（全零/空）——仅供证据门自检。

    目的：证明“证据等级分类器 + 门禁”不是空转，而是真的能把占位实现降级。
    """

    def __init__(self, base: Module, grade: str = MOCK, frozen: Dict[str, Any] | None = None):
        assert grade in (MOCK, STUB), "占位等级只允许 MOCK/STUB"
        self.base = base
        self.contract = base.contract
        self._grade = grade
        self._frozen = frozen or {}
        self.evidence = {f.name: (grade, f"占位实现（{grade}）：不执行真实计算/硬编码输出")
                         for f in base.contract.provides}

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in self.contract.provides:
            if f.name in self._frozen:
                out[f.name] = self._frozen[f.name]
            elif self._grade == MOCK:
                out[f.name] = 1.0 if f.kind in ("float", "int") else []
            else:  # STUB
                out[f.name] = 0.0 if f.kind in ("float", "int") else []
        return out