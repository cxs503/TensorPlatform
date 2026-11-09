"""IN61-N11 集成测试：契约层 / 物理锚点 / 端到端 / 证据门。

运行：pytest domains/full_performance_pipeline/tests -q
"""
from __future__ import annotations

import math

import pytest

from domains.full_performance_pipeline import references as ref
from domains.full_performance_pipeline.contracts import (ADAPTED, MISSING, NATIVE,
                                                         UNIT_MISMATCH, Contract, Field,
                                                         check_edge, convert, validate_payload)
from domains.full_performance_pipeline.modules_core import (FoulingModule, NavigationModule,
                                                            SpeedLossModule, WindLoadModule,
                                                            RHO_W)
from domains.full_performance_pipeline.modules_ext import (DeckWaterModule,
                                                           PlaceholderModule, SlammingModule,
                                                           TowingModule)
from domains.full_performance_pipeline.pipeline import (EDGES, ORDER, default_modules,
                                                        run_all, run_scenario,
                                                        verify_contracts)
from domains.full_performance_pipeline.synthetic import build_scenarios, scenario

MODS = default_modules()
RUNS = [run_scenario(s, MODS) for s in build_scenarios()]


# ------------------------------------------------------------------ 契约层
def test_field_schema_export():
    f = Field("speed", "m/s", "float", "航速")
    sch = f.as_json_schema()
    assert sch["type"] == "number" and sch["x-unit"] == "m/s"


def test_adapter_whitelist_only():
    assert convert(10.0, "kn", "m/s") == pytest.approx(5.1444444444)
    assert convert([1.0, 2.0], "h", "s") == [3600.0, 7200.0]
    with pytest.raises(KeyError):
        convert(1.0, "kgf", "N")          # 未登记换算必须报错，不许隐式猜


def _producer(unit: str = "m/s", kind: str = "float") -> Contract:
    return Contract("P", "producer", "1", (), (Field("v", unit, kind, "航速"),))


def _consumer(unit: str = "m/s", kind: str = "float") -> Contract:
    return Contract("C", "consumer", "1", (Field("v", unit, kind, "航速"),), ())


def test_edge_native_adapted_mismatch():
    assert check_edge(_producer(), _consumer())["counts"] == {NATIVE: 1}
    r = check_edge(_producer("kn"), _consumer("m/s"))
    assert r["counts"] == {ADAPTED: 1} and r["contract_ok"]
    r = check_edge(_producer("kgf"), _consumer("N"))
    assert r["counts"] == {UNIT_MISMATCH: 1} and not r["contract_ok"]
    r = check_edge(_producer("m/s"), _consumer("m/s"), extra_provided=("other",))
    assert r["counts"] == {NATIVE: 1}


def test_edge_missing_and_renamed():
    c = Contract("C2", "c2", "1", (Field("v_other", "m/s"),), ())
    r = check_edge(_producer(), c)
    assert r["counts"] == {MISSING: 1} and not r["contract_ok"]
    r = check_edge(_producer(), c, mapping={"v_other": "v"})
    assert r["counts"] == {NATIVE: 1} and r["checks"][0]["field"] == "v_other"


def test_payload_validation_detects_nan():
    m = NavigationModule()
    good = m.compute({"lat_dep_deg": 0, "lon_dep_deg": 0, "lat_arr_deg": 0,
                      "lon_arr_deg": 5, "speed_ms": 5})
    assert validate_payload(m.contract, good)["ok"]
    bad = dict(good, eta_h=float("nan"))
    res = validate_payload(m.contract, bad)
    assert not res["ok"] and any("non_finite" in p for p in res["problems"])
    assert not validate_payload(m.contract, {})["ok"]


def test_wiring_no_unresolved_fields():
    """全部算例的配置键都能覆盖契约里的配置注入字段（无未接线字段）。"""
    for scn in build_scenarios():
        res = verify_contracts(MODS, EDGES, list(scn.config_flat().keys()))
        assert res["all_ok"], res["globals"]
        g = res["globals"]
        assert g["missing"] == 0
        assert g["wired_native"] + g["config"] == g["requires"]


