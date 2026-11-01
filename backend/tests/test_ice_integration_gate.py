"""W8-N08 跨域集成门 pytest (契约/数据流/守恒/对标).

运行: cd backend && pytest tests/test_ice_integration_gate.py -q
全量端到端流水线只跑一次 (session 级 fixture), 后续用例复用其结果。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from services.ice_integration import nodes_downstream as nd
from services.ice_integration import reference_nodes as rn
from services.ice_integration.contracts import (
    CONTRACTS,
    EDGES,
    Edge,
    validate_contracts,
)
from services.ice_integration.dimensions import DimensionError, convert, conversion_factor
from services.ice_integration.gate import count_tests, evaluate_criteria, check_report_sections
from services.ice_integration.pipeline import run_pipeline, validate_payload
from services.ice_integration.probe import probe_all

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


@pytest.fixture(scope="session")
def pipeline_result():
    return run_pipeline()


@pytest.fixture(scope="session")
def probe_result():
    return probe_all()


# ---------------------------------------------------------------------------
# (b) 契约: 正向 0 错误 + 四类负向必须能判负
# ---------------------------------------------------------------------------
def test_contract_registry_has_zero_violations():
    rep = validate_contracts()
    assert rep.ok, rep.as_dict()["violations"]
    assert rep.as_dict()["error_count"] == 0
    assert rep.edges_checked == len(EDGES)
    assert rep.nodes == sorted(CONTRACTS)


def test_negative_dimension_mismatch_is_detected():
    bad = list(EDGES) + [Edge("N02", "wall_impact_pressure", "N07", "measured_moment")]
    rep = validate_contracts(edges=bad)
    codes = {v.code for v in rep.violations}
    assert "DIMENSION_MISMATCH" in codes


def test_negative_frame_mismatch_is_detected():
    bad = list(EDGES) + [
        Edge("N02", "wall_impact_pressure", "N03", "excitation_moment_x", transform="identity")
    ]
    rep = validate_contracts(edges=bad)
    assert any(v.code in ("FRAME_MISMATCH", "DIMENSION_MISMATCH") for v in rep.violations)


def test_negative_algebraic_loop_is_detected():
    bad = [Edge(e.src_node, e.src_port, e.dst_node, e.dst_port, e.transform, delayed=False)
           if e.delayed else e for e in EDGES]
    rep = validate_contracts(edges=bad)
    assert any(v.code == "ALGEBRAIC_LOOP" for v in rep.violations)


def test_negative_dangling_output_is_detected():
    bad = [e for e in EDGES if not (e.src_node == "N01" and e.src_port == "melt_mass_rate")]
    rep = validate_contracts(edges=bad)
    assert any(v.code == "UNFED_INPUT" for v in rep.violations)


def test_feedback_edge_is_explicitly_delayed():
    rep = validate_contracts()
    assert rep.delays == ["N02.slosh_moment_x->N03.slosh_moment_x"]
    assert rep.loops  # 存在闭环, 且已由 delayed 边化解


def test_unit_conversion_roundtrip_and_incompatible_units_rejected():
    assert abs(conversion_factor("N*m", "kN*m") - 1e-3) < 1e-15
    assert abs(convert(convert(40e6, "N*m", "kN*m"), "kN*m", "N*m") - 40e6) < 1e-6
    with pytest.raises(DimensionError):
        convert(1.0, "Pa", "N*m")


# ---------------------------------------------------------------------------
# 各节点物理量 vs 解析/参考 (证据: REAL)
# ---------------------------------------------------------------------------
def test_sloshing_natural_frequencies_match_analytic():
    res = rn.run_node02()
    modes = res.meta["modal"]["modes"]
    assert len(modes) >= 3
    for m in modes:
        assert m["rel_error"] < 0.03, m
        assert m["mode_shape_correlation"] > 0.99
    # 深水捷径必须被判负 (有限水深, kh=0.78)
    assert modes[0]["deep_water_rel_error"] > 0.03


def test_stefan_film_thickness_matches_analytic():
    res = rn.run_node01()
    assert res.meta["stefan_relative_error"] < 0.01
    assert 1e-6 < res.payload["melt_film_thickness"] < 0.05


def test_clapeyron_melting_point_depression():
    slope = rn.clapeyron_slope()
    assert -1e-7 < slope < -5e-8          # 冰-水反常: 负斜率
    assert rn.melting_point(2.0e6) < 273.15
    assert abs(rn.melting_point(0.0) - 273.15) < 1e-12


def test_gz_curve_matches_wall_sided_analytic_and_imo():
    curve = nd.gz_curve(46.0, 12.0, 18.0, list(np.arange(0.0, 61.0, 2.5)))
    # 解析式仅在甲板边缘入水前有效 (phi_deck = atan(2T/B) ≈ 27.6°), 窗口内要求 <1%
    assert curve["max_relative_error"] < 0.01
    assert curve["analytic_validity_limit_deg"] > 20.0
    assert curve["max_relative_error_full_range"] > curve["max_relative_error"]
    assert abs(curve["gz_numeric"][0]) < 1e-9
    imo = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    assert set(imo["checks"]) >= {"area_0_30_m_rad", "gm0_m", "gz_max_m"}
    assert isinstance(imo["passed"], bool)


def test_two_dof_degenerate_limit_and_eigen_consistency():
    I, C, l = 4.6e10, 1.26e10, 29.0
    assert nd.degenerate_residual(I, C, 1e-9, l) < 1e-9
    eig = nd.two_dof_eigen(I, C, 3.384e6, l)
    assert eig["det_residual"] < 1e-9
    assert eig["omega_low"] < eig["omega_high"]


def test_sea_power_balance_and_reciprocity():
    n = [10.0, 2.0, 50.0]
    res = nd.sea_solve(1000.0, n, [0.01, 0.01, 0.01],
                       [{1: 0.02, 2: 0.001}, {0: 0.02}, {}], [1000.0, 0.0, 0.0])
    assert res["residual"] < 1e-10
    assert res["reciprocity_residual"] < 1e-12
    assert all(e >= 0 for e in res["energies"])


def test_sea_energy_relation_matches_random_vibration():
    chk = nd.verify_sea_energy_relation()
    assert chk["relative_error"] < 0.03


def test_plate_modal_density_matches_mode_count():
    side, h = 25.29, 0.02
    n_f = nd.plate_modal_density(side * side, h)
    count = nd.count_plate_modes(side, side, h, 500.0)
    assert abs(count / (n_f * 500.0) - 1.0) < 0.05


def test_beverloo_discharge_monotone_and_drain_balance():
    q_small = nd.beverloo_discharge(0.30, 0.06)
    q_big = nd.beverloo_discharge(0.60, 0.06)
    assert 0 < q_small < q_big
    assert nd.beverloo_discharge(0.06, 0.06) == 0.0
    from services.ice_integration.pipeline import drain_mass_audit
    audit = drain_mass_audit(0.60, 0.06, 0.54, 0.60)
    assert audit["residual"] < 0.01


def test_frazil_viscosity_limits_and_monotonicity():
    eta0 = nd.kd_viscosity(0.0)
    assert abs(eta0 - 1.8e-3) < 1e-12
    etas = [nd.kd_viscosity(p) for p in (0.05, 0.10, 0.20, 0.30, 0.38)]
    assert all(b > a for a, b in zip(etas, etas[1:]))
    assert nd.kd_viscosity(0.39) > 10 * eta0


def test_ekf_tracks_truth_and_is_consistent():
    t = np.arange(0.0, 60.0, 0.01)
    phi = 0.01 * np.sin(1.2 * t)
    phid = 0.012 * np.cos(1.2 * t)
    moment = np.zeros_like(t)
    res = nd.run_node07(t, phi, phid, moment)
    assert res.meta["rmse_rad"] < 0.01
    assert res.meta["coverage_3sigma"] > 0.85
    assert 0.5 < res.meta["mean_norm_innovation_sq"] < 6.0


# ---------------------------------------------------------------------------
# (c) 数据流贯通 / (d) 守恒
# ---------------------------------------------------------------------------
def test_end_to_end_payloads_match_contract(pipeline_result):
    assert pipeline_result["payload_checks"] == []
    raw = pipeline_result["raw_payloads"]
    for node in ("N01", "N02", "N03", "N04", "N05", "N06", "N07"):
        assert raw[node], node
        assert validate_payload(node, raw[node]) == []


def test_payload_validator_catches_unit_scale_error(pipeline_result):
    payload = dict(pipeline_result["raw_payloads"]["N01"])
    payload["contact_force"] = 10.0  # 把 MN 当 N 写
    codes = {p["code"] for p in validate_payload("N01", payload)}
    assert "OUT_OF_RANGE" in codes


def test_conservation_residuals_below_one_percent(pipeline_result):
    res = pipeline_result["conservation"]["residuals"]
    assert set(res) >= {"mass_melt", "mass_ice_transfer", "momentum_surge",
                        "energy_roll_slosh", "energy_sea"}
    assert max(res.values()) < 0.01, res


def test_staggered_cosimulation_agrees_with_monolithic(pipeline_result):
    delta = pipeline_result["coupling"]["relative_delta"]
    assert delta < 0.10, delta
    assert pipeline_result["unit_invariance"]["roundtrip_relative_error"] < 1e-12


def test_chain_order_is_physically_consistent(pipeline_result):
    n = pipeline_result["nodes"]
    # 冰载荷 -> 横摇 -> 液晃反作用 -> 孪生 -> 冰况 -> 流变
    assert n["N01"]["payload"]["contact_moment_x"] > 0
    assert n["N03"]["payload"]["roll_angle"] > 0
    assert n["N02"]["payload"]["slosh_moment_x"] > 0
    assert 0.0 <= n["N05"]["payload"]["cooling_availability"] <= 1.0
    assert 0.0 <= n["N06"]["payload"]["thrust_loss_fraction"] <= 1.0
    assert 0.0 <= n["N07"]["payload"]["estimated_ice_concentration"] <= 1.0


# ---------------------------------------------------------------------------
# (e)(f) 战役级门: 判据裁决 + 报告六要素 + 集成测试数量
# ---------------------------------------------------------------------------
def test_gate_criteria_pass(pipeline_result, probe_result):
    test_file = Path(__file__).resolve()
    n_tests = count_tests(test_file)
    docs = REPO / "docs" / "ice_integration_gate.md"
    report = check_report_sections(docs if docs.exists() else None)
    crit = evaluate_criteria(pipeline_result, probe_result, report, test_file, n_tests)
    failed = [k for k, v in crit.items() if not v["passed"]]
    assert n_tests >= 10
    assert not failed, {k: crit[k] for k in failed}
    assert crit["b_dimension_consistency"]["error_count"] == 0
    assert crit["d_conservation"]["max_residual"] < 0.01


def test_upstream_probe_records_honest_status(probe_result):
    nodes = probe_result["nodes"]
    assert set(nodes) == {"N01", "N02", "N03", "N04", "N05", "N06", "N07"}
    for node, row in nodes.items():
        assert row["status"] in ("MERGED", "UNMERGED_ARTIFACT", "UNMERGED_EMPTY", "ABSENT")
        assert isinstance(row["dirty_files"], int)
    json.dumps(probe_result)  # 可序列化 (工件落地前提)