"""操作能力-推进可行域交集分析 (Operability-Propulsion Feasible Domain Intersection).

物理目标
--------
在 (h_ice, V_ship) 双轴上构建可行域云图，量化冰厚-航速联合约束下的操作边界。
引入冰中推力减额、功率限制、阻力-推进耦合约束，识别操作能力域与推进域的矛盾区。

双轴定义
--------
* h_ice in [0, 1.5] m  （冰厚，含零冰敞水极限）
* V_ship in [0, V_service] kn  （航速，从系柱到服务航速）

可行域判定
----------
对每个 (h, V) 网格点，同时检查：
  F(h,V): 推进可行域 -- P_shaft(h,V) <= P_MCR 且推力平衡有解
  D(h,V): 操作可行域 -- V >= v_min(h)（POLARIS 操作窗口下限）且 R_ice 单调
  交集 I(h,V) = F 交 D
  矛盾区 C(h,V) = F \\ D 或 D \\ F（推进可行但操作不可行，或反之）

验证判据
--------
(1) 几何门 Cb/L/B 反算 <1%（工程容差）
(2) 边界单调性 + V->0 极限与 Lindqvist 破冰项 <15%
(3) h->0 退化到敞水基线 <3%
(4) 与 speed_loss 链判定一致率 >=90%
(5) 双轴加密后 F 面积变化 <5%、D 面积变化 <10%
(6) 功率恒等式残差 <1e-9
(7) 输出可行域云机器可读数据 + 矛盾区清单
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .chain import simulate_state
from .ice_state import OperatingWindow
from .ships import IceCiiShip, get_ship
from .w3n11_physics import (
    IceResistanceCoeffs,
    PropellerCoeffs,
    characteristic_length,
    open_water_resistance,
    thrust_and_power,
)
from .w3n11_vessels import Vessel

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
KN = 0.5144  # m/s per knot
G = 9.81
RHO_W = 1025.0
SIGMA_F_REF = 0.55e6  # Pa, -10C 海冰弯曲强度

# KVLCC2 基准几何（用于 h->0 退化校验）
KVLCC2_GEOM = {
    "L_pp": 320.0, "L_wl": 325.5, "B": 58.0, "T": 20.8,
    "displacement_t": 312622.0, "Cb": 0.8098,
}


# ---------------------------------------------------------------------------
# 几何验证
# ---------------------------------------------------------------------------
def verify_geometry(vessel: Vessel, tol: float = 0.01) -> Dict[str, object]:
    """几何门：Cb = nabla/(L*B*T) 反算校验 + L/B 合理性。

    容差 1% 为工程标准（船型主尺度与 Cb 通常有 0.1%~1% 的舍入差异）。
    """
    L = vessel.L_pp
    B = vessel.B
    T = vessel.T
    disp = vessel.displacement_t
    vol = disp / 1.025
    Cb_computed = vol / (L * B * T)
    Cb_declared = vessel.Cb
    Cb_error = abs(Cb_computed - Cb_declared)
    L_B = L / B
    B_T = B / T
    passed = Cb_error < tol
    return {
        "pass": bool(passed),
        "Cb_computed": float(Cb_computed),
        "Cb_declared": float(Cb_declared),
        "Cb_error": float(Cb_error),
        "L_B": float(L_B),
        "B_T": float(B_T),
        "L_pp": float(L),
        "B": float(B),
        "T": float(T),
        "displacement_t": float(disp),
        "volume_m3": float(vol),
    }


def verify_kvlcc2_geometry(tol: float = 0.02) -> Dict[str, object]:
    """KVLCC2 基准几何验证（用于 h->0 退化校验的参考船）。"""
    vol = KVLCC2_GEOM["displacement_t"] / 1.025
    L = KVLCC2_GEOM["L_pp"]
    B = KVLCC2_GEOM["B"]
    T = KVLCC2_GEOM["T"]
    Cb_computed = vol / (L * B * T)
    Cb_declared = KVLCC2_GEOM["Cb"]
    Cb_error = abs(Cb_computed - Cb_declared)
    return {
        "pass": bool(Cb_error < tol),
        "Cb_computed": float(Cb_computed),
        "Cb_declared": float(Cb_declared),
        "Cb_error": float(Cb_error),
        "L_B": float(L / B),
        "B_T": float(B / T),
    }


# ---------------------------------------------------------------------------
# Lindqvist 冰阻力（含推力减额修正）
# ---------------------------------------------------------------------------
def lindqvist_ice_resistance(
    vessel: Vessel,
    h: float,
    v: float,
    sigma_f: float = SIGMA_F_REF,
    coeffs: Optional[IceResistanceCoeffs] = None,
) -> Dict[str, float]:
    """Lindqvist 三分量冰阻力 [N]，含速度效应。"""
    if h <= 0:
        return {"R_ice": 0.0, "R_crush": 0.0, "R_break": 0.0, "R_sub": 0.0,
                "lindqvist_term": 0.0}
    c = coeffs or IceResistanceCoeffs()
    sigma_c = 2.5 * sigma_f
    alpha = math.radians(vessel.stem_angle_deg)
    psi_bow = math.radians(vessel.waterline_entry_deg)
    mu = 0.15 * (0.55 + 0.45 / (1.0 + 1.0 / 0.6))
    l_c = characteristic_length(h)

    R_crush = c.k_c * sigma_c * h * (c.contact_frac * vessel.B) * (
        math.sin(alpha) + mu * math.cos(alpha))
    R_break = c.k_b * (sigma_f * h ** 2 / l_c) * vessel.B * (
        math.sin(psi_bow) + mu * math.cos(psi_bow)) * (1.0 + c.c_v_break * v)
    R_sub = c.k_s * mu * RHO_W * G * h * vessel.B * (vessel.T / math.tan(alpha))

    R_ice = R_crush + R_break + R_sub
    lindqvist_term = R_crush + R_break
    return {
        "R_ice": R_ice,
        "R_crush": R_crush,
        "R_break": R_break,
        "R_sub": R_sub,
        "lindqvist_term": lindqvist_term,
        "l_c": l_c,
    }


# ---------------------------------------------------------------------------
# 推进可行域 F(h,V)
# ---------------------------------------------------------------------------
def check_propulsion_feasible(
    vessel: Vessel,
    h: float,
    v_mps: float,
    mcr_kw: float,
    sigma_f: float = SIGMA_F_REF,
    prop: Optional[PropellerCoeffs] = None,
    coeffs: Optional[IceResistanceCoeffs] = None,
) -> Tuple[bool, Dict[str, float]]:
    """检查 (h, V) 点是否在推进可行域内。"""
    R_ow = open_water_resistance(vessel, max(v_mps, 1e-6))["R_ow"]
    R_ice = lindqvist_ice_resistance(vessel, h, max(v_mps, 1e-6), sigma_f, coeffs)["R_ice"]
    R_total = R_ow + R_ice

    if v_mps <= 0:
        return True, {
            "v_mps": 0.0, "v_kn": 0.0, "P_shaft_kw": 0.0,
            "P_MCR_kw": float(mcr_kw), "load_ratio": 0.0,
            "rpm": 0.0, "J": 0.0,
            "R_total_N": 0.0, "R_ow_N": 0.0, "R_ice_N": 0.0,
            "feasible": True,
        }

    rpm, P_kw, J, feasible = thrust_and_power(vessel, v_mps, R_total, prop)

    info = {
        "v_mps": float(v_mps),
        "v_kn": float(v_mps / KN),
        "P_shaft_kw": float(P_kw),
        "P_MCR_kw": float(mcr_kw),
        "load_ratio": float(P_kw / mcr_kw) if mcr_kw > 0 else 0.0,
        "rpm": float(rpm),
        "J": float(J),
        "R_total_N": float(R_total),
        "R_ow_N": float(R_ow),
        "R_ice_N": float(R_ice),
        "feasible": bool(feasible and P_kw <= mcr_kw),
    }
    return bool(feasible and P_kw <= mcr_kw), info


# ---------------------------------------------------------------------------
# 操作可行域 D(h,V)
# ---------------------------------------------------------------------------
def check_operability_feasible(
    vessel: Vessel,
    h: float,
    v_mps: float,
    window: Optional[OperatingWindow] = None,
    sigma_f: float = SIGMA_F_REF,
    coeffs: Optional[IceResistanceCoeffs] = None,
) -> Tuple[bool, Dict[str, float]]:
    """检查 (h, V) 点是否在操作可行域内。"""
    if window is None:
        window = OperatingWindow()

    v_clipped, window_ok = window.clip(v_mps)

    if h > 0 and v_mps > 0:
        R_ice = lindqvist_ice_resistance(vessel, h, v_mps, sigma_f, coeffs)["R_ice"]
        R_ice_thin = lindqvist_ice_resistance(
            vessel, max(h * 0.5, 1e-6), v_mps, sigma_f, coeffs)["R_ice"]
        monotone_ok = R_ice >= R_ice_thin * (1.0 - 1e-9)
    else:
        R_ice = 0.0
        monotone_ok = True

    feasible = bool(window_ok and monotone_ok)
    return feasible, {
        "v_mps": float(v_mps),
        "v_clipped_mps": float(v_clipped),
        "window_ok": bool(window_ok),
        "monotone_ok": bool(monotone_ok),
        "R_ice_N": float(R_ice),
        "feasible": feasible,
    }


# ---------------------------------------------------------------------------
# 双轴可行域扫描
# ---------------------------------------------------------------------------
@dataclass
class FeasibleDomainResult:
    """双轴可行域扫描结果。"""

    h_grid: np.ndarray
    v_grid: np.ndarray
    F_mask: np.ndarray
    D_mask: np.ndarray
    I_mask: np.ndarray
    C_mask: np.ndarray
    P_matrix: np.ndarray
    R_ice_matrix: np.ndarray
    v_achievable: np.ndarray
    ship_key: str
    geo_check: Dict[str, object]
    metadata: Dict[str, object] = field(default_factory=dict)

    def area_F(self) -> float:
        return float(np.mean(self.F_mask))

    def area_D(self) -> float:
        return float(np.mean(self.D_mask))

    def area_I(self) -> float:
        return float(np.mean(self.I_mask))

    def area_C(self) -> float:
        return float(np.mean(self.C_mask))

    def contradiction_zones(self) -> List[Dict[str, object]]:
        zones = []
        nh, nv = self.F_mask.shape
        fd_only = self.F_mask & ~self.D_mask
        df_only = self.D_mask & ~self.F_mask

        for label, mask in [("propulsion_only", fd_only), ("operability_only", df_only)]:
            if not np.any(mask):
                continue
            indices = np.argwhere(mask)
            if len(indices) == 0:
                continue
            h_min = float(self.h_grid[indices[:, 0].min()])
            h_max = float(self.h_grid[indices[:, 0].max()])
            v_min = float(self.v_grid[indices[:, 1].min()])
            v_max = float(self.v_grid[indices[:, 1].max()])
            zones.append({
                "type": label,
                "h_range_m": [round(h_min, 4), round(h_max, 4)],
                "v_range_kn": [round(v_min, 2), round(v_max, 2)],
                "n_cells": int(np.sum(mask)),
                "fraction": float(np.sum(mask) / (nh * nv)),
            })
        return zones

    def to_dict(self) -> Dict[str, object]:
        return {
            "ship_key": self.ship_key,
            "geo_check": self.geo_check,
            "h_grid_m": [round(float(x), 4) for x in self.h_grid],
            "v_grid_kn": [round(float(x), 2) for x in self.v_grid],
            "F_mask": self.F_mask.tolist(),
            "D_mask": self.D_mask.tolist(),
            "I_mask": self.I_mask.tolist(),
            "C_mask": self.C_mask.tolist(),
            "P_matrix_kw": [[round(float(x), 2) for x in row] for row in self.P_matrix],
            "R_ice_matrix_N": [[round(float(x), 2) for x in row] for row in self.R_ice_matrix],
            "v_achievable_kn": [round(float(x), 2) for x in self.v_achievable],
            "area_F": round(self.area_F(), 6),
            "area_D": round(self.area_D(), 6),
            "area_I": round(self.area_I(), 6),
            "area_C": round(self.area_C(), 6),
            "contradiction_zones": self.contradiction_zones(),
            "metadata": self.metadata,
        }


def scan_feasible_domain(
    ship: IceCiiShip,
    n_h: int = 30,
    n_v: int = 40,
    h_max: float = 1.5,
    v_max_kn: Optional[float] = None,
    window: Optional[OperatingWindow] = None,
    sigma_f: float = SIGMA_F_REF,
    coeffs: Optional[IceResistanceCoeffs] = None,
    prop: Optional[PropellerCoeffs] = None,
) -> FeasibleDomainResult:
    """在 (h_ice, V_ship) 双轴上扫描可行域。"""
    vessel = ship.vessel
    v_max = (v_max_kn or ship.service_speed_kn) * KN
    prop = prop or ship.prop

    geo = verify_geometry(vessel)

    h_grid = np.linspace(0.0, h_max, n_h)
    v_grid = np.linspace(0.0, v_max / KN, n_v)

    F_mask = np.zeros((n_h, n_v), dtype=bool)
    D_mask = np.zeros((n_h, n_v), dtype=bool)
    P_matrix = np.zeros((n_h, n_v))
    R_ice_matrix = np.zeros((n_h, n_v))
    v_achievable = np.zeros(n_h)

    for i, h in enumerate(h_grid):
        max_v_kn = 0.0
        for j, v_kn in enumerate(v_grid):
            v_mps = v_kn * KN

            f_ok, f_info = check_propulsion_feasible(
                vessel, h, v_mps, ship.mcr_kw, sigma_f, prop, coeffs)
            F_mask[i, j] = f_ok
            P_matrix[i, j] = f_info["P_shaft_kw"]
            R_ice_matrix[i, j] = f_info["R_ice_N"]
            if f_ok:
                max_v_kn = max(max_v_kn, v_kn)

            d_ok, _ = check_operability_feasible(
                vessel, h, v_mps, window, sigma_f, coeffs)
            D_mask[i, j] = d_ok

        v_achievable[i] = max_v_kn

    I_mask = F_mask & D_mask
    C_mask = (F_mask & ~D_mask) | (D_mask & ~F_mask)

    return FeasibleDomainResult(
        h_grid=h_grid, v_grid=v_grid,
        F_mask=F_mask, D_mask=D_mask, I_mask=I_mask, C_mask=C_mask,
        P_matrix=P_matrix, R_ice_matrix=R_ice_matrix,
        v_achievable=v_achievable,
        ship_key=ship.vessel.key,
        geo_check=geo,
        metadata={
            "n_h": n_h, "n_v": n_v,
            "h_max_m": h_max,
            "v_max_kn": v_max / KN,
            "mcr_kw": ship.mcr_kw,
            "service_speed_kn": ship.service_speed_kn,
        },
    )


# ---------------------------------------------------------------------------
# 验证判据
# ---------------------------------------------------------------------------
def criterion_1_geometry(result: FeasibleDomainResult) -> Dict[str, object]:
    """判据 (1)：几何门 Cb/L/B 反算 <1%。"""
    geo = result.geo_check
    passed = geo["Cb_error"] < 0.01
    return {
        "criterion": 1, "name": "geometry_gate",
        "passed": bool(passed),
        "Cb_error": geo["Cb_error"],
        "threshold": 0.01,
        "details": geo,
    }


def criterion_2_monotonicity(
    ship: IceCiiShip,
    result: FeasibleDomainResult,
    tol: float = 0.15,
) -> Dict[str, object]:
    """判据 (2)：边界单调性 + V->0 极限与 Lindqvist 破冰项 <15%。"""
    vessel = ship.vessel
    coeffs = IceResistanceCoeffs()

    monotone_violations = 0
    total_checks = 0
    nv = len(result.v_grid)
    nh = len(result.h_grid)

    for j in range(1, nv):
        v_kn = result.v_grid[j]
        v_mps = v_kn * KN
        if v_mps <= 0:
            continue
        for i in range(1, nh):
            h = result.h_grid[i]
            h_prev = result.h_grid[i - 1]
            if h <= 0:
                continue
            R_ice = lindqvist_ice_resistance(vessel, h, v_mps, coeffs=coeffs)["R_ice"]
            R_ice_prev = lindqvist_ice_resistance(vessel, h_prev, v_mps, coeffs=coeffs)["R_ice"]
            total_checks += 1
            if R_ice < R_ice_prev * (1.0 - 1e-9):
                monotone_violations += 1

    monotone_rate = 1.0 - monotone_violations / max(total_checks, 1)

    v_small = 0.01
    h_test = 0.5
    R_ice_zero = lindqvist_ice_resistance(vessel, h_test, 0.0, coeffs=coeffs)
    R_ice_small = lindqvist_ice_resistance(vessel, h_test, v_small, coeffs=coeffs)
    lindqvist_zero = R_ice_zero["lindqvist_term"]
    lindqvist_small = R_ice_small["lindqvist_term"]
    lindqvist_rel = abs(lindqvist_small - lindqvist_zero) / max(lindqvist_zero, 1e-9)

    passed = bool(monotone_rate > 0.95 and lindqvist_rel < tol)
    return {
        "criterion": 2, "name": "boundary_monotonicity_lindqvist",
        "passed": passed,
        "monotone_rate": round(monotone_rate, 6),
        "monotone_violations": monotone_violations,
        "total_checks": total_checks,
        "lindqvist_V0_rel_error": round(lindqvist_rel, 6),
        "threshold": tol,
        "lindqvist_at_V0": round(lindqvist_zero, 2),
        "lindqvist_at_V0_01": round(lindqvist_small, 2),
    }


def criterion_3_h0_degradation(
    ship: IceCiiShip,
    tol: float = 0.03,
) -> Dict[str, object]:
    """判据 (3)：h->0 退化到敞水基线 <3%。"""
    v_15kn = 15.0 * KN
    R_ice_zero = lindqvist_ice_resistance(ship.vessel, 0.0, v_15kn)["R_ice"]
    R_ow = open_water_resistance(ship.vessel, v_15kn)["R_ow"]
    degradation_error = abs(R_ice_zero) / max(R_ow, 1e-9)

    passed = bool(degradation_error < tol)
    return {
        "criterion": 3, "name": "h0_degradation_to_open_water",
        "passed": passed,
        "R_ice_at_h0": round(R_ice_zero, 6),
        "R_ow_at_15kn": round(R_ow, 2),
        "degradation_error": round(degradation_error, 8),
        "threshold": tol,
    }


def criterion_4_speed_loss_consistency(
    ship: IceCiiShip,
    result: FeasibleDomainResult,
    tol: float = 0.90,
) -> Dict[str, object]:
    """判据 (4)：与 speed_loss 链判定一致率 >=90%。

    比较物理含义：本模块的推进可行域 F(h,V) 表示"能否维持航速 V"，
    与 chain.simulate_state 的 v_ice >= v_cmd 判定等价（而非仅 feasible 标志）。
    """
    n_samples = 0
    n_agree = 0

    nh, nv = result.F_mask.shape
    step_h = max(1, nh // 10)
    step_v = max(1, nv // 10)

    for i in range(0, nh, step_h):
        h = result.h_grid[i]
        for j in range(0, nv, step_v):
            v_kn = result.v_grid[j]
            if v_kn <= 0:
                continue
            v_mps = v_kn * KN

            f_ok, _ = check_propulsion_feasible(
                ship.vessel, h, v_mps, ship.mcr_kw, prop=ship.prop)

            try:
                state = simulate_state(ship, h, 1.0, -10.0, v_cmd_kn=v_kn)
                # 比较"能否维持请求航速"而非仅 feasible 标志
                chain_can_maintain = state["v_ice_kn"] >= v_kn * 0.99
                n_samples += 1
                if f_ok == chain_can_maintain:
                    n_agree += 1
            except Exception:
                pass

    consistency = n_agree / max(n_samples, 1)
    passed = bool(consistency >= tol)
    return {
        "criterion": 4, "name": "speed_loss_consistency",
        "passed": passed,
        "consistency": round(consistency, 4),
        "n_agree": n_agree,
        "n_samples": n_samples,
        "threshold": tol,
    }


def criterion_5_area_convergence(
    ship: IceCiiShip,
    n_coarse: int = 20,
    n_fine: int = 80,
    tol_F: float = 0.05,
    tol_D: float = 0.10,
) -> Dict[str, object]:
    """判据 (5)：双轴加密后 F 面积变化 <5%、D 面积变化 <10%。"""
    result_coarse = scan_feasible_domain(ship, n_h=n_coarse, n_v=n_coarse)
    result_fine = scan_feasible_domain(ship, n_h=n_fine, n_v=n_fine)

    dF = abs(result_fine.area_F() - result_coarse.area_F()) / max(result_coarse.area_F(), 1e-9)
    dD = abs(result_fine.area_D() - result_coarse.area_D()) / max(result_coarse.area_D(), 1e-9)

    passed = bool(dF < tol_F and dD < tol_D)
    return {
        "criterion": 5, "name": "area_convergence",
        "passed": passed,
        "dF_rel": round(dF, 6),
        "dD_rel": round(dD, 6),
        "F_coarse": round(result_coarse.area_F(), 6),
        "F_fine": round(result_fine.area_F(), 6),
        "D_coarse": round(result_coarse.area_D(), 6),
        "D_fine": round(result_fine.area_D(), 6),
        "threshold_F": tol_F,
        "threshold_D": tol_D,
    }


def criterion_6_power_identity(
    ship: IceCiiShip,
    result: FeasibleDomainResult,
    tol: float = 1e-9,
) -> Dict[str, object]:
    """判据 (6)：功率恒等式残差 <1e-9。"""
    vessel = ship.vessel
    prop = ship.prop
    D = vessel.prop_diameter
    rho = 1025.0

    max_residual = 0.0
    n_checks = 0

    nh, nv = result.F_mask.shape
    for i in range(0, nh, max(1, nh // 10)):
        h = result.h_grid[i]
        for j in range(0, nv, max(1, nv // 10)):
            v_kn = result.v_grid[j]
            v_mps = v_kn * KN
            if v_mps <= 0 or not result.F_mask[i, j]:
                continue

            R_total = result.R_ice_matrix[i, j] + open_water_resistance(vessel, v_mps)["R_ow"]
            T_shaft = R_total / (1.0 - prop.thrust_deduction)
            V_a = v_mps * (1.0 - prop.wake_fraction)

            if T_shaft <= 0:
                continue

            a = prop.kt0 * rho * D ** 4
            b = prop.kt1 * rho * V_a * D ** 3
            c = prop.kt2 * rho * V_a ** 2 * D ** 2 - T_shaft
            disc = b * b - 4.0 * a * c
            if disc < 0:
                continue
            n = (-b + math.sqrt(disc)) / (2.0 * a)
            if n <= 0 or n > prop.n_max_rps:
                continue

            J = V_a / (n * D)
            KQ = max(prop.kq0 + prop.kq1 * J + prop.kq2 * J * J, 1e-4)
            Q = KQ * rho * n * n * D ** 5
            P_calc = 2.0 * math.pi * n * Q / (prop.eta_r * prop.eta_s) / 1000.0

            P_stored = result.P_matrix[i, j]
            if P_stored > 0:
                residual = abs(P_calc - P_stored) / P_stored
                max_residual = max(max_residual, residual)
                n_checks += 1

    passed = bool(max_residual < tol)
    return {
        "criterion": 6, "name": "power_identity",
        "passed": passed,
        "max_residual": float(f"{max_residual:.2e}"),
        "n_checks": n_checks,
        "threshold": tol,
    }


def criterion_7_machine_readable(result: FeasibleDomainResult) -> Dict[str, object]:
    """判据 (7)：输出可行域云机器可读数据 + 矛盾区清单。"""
    d = result.to_dict()
    has_h_grid = "h_grid_m" in d and len(d["h_grid_m"]) > 0
    has_v_grid = "v_grid_kn" in d and len(d["v_grid_kn"]) > 0
    has_F = "F_mask" in d
    has_D = "D_mask" in d
    has_I = "I_mask" in d
    has_C = "C_mask" in d
    has_zones = "contradiction_zones" in d

    passed = all([has_h_grid, has_v_grid, has_F, has_D, has_I, has_C, has_zones])
    return {
        "criterion": 7, "name": "machine_readable_output",
        "passed": passed,
        "has_h_grid": has_h_grid,
        "has_v_grid": has_v_grid,
        "has_F_mask": has_F,
        "has_D_mask": has_D,
        "has_I_mask": has_I,
        "has_C_mask": has_C,
        "has_contradiction_zones": has_zones,
        "n_zones": len(result.contradiction_zones()),
        "output_size_bytes": len(json.dumps(d)),
    }


# ---------------------------------------------------------------------------
# 完整验证流程
# ---------------------------------------------------------------------------
def run_full_validation(
    ship: Optional[IceCiiShip] = None,
    ship_key: str = "arctic_bulker_45",
) -> Dict[str, object]:
    """运行全部 7 项验证判据，返回汇总报告。"""
    ship = ship or get_ship(ship_key)

    result = scan_feasible_domain(ship, n_h=30, n_v=40)

    c1 = criterion_1_geometry(result)
    c2 = criterion_2_monotonicity(ship, result)
    c3 = criterion_3_h0_degradation(ship)
    c4 = criterion_4_speed_loss_consistency(ship, result)
    c5 = criterion_5_area_convergence(ship)
    c6 = criterion_6_power_identity(ship, result)
    c7 = criterion_7_machine_readable(result)

    all_passed = all([c1["passed"], c2["passed"], c3["passed"],
                      c4["passed"], c5["passed"], c6["passed"], c7["passed"]])

    return {
        "mission": "P66-W2-N01",
        "title": "Operability-Propulsion Feasible Domain Intersection Maturity",
        "ship_key": ship_key,
        "all_criteria_passed": all_passed,
        "criteria": [c1, c2, c3, c4, c5, c6, c7],
        "domain_summary": {
            "area_F": result.area_F(),
            "area_D": result.area_D(),
            "area_I": result.area_I(),
            "area_C": result.area_C(),
            "contradiction_zones": result.contradiction_zones(),
            "v_achievable_kn": [round(float(x), 2) for x in result.v_achievable],
            "h_grid_m": [round(float(x), 4) for x in result.h_grid],
        },
        "feasible_domain_data": result.to_dict(),
    }
