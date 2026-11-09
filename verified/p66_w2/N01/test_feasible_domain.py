"""可行域交集分析测试：7 项验证判据全覆盖。"""

import json
import numpy as np
import pytest

from modules.green_navigation_ice.feasible_domain import (
    FeasibleDomainResult,
    check_operability_feasible,
    check_propulsion_feasible,
    criterion_1_geometry,
    criterion_2_monotonicity,
    criterion_3_h0_degradation,
    criterion_4_speed_loss_consistency,
    criterion_5_area_convergence,
    criterion_6_power_identity,
    criterion_7_machine_readable,
    lindqvist_ice_resistance,
    run_full_validation,
    scan_feasible_domain,
    verify_geometry,
    verify_kvlcc2_geometry,
)
from modules.green_navigation_ice.ice_state import OperatingWindow
from modules.green_navigation_ice.ships import get_ship
from modules.green_navigation_ice.w3n11_physics import (
    IceResistanceCoeffs,
    open_water_resistance,
)


# ---------------------------------------------------------------------------
# 几何验证
# ---------------------------------------------------------------------------
def test_geometry_gate_arctic_bulker():
    """判据 (1)：arctic_bulker_45 几何门通过（Cb 误差 <1%）。"""
    ship = get_ship("arctic_bulker_45")
    geo = verify_geometry(ship.vessel)
    assert geo["pass"] is True
    assert geo["Cb_error"] < 0.01


def test_geometry_gate_kvlcc2():
    """KVLCC2 基准几何验证。"""
    geo = verify_kvlcc2_geometry()
    assert geo["pass"] is True
    assert geo["Cb_error"] < 0.02


# ---------------------------------------------------------------------------
# Lindqvist 冰阻力
# ---------------------------------------------------------------------------
def test_lindqvist_zero_ice():
    """h=0 时冰阻力严格为零。"""
    ship = get_ship("arctic_bulker_45")
    res = lindqvist_ice_resistance(ship.vessel, 0.0, 5.0)
    assert res["R_ice"] == 0.0
    assert res["lindqvist_term"] == 0.0


def test_lindqvist_monotone_in_h():
    """冰阻力对冰厚单调增。"""
    ship = get_ship("arctic_bulker_45")
    hs = np.linspace(0.1, 1.0, 10)
    R_ices = []
    for h in hs:
        R_ices.append(lindqvist_ice_resistance(ship.vessel, h, 3.0)["R_ice"])
    R_ices = np.array(R_ices)
    assert np.all(np.diff(R_ices) > 0)


def test_lindqvist_V0_limit():
    """V→0 时 Lindqvist 项与 V=0 极限偏差 <15%。"""
    ship = get_ship("arctic_bulker_45")
    h = 0.5
    R_zero = lindqvist_ice_resistance(ship.vessel, h, 0.0)
    R_small = lindqvist_ice_resistance(ship.vessel, h, 0.01)
    rel = abs(R_small["lindqvist_term"] - R_zero["lindqvist_term"]) / max(
        R_zero["lindqvist_term"], 1e-9)
    assert rel < 0.15


# ---------------------------------------------------------------------------
# 推进可行域
# ---------------------------------------------------------------------------
def test_propulsion_feasible_open_water():
    """敞水 (h=0) 在服务航速下推进可行。"""
    ship = get_ship("arctic_bulker_45")
    v_mps = ship.service_speed_kn * 0.5144
    ok, info = check_propulsion_feasible(ship.vessel, 0.0, v_mps, ship.mcr_kw)
    assert ok is True
    assert info["P_shaft_kw"] < ship.mcr_kw


def test_propulsion_infeasible_thick_ice():
    """厚冰 + 高速下推进不可行。"""
    ship = get_ship("arctic_bulker_45")
    v_mps = 8.0  # ~15.5 kn
    ok, info = check_propulsion_feasible(ship.vessel, 1.2, v_mps, ship.mcr_kw)
    assert info["P_shaft_kw"] > 0


def test_propulsion_zero_speed():
    """V=0 时推进可行（系柱工况）。"""
    ship = get_ship("arctic_bulker_45")
    ok, info = check_propulsion_feasible(ship.vessel, 0.5, 0.0, ship.mcr_kw)
    assert ok is True
    assert info["P_shaft_kw"] == 0.0
    assert info["R_ice_N"] == 0.0


# ---------------------------------------------------------------------------
# 操作可行域
# ---------------------------------------------------------------------------
def test_operability_window_clip():
    """POLARIS 窗口夹紧。"""
    ship = get_ship("arctic_bulker_45")
    window = OperatingWindow(v_min_mps=0.5144, v_max_mps=8.0)
    ok, info = check_operability_feasible(ship.vessel, 0.5, 0.3, window)
    assert ok is False  # 低于 v_min