# ------------------------------------------------------------------ 模块物理锚点
def test_navigation_analytic_limits():
    nav = NavigationModule()
    assert nav.gc_distance_m(0, 0, 0, 10) == pytest.approx(nav.R_EARTH * math.radians(10), rel=1e-12)
    assert nav.gc_distance_m(0, 0, 0, 180) == pytest.approx(math.pi * nav.R_EARTH, rel=1e-12)
    assert nav.initial_course_deg(0, 0, 0, 10) == pytest.approx(90.0)
    assert nav.rhumb_distance_m(10, 20, 30, 20) == pytest.approx(nav.gc_distance_m(10, 20, 30, 20))


def test_fouling_ittc_lines():
    assert FoulingModule.cf_ittc57(1e9) == pytest.approx(1.531e-3, rel=2e-3)
    # ΔCF 的解析适用下界：ΔCF(ks_min)=0
    lpp = 200.0
    assert FoulingModule.dcf_roughness(FoulingModule.ks_min_valid_m(lpp), lpp) == pytest.approx(0.0, abs=1e-15)
    assert FoulingModule.dcf_roughness(150e-6, lpp) == pytest.approx(
        (105 * (150e-6 / lpp) ** (1 / 3) - 0.64) * 1e-3, rel=1e-12)


def test_spectrum_normalisation_and_scaling():
    hs, tp = 4.0, 9.0
    assert SpeedLossModule.spectrum_m0(hs, tp) == pytest.approx(hs * hs / 16.0, rel=1e-4)
    f = lambda h: SpeedLossModule.added_resistance_wave(h, tp, 30.0, 180.0, 180.0, 0.35)
    assert f(2 * hs) / f(hs) == pytest.approx(4.0, rel=1e-12)
    g = lambda k: SpeedLossModule.added_resistance_wave(hs, tp, 30.0, 180.0, 180.0, k)
    assert g(0.7) / g(0.35) == pytest.approx(2.0, rel=1e-12)
    assert SpeedLossModule.added_resistance_wave(hs, tp, 30.0, 180.0, 90.0, 0.35) == 0.0


def test_power_balance_matches_bisection():
    p, r_add, a, eta = 6.0e6, 3.2e5, 2500.0, 0.62
    v = SpeedLossModule.solve_speed(p, r_add, a, eta)
    assert a * v ** 3 + r_add * v == pytest.approx(p * eta, rel=1e-12)
    assert v > 0


def test_wind_apparent_and_parallel_axis():
    w = WindLoadModule()
    assert w.apparent_wind(10.0, 0.0, 0.0, 0.0) == (pytest.approx(10.0), pytest.approx(0.0))
    vr, psi = w.apparent_wind(10.0, 0.0, 5.0, 0.0)
    assert vr == pytest.approx(15.0) and psi == pytest.approx(0.0)
    vr, psi = w.apparent_wind(8.0, 90.0, 0.0, 0.0)   # 风自正东，船首向北 → 右舷来风
    assert psi == pytest.approx(270.0)
    base = {"wind_speed_ms": 14.0, "wind_from_dir_deg": 20.0, "ship_speed_ms": 6.0,
            "ship_heading_deg": 40.0, "area_front_m2": 900.0, "area_lateral_m2": 3000.0,
            "lpp_m": 180.0, "x_ref_m": 0.0, "z_ref_m": 10.0, "x_centroid_m": 4.0,
            "z_centroid_m": 14.0, "cx_coeff": [-0.7, -0.25, -0.05, 0.0],
            "cy_coeff": [-0.9, -0.15, 0.0], "cn_coeff": [0.15, -0.05, 0.02],
            "ref_shift_m": 1.0}
    out = w.compute(base)
    assert out["yaw_moment_shift_Nm"] - out["yaw_moment_Nm"] == pytest.approx(
        out["wind_y_force_N"], rel=1e-12)      # 平行轴恒等式 Δx=1 m
    assert out["heeling_moment_Nm"] == pytest.approx(4.0 * abs(out["wind_y_force_N"]))


def test_slamming_scaling():
    s = SlammingModule()
    assert s.peak_pressure_pa(2.0, 25.0) / s.peak_pressure_pa(1.0, 25.0) == pytest.approx(4.0)
    assert s.peak_pressure_pa(3.0, 25.0) / s.peak_pressure_pa(3.0, 45.0) == pytest.approx(
        math.tan(math.radians(45.0)) / math.tan(math.radians(25.0)))
    betas = [5, 10, 15, 20, 25, 30, 40]
    pr = [s.peak_pressure_pa(4.0, b) for b in betas]
    assert all(pr[i] > pr[i + 1] for i in range(len(pr) - 1))
    # 阈值速度 → 0 时超越概率应趋于 0.5
    out = s.compute({"wave_height_m": 4.0, "wave_peak_period_s": 9.0, "sigma_v_ratio": 0.55,
                     "design_fractile": 3.0, "threshold_velocity_ms": 0.0,
                     "deadrise_deg": 25.0, "beam_m": 30.0, "ship_speed_ms": 6.0})
    assert out["slamming_probability"] == pytest.approx(0.5)


def test_towing_catenary_roundtrip_and_ode():
    t = TowingModule()
    h, w, length = 6.759e5, 30.0, 400.0
    span = t.catenary_span(length, h, w)
    assert t.arc_length(span, h, w) == pytest.approx(length, rel=1e-12)
    sag = t.catenary_sag(span, h, w)
    # ODE y''=(w/H)sqrt(1+y'^2) 显式 RK4 复算
    n = 4000
    dx = (span / 2.0) / n
    y = yp = 0.0
    f = lambda yy, pp: (pp, (w / h) * math.sqrt(1.0 + pp * pp))
    for _ in range(n):
        k1 = f(y, yp)
        k2 = f(y + 0.5 * dx * k1[0], yp + 0.5 * dx * k1[1])
        k3 = f(y + 0.5 * dx * k2[0], yp + 0.5 * dx * k2[1])
        k4 = f(y + dx * k3[0], yp + dx * k3[1])
        y += dx / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        yp += dx / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    assert sag == pytest.approx(y, rel=1e-5)


def test_deck_water_free_surface_analytic():
    d = DeckWaterModule()
    l, b = 60.0, 24.0
    i = d.free_surface_inertia(l, b)
    # 数值二重积分复核
    nx, ny = 200, 400
    dx, dy = l / nx, b / ny
    num = sum(((-b / 2 + (iy + 0.5) * dy) ** 2) * dy * l for iy in range(ny))
    assert i == pytest.approx(num, rel=1e-4)
    out = d.compute({"deck_length_m": l, "deck_breadth_m": b, "bulwark_height_m": 1.2,
                     "accumulated_volume_m3": 320.0, "lateral_offset_ratio": 0.35,
                     "displacement_t": 40000.0, "kg_m": 9.5, "gm_solid_m": 1.6,
                     "gm_required_m": 0.6, "heel_limit_deg": 5.0})
    assert out["free_surface_correction_m"] == pytest.approx(i / (40000.0 * 1000.0 / RHO_W))
    assert out["ponding_depth_m"] == pytest.approx(320.0 / (l * b))
    # 全宽自由液面使 GM 转负 → 判倾覆（输出 90° 而非伪角度）
    assert out["gm_effective_m"] < 0 and out["equilibrium_heel_deg"] == 90.0
    assert out["heel_ok"] == 0


# ------------------------------------------------------------------ 端到端
def test_e2e_all_scenarios_run_and_converge():
    assert len(RUNS) == len(build_scenarios())
    for r in RUNS:
        assert set(r["outputs"]) == set(ORDER)
        assert r["converged"], r["iterations"][-1]
        assert all(p["ok"] for p in r["payload_checks"].values())
        assert r["contract"]["all_ok"]
        assert r["contract"]["globals"]["missing"] == 0


def test_e2e_latency_under_one_second():
    for r in RUNS:
        assert r["timing"]["e2e_ms"] < 1000.0, r["scenario"]
        assert max(r["timing"]["stage_max_ms"].values()) < 1000.0