def test_operability_monotone():
    """操作可行域冰阻力单调性。"""
    ship = get_ship("arctic_bulker_45")
    ok, info = check_operability_feasible(ship.vessel, 0.5, 3.0)
    assert info["monotone_ok"] is True


# ---------------------------------------------------------------------------
# 可行域扫描
# ---------------------------------------------------------------------------
def test_scan_feasible_domain_shape():
    """扫描结果形状正确。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=20, n_v=25)
    assert result.F_mask.shape == (20, 25)
    assert result.D_mask.shape == (20, 25)
    assert result.I_mask.shape == (20, 25)
    assert result.C_mask.shape == (20, 25)
    assert result.P_matrix.shape == (20, 25)
    assert len(result.v_achievable) == 20


def test_scan_h0_all_feasible():
    """h=0 行全部推进可行（敞水无冰阻力）。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=10, n_v=20)
    assert np.all(result.F_mask[0, :])


def test_scan_v0_all_feasible():
    """V=0 列全部可行（系柱工况）。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=10, n_v=20)
    assert np.all(result.F_mask[:, 0])


def test_scan_achievable_decreases_with_h():
    """可达航速随冰厚单调减。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=20, n_v=30)
    v = result.v_achievable
    assert np.all(np.diff(v) <= 1e-9)


# ---------------------------------------------------------------------------
# 判据验证
# ---------------------------------------------------------------------------
def test_criterion_1():
    """判据 (1) 几何门。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=10, n_v=10)
    c = criterion_1_geometry(result)
    assert c["passed"] is True
    assert c["Cb_error"] < 0.01


def test_criterion_2():
    """判据 (2) 边界单调性 + Lindqvist。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=15, n_v=15)
    c = criterion_2_monotonicity(ship, result)
    assert c["passed"] is True
    assert c["monotone_rate"] > 0.95
    assert c["lindqvist_V0_rel_error"] < 0.15


def test_criterion_3():
    """判据 (3) h→0 退化。"""
    ship = get_ship("arctic_bulker_45")
    c = criterion_3_h0_degradation(ship)
    assert c["passed"] is True
    assert c["degradation_error"] < 0.03


def test_criterion_4():
    """判据 (4) speed_loss 一致性。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=15, n_v=15)
    c = criterion_4_speed_loss_consistency(ship, result)
    assert c["passed"] is True
    assert c["consistency"] >= 0.90


def test_criterion_5():
    """判据 (5) 面积收敛。"""
    ship = get_ship("arctic_bulker_45")
    c = criterion_5_area_convergence(ship, n_coarse=20, n_fine=80)
    assert c["passed"] is True
    assert c["dF_rel"] < 0.05
    assert c["dD_rel"] < 0.10


def test_criterion_6():
    """判据 (6) 功率恒等式。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=15, n_v=15)
    c = criterion_6_power_identity(ship, result)
    assert c["passed"] is True
    assert float(c["max_residual"]) < 1e-9


def test_criterion_7():
    """判据 (7) 机器可读输出。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=10, n_v=10)
    c = criterion_7_machine_readable(result)
    assert c["passed"] is True
    assert c["has_h_grid"] is True
    assert c["has_contradiction_zones"] is True


# ---------------------------------------------------------------------------
# 完整验证
# ---------------------------------------------------------------------------
def test_full_validation():
    """完整验证流程全部通过。"""
    report = run_full_validation()
    assert report["all_criteria_passed"] is True
    assert len(report["criteria"]) == 7
    for c in report["criteria"]:
        assert c["passed"] is True, f"Criterion {c['criterion']} failed: {c['name']}"


def test_full_validation_json_serializable():
    """完整验证结果可 JSON 序列化。"""
    report = run_full_validation()
    json_str = json.dumps(report, indent=2)
    assert len(json_str) > 1000


def test_contradiction_zones():
    """矛盾区清单格式正确。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=30, n_v=40, h_max=1.5)
    zones = result.contradiction_zones()
    assert isinstance(zones, list)
    for z in zones:
        assert "type" in z
        assert "h_range_m" in z
        assert "v_range_kn" in z
        assert "n_cells" in z
        assert "fraction" in z


def test_to_dict_roundtrip():
    """to_dict 输出可反序列化。"""
    ship = get_ship("arctic_bulker_45")
    result = scan_feasible_domain(ship, n_h=10, n_v=10)
    d = result.to_dict()
    json_str = json.dumps(d)
    d2 = json.loads(json_str)
    assert d2["ship_key"] == d["ship_key"]
    assert len(d2["h_grid_m"]) == 10
    assert len(d2["v_grid_kn"]) == 10