def test_e2e_weather_degrades_performance():
    calm = run_scenario(scenario("SYNTH-A_SEA-1_calm"), MODS)
    storm = run_scenario(scenario("SYNTH-A_SEA-2_storm"), MODS)
    assert storm["performance"]["total_resistance_N"] > calm["performance"]["total_resistance_N"]
    assert storm["performance"]["speed_achievable_ms"] < calm["performance"]["speed_achievable_ms"]
    assert storm["performance"]["speed_loss_pct"] > calm["performance"]["speed_loss_pct"]
    # 功率平衡：需求功率应等于可用功率（迭代后）
    for r in RUNS:
        assert abs(r["performance"]["required_power_kw"] / r["outputs"]["N01-speed-loss"]["required_power_kw"] - 1) < 1e-12


def test_e2e_provenance_marks_sources():
    r = RUNS[1]
    prov = r["provenance"]["N01-speed-loss"]
    assert prov["friction_resistance_N"].startswith("N02-fouling")
    assert prov["wind_x_force_N"].startswith("N05-wind-load")
    assert prov["wave_height_m"].startswith("CFG-environment")


def test_residual_identity_of_power_balance():
    for r in RUNS:
        assert abs(r["outputs"]["N01-speed-loss"]["power_balance_residual"]) < 1e-9


# ------------------------------------------------------------------ 证据门
def test_evidence_grades_registered():
    for mid, mod in MODS.items():
        assert set(mod.evidence) == {f.name for f in mod.contract.provides}, mid
        assert mod.module_grade() in ("REAL", "WITHHELD", "STUB", "MOCK")


def test_placeholder_detected_as_mock_and_stub():
    base = MODS["N01-speed-loss"]
    mk = PlaceholderModule(base, "MOCK")
    st = PlaceholderModule(base, "STUB")
    inp = {f.name: 1.0 for f in base.contract.requires}
    for f in base.contract.requires:
        if f.kind == "array":
            inp[f.name] = [0.1, 0.1, 0.1]
    assert mk.module_grade() == "MOCK" and st.module_grade() == "STUB"
    out_mk = mk.compute(inp)
    assert out_mk["total_resistance_N"] == 1.0        # 硬编码，与输入无关
    out_st = st.compute(inp)
    assert out_st["total_resistance_N"] == 0.0
    with pytest.raises(AssertionError):
        PlaceholderModule(base, "REAL")


def test_gate_self_test_flags_poisoned_pipeline():
    from domains.full_performance_pipeline.report import gate_self_test
    g = gate_self_test()
    assert g["mock_fields"] > 0 and g["stub_fields"] > 0
    assert g["n01_grade_poisoned"] == "MOCK"
    assert g["n02_grade_poisoned"] == "STUB"
    assert g["pipeline_still_runs"] is True
    assert g["gate_ok"] is True


# ------------------------------------------------------------------ 验证表
def test_reference_verification_table():
    v = ref.full_verification(RUNS)
    assert v["judged"] >= 40
    assert v["failed_3pct"] == 0, [r["item"] for r in v["items"] if r["within_3pct"] is False]
    assert v["max_rel_error"] < 0.03
    # 缺基准项必须显式声明，不许静默
    assert len(v["spread"]) >= 4
    assert all(s["within_3pct"] is False for s in v["spread"])


def test_report_json_contract(tmp_path):
    from domains.full_performance_pipeline.report import build_report
    rep = build_report(out_dir=str(tmp_path), write_doc=False)
    assert rep["all_criteria_pass"] is True
    for key in ("J1_end_to_end_link", "J2_latency", "J3_contract", "J4_evidence_report"):
        assert rep["judgement"][key]["PASS"] is True
    assert rep["judgement"]["J2_latency"]["worst_e2e_ms"] < 1000.0
    assert rep["judgement"]["J3_contract"]["missing_or_mismatch"] == 0
    assert len(rep["figures"]) >= 5
    import json
    import os
    path = os.path.join(str(tmp_path), "integration_report.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["node"] == "IN61-N11"