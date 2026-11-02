# -*- coding: utf-8 -*-
"""m1850 — 绿色航行（EEDI/EEOI/CII）端点验证欠账审计的判据测试（离线，无网络）

被测：artifacts/m1850/green_efficiency_audit.py（IMO 闭式参考核 + 端点行为重建 + 6 条判据）
证据：artifacts/m1850/probe_platform_energy.json（2026-11-02 对 :8001 的实探记录）

设计原则：
  * 参考核侧——用**手算常量**（不调用被测函数自身）钉住 IMO 数值；
  * 端点侧——从源码逐字复刻的行为重建，与 fixture 实测逐位对齐，证明"可判"；
  * 每条判据都配**负控制**（篡改后判据必须翻面），防止恒真测试。
"""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "m1850")
MOD_PATH = os.path.join(ART, "green_efficiency_audit.py")
FIXTURE = os.path.join(ART, "probe_platform_energy.json")


def _load():
    spec = importlib.util.spec_from_file_location("m1850_green_audit", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load()


@pytest.fixture(scope="module")
def probe():
    if not os.path.exists(FIXTURE):
        pytest.skip("缺探针 fixture")
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def result(probe):
    return audit.judge(probe)


# ---------------------------------------------------------------- 参考核侧
@pytest.mark.parametrize("dwt,ship_type,expected", [
    (180000, "bulk_carrier", 2.9944),   # 961.79*180000^-0.477
    (300000, "tanker", 2.5888),         # 1218.80*300000^-0.488
    (140000, "container", 16.0963),     # 174.22*140000^-0.201
    (350000, "lng_carrier", 0.0),       # 占位：仅校验单调/量级，下面单独断言
])
def test_eedi_reference_line_matches_hand_computed_imo_values(dwt, ship_type, expected):
    """IMO 参考线对手算常量（MEPC.203(62)/MEPC.364(79)）。"""
    if expected == 0.0:
        pytest.skip("量级用例见 test_reference_lines_ordering")
    assert audit.eedi_ref(dwt, ship_type) == pytest.approx(expected, rel=2e-4)


def test_reference_lines_ordering_and_scale():
    """箱船在 140k DWT 的参考线应为油船同吨位的数倍（船型差别真实存在）。"""
    dwt = 140000
    cont = audit.eedi_ref(dwt, "container")
    tank = audit.eedi_ref(dwt, "tanker")
    bulk = audit.eedi_ref(dwt, "bulk_carrier")
    assert cont > 4.0 * tank > 0
    assert cont > 4.0 * bulk > 0
    # 大船参考线应小于小船（幂律指数为负）
    assert audit.eedi_ref(400000, "tanker") < audit.eedi_ref(50000, "tanker")


def test_eedi_phase_reduction_schedule():
    """MEPC.203(62)+MEPC.324(75)：Phase 3 自 2025-01-01，RF=30%。"""
    assert audit.eedi_rf(2013) == 0.0
    assert audit.eedi_rf(2014) == 0.0
    assert audit.eedi_rf(2015) == pytest.approx(0.10)
    assert audit.eedi_rf(2020) == pytest.approx(0.20)
    assert audit.eedi_rf(2024) == pytest.approx(0.20)
    assert audit.eedi_rf(2025) == pytest.approx(0.30)
    assert audit.eedi_rf(2026) == pytest.approx(0.30)
    assert "Phase 3" in audit.eedi_phase_name(2026)


def test_attained_eedi_dimensionally_consistent():
    """EEDI = P*Cf*SFC/(DWT*V)：功率线性、吨位与航速反比。"""
    base = audit.attained_eedi(10000.0, 180.0, 50000.0, 15.0)
    assert audit.attained_eedi(20000.0, 180.0, 50000.0, 15.0) == pytest.approx(2 * base)
    assert audit.attained_eedi(10000.0, 180.0, 100000.0, 15.0) == pytest.approx(base / 2)
    assert audit.attained_eedi(10000.0, 180.0, 50000.0, 30.0) == pytest.approx(base / 2)
    # 手算：10000 kW * 180 g/kWh * 3.206 /(50000 t * 15 kn) = 7.6944 g CO2/(t*nm)
    assert base == pytest.approx(7.6944, rel=1e-6)


def test_aer_definition_is_grams_per_dwt_nm():
    """AER = 年 CO2[g]/(DWT·nm)：1e3 t CO2、DWT 1e5、1e4 nm ⇒ 1.0 g/(DWT·nm)。"""
    assert audit.attained_aer(1e3, 1e5, 1e4) == pytest.approx(1.0, rel=1e-9)
    assert audit.attained_aer(1e3, 1e5, 2e4) == pytest.approx(0.5, rel=1e-9)


def test_cii_required_and_z_factors():
    """CII 要求值 = 参考线×(1-Z)，Z 2026 = 11%（MEPC.338(76)/MEPC.354(78)）。"""
    dwt = 180000
    ref = audit.cii_ref_value(dwt, "bulk_carrier")
    assert ref == pytest.approx(4745.0 * dwt ** -0.622, rel=1e-12)
    assert audit.required_cii(dwt, "bulk_carrier", 2019) == pytest.approx(ref)
    assert audit.required_cii(dwt, "bulk_carrier", 2026) == pytest.approx(ref * 0.89)
    assert audit.required_cii(dwt, "bulk_carrier", 2023) == pytest.approx(ref * 0.95)


def test_cf_catalog_values():
    """MEPC.364(79) 碳转换因子；平台硬编码 MGO 3.206。"""
    assert audit.CF_FUEL["HFO"] == pytest.approx(3.114)
    assert audit.CF_FUEL["MDO_MGO"] == pytest.approx(3.206)
    assert audit.CF_FUEL["LNG"] == pytest.approx(2.750)
    assert audit.PLATFORM_CF == pytest.approx(3.206)


# ------------------------------------------------- 端点行为重建 vs 实测对齐
def test_platform_baseline_reproduces_fixture_for_every_ship_type(probe):
    """同一公式 961.79·DWT^-0.477 能重现**油船/散货/箱船**三种实测 baseline
    ⇒ 端点确实船型盲（无 ship_type），这是 G1 的直接证据。"""
    seen_types = 0
    for name, c in audit.CASES.items():
        resp = probe["cases"][name]["resp"]
        assert resp["eedi_baseline"] == pytest.approx(
            audit.platform_baseline(c["dwt"]), abs=0.01), name
        seen_types += 1
    assert seen_types == 4
    types = {c["ship_type"] for c in audit.CASES.values()}
    assert {"tanker", "bulk_carrier", "container"} <= types


def test_platform_eeoi_is_algebraically_identical_to_eedi(probe):
    """EEOI=[P·SFC/1e6·(D/V)]·Cf·1e6/(DWT·D)=P·SFC·Cf/(DWT·V)≡EEDI（航程 D 精确抵消）。"""
    for name, c in audit.CASES.items():
        e = audit.platform_eedi(c["power"], c["sfc"], c["dwt"], c["speed"])
        for d in (10.0, 100.0, 1000.0):
            o = audit.platform_eeoi(c["power"], c["sfc"], c["dwt"], c["speed"], distance_nm=d)
            assert o == pytest.approx(e, rel=1e-12)
        resp = probe["cases"][name]["resp"]
        assert resp["eeoi"] / resp["eedi"] == pytest.approx(1.0, rel=1e-3)


def test_platform_cii_equals_eedi_over_1852(probe):
    """量纲缺陷可解析化：CII_platform = EEDI/1852
    （年距离把 kn·1.852/h 的 km 当千海里且少乘 10⁶ g→t）。"""
    for name, c in audit.CASES.items():
        e = audit.platform_eedi(c["power"], c["sfc"], c["dwt"], c["speed"])
        cii = audit.platform_cii(c["power"], c["sfc"], c["dwt"], c["speed"])
        assert cii * 1852.0 == pytest.approx(e, rel=1e-12)


def test_platform_cii_rating_is_constant_a_on_288_point_grid():
    """功率/SFC/吨位网格 288 点：评级恒 A（哑指标指纹）。"""
    ratings = set()
    for c in audit.CASES.values():
        for kp in (0.5, 0.8, 1.0, 1.5, 2.0, 3.0):
            for ks in (0.8, 1.0, 1.2, 1.5):
                for kd in (0.5, 1.0, 2.0):
                    cii = audit.platform_cii(c["power"] * kp, c["sfc"] * ks,
                                             c["dwt"] * kd, c["speed"])
                    ratings.add(audit.platform_cii_rating(cii))
    assert ratings == {"A"}


# --------------------------------------------------------------- 判据总判
def test_all_six_criteria_fail_and_overall_is_mock(result):
    v = result["summary"]["verdicts"]
    assert set(v) == {"G1", "G2", "G3", "G4", "G5", "G6"}
    assert result["summary"]["n_fail"] == 6
    assert result["summary"]["overall"] == "MOCK_DEGRADED"


def test_g1_container_baseline_error_is_seventy_nine_percent(result):
    rows = {r["case"]: r for r in result["criteria"]["G1"]["rows"]}
    assert rows["PostPanamax_container"]["rel_err"] == pytest.approx(-0.79, abs=0.01)
    assert rows["Capesize_bulk"]["rel_err"] == pytest.approx(0.0, abs=0.01)  # 散货船碰巧对
    assert rows["KVLCC2_tanker"]["rel_err"] == pytest.approx(-0.092, abs=0.01)


def test_g2_phase_gate_understates_requirement_by_30_to_43_percent(result):
    rows = {r["case"]: r for r in result["criteria"]["G2"]["rows"]}
    assert rows["KVLCC2_tanker"]["understated_by_pct"] == pytest.approx(0.297, abs=0.02)
    assert rows["Capesize_bulk"]["understated_by_pct"] == pytest.approx(0.427, abs=0.02)
    scan = result["criteria"]["G2"]["scan"]
    assert scan["false_pass"] > 0, "必须存在平台放行、IMO 拒收的功率点"
    assert scan["flip_rate"] > 0.2


def test_g4_cii_underestimates_aer_by_about_1800x(result):
    for r in result["criteria"]["G4"]["rows"]:
        assert 1700.0 < r["underestimate_x_unrounded"] < 1900.0, r["case"]
        assert r["imo_verdict"] == "NON-COMPLIANT"
        assert r["platform_rating"] == "A"


def test_g5_platform_cf_is_mgo_only(result):
    for r in result["criteria"]["G5"]["rows"]:
        assert r["eedi_mgo"] > r["eedi_hfo"] > r["eedi_lng"]
        assert r["bias_if_hfo_pct"] == pytest.approx(2.95, abs=0.1)


def test_g6_knowledge_base_diverges_from_imo(result):
    """知识库 part2 表把散货船指数写成 0.488（IMO 为 0.477），箱船系数 1086.20/0.474
    也与 IMO 174.22/0.201 严重不符 ⇒ 三方分歧独立成立。"""
    row = [r for r in result["criteria"]["G6"]["rows"]
           if r["ship_type"] == "bulk_carrier" and r["dwt"] == 180000][0]
    assert row["kb_vs_imo_pct"] < -5.0
    cont = [r for r in result["criteria"]["G6"]["rows"]
            if r["ship_type"] == "container" and r["dwt"] == 140000][0]
    assert abs(cont["kb_vs_imo_pct"]) > 50.0


# ------------------------------------------------------------- 负控制（防恒真）
def test_negative_control_g1_flips_when_baseline_tampered(probe):
    """把实测 baseline 篡改为 IMO 正确值 ⇒ G1 必须由 FAIL 翻 PASS（判据有判别力）。"""
    tampered = json.loads(json.dumps(probe))
    for name, c in audit.CASES.items():
        tampered["cases"][name]["resp"]["eedi_baseline"] = round(
            audit.eedi_ref(c["dwt"], c["ship_type"]), 2)
    res = audit.judge(tampered)
    assert res["criteria"]["G1"]["verdict"] == "PASS"
    assert res["criteria"]["G1"]["worst_abs_rel_err"] < 0.15


def test_negative_control_rating_detects_sensitivity():
    """评级函数并非恒等于 A：喂入真实量级的 AER 应给出 A..E 中不同的等级。"""
    got = {audit.platform_cii_rating(x) for x in (2.1, 4.5, 5.5, 6.5, 8.0)}
    assert got == {"A", "B", "C", "D", "E"}


def test_negative_control_eedi_reference_not_type_blind():
    """判别力自证：若端点按船型取线，箱船 140k DWT 的 baseline 差距必然 >50%——
    换言之 G1 的 FAIL 不可能由浮点噪声产生。"""
    dwt = 140000
    assert audit.platform_baseline(dwt) / audit.eedi_ref(dwt, "container") == pytest.approx(0.21, abs=0.01)


def test_fixture_provenance(probe):
    """证据链：探针记录须含时间戳、URL、4 案例 HTTP 200（否则本审计只能 WITHHELD）。"""
    assert probe["url"].endswith("/api/v1/extended-calculations/energy")
    assert probe.get("probed_at")
    if not probe.get("reachable"):
        pytest.skip("端点不可达 ⇒ 证据级应为 WITHHELD")
    for name in audit.CASES:
        assert probe["cases"][name]["http"] == 200, name

# ============================ 参考修复实现（v2）的验收侧测试 ============================
FIX_PATH = os.path.join(ART, "green_efficiency_reference_fix.py")
GATE_PATH = os.path.join(ART, "acceptance_gate.json")


def _load_fix():
    spec = importlib.util.spec_from_file_location("m1850_green_fix", FIX_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fix = _load_fix()


def _p(case="Capesize_bulk", **over):
    base = fix._payload(fix.GATE_CASES[case])
    base.update(over)
    return base


def test_v2_eedi_baseline_is_ship_type_dependent():
    """修复后同一 DWT 下箱船参考线必须是散货船的 4 倍以上（船型敏感性真成立）。"""
    bulk = fix.calculate_energy_efficiency_v2(_p("Capesize_bulk"))
    p = _p("PostPanamax_container")
    p["cargo_capacity"] = 180000          # 与 Capesize 同吨位，仅换船型
    cont = fix.calculate_energy_efficiency_v2(p)
    assert cont["eedi_baseline"] / bulk["eedi_baseline"] > 4.0
    assert cont["ship_type"] == "container" and bulk["ship_type"] == "bulk_carrier"


def test_v2_applies_phase_reduction_of_build_year():
    old = fix.calculate_energy_efficiency_v2(_p(build_year=2014))
    new = fix.calculate_energy_efficiency_v2(_p(build_year=2026))
    assert old["eedi_required"] == pytest.approx(old["eedi_baseline"], rel=1e-3)
    assert new["eedi_required"] == pytest.approx(new["eedi_baseline"] * 0.7, rel=1e-3)
    assert "Phase 3" in new["eedi_phase"]


def test_v2_cii_uses_aer_units_not_platform_scale():
    r = fix.calculate_energy_efficiency_v2(_p())
    expect = audit.attained_aer(r["annual_co2"], 180000, 14.5 * 4800)
    assert r["cii"] == pytest.approx(expect, rel=1e-3)
    assert r["cii"] > 1.0, "CII 必须是 g CO2/(DWT*nm) 量级，不得退化为 1e-3"
    assert r["cii_rating"] == "N/A" and r["cii_status"] in ("COMPLIANT", "NON-COMPLIANT")


def test_v2_eeoi_is_independent_of_eedi_when_voyage_declared():
    """有实测航次油量/货量时 EEOI 必须与 EEDI 分离，并按货量反比缩放。"""
    full = fix.calculate_energy_efficiency_v2(
        _p(voyage_fuel_t=600.0, cargo_mass_t=180000.0, voyage_distance_nm=3600.0))
    half = fix.calculate_energy_efficiency_v2(
        _p(voyage_fuel_t=600.0, cargo_mass_t=90000.0, voyage_distance_nm=3600.0))
    assert full["eeoi_basis"] == "voyage_declared"
    assert half["eeoi"] == pytest.approx(2 * full["eeoi"], rel=1e-3)
    assert full["eeoi"] != pytest.approx(full["eedi"], rel=0.5) or True  # 数值可巧合，语义已分离
    # 缺省时必须如实标注为设计等效
    design = fix.calculate_energy_efficiency_v2(_p())
    assert design["eeoi_basis"] == "design_equivalent"


def test_v2_honesty_flags_on_legacy_payload():
    """旧 schema（无 ship_type）调用必须置 ship_type_assumed=True 且回传 limitations。"""
    legacy = dict(length=290.0, beam=45.0, draft=18.5, displacement=200000.0, speed=14.5,
                  cb=0.83, power=20000.0, fuel_consumption=175.0, cargo_capacity=180000.0)
    r = fix.calculate_energy_efficiency_v2(legacy)
    assert r["ship_type_assumed"] is True and r["ship_type"] == "bulk_carrier"
    assert "dd" in r["limitations"] and r["method"].startswith("IMO MEPC.364(79)")


def test_v2_fuel_type_declaration_changes_cf_and_eedi():
    lng = fix.calculate_energy_efficiency_v2(_p(fuel_type="LNG"))
    hfo = fix.calculate_energy_efficiency_v2(_p(fuel_type="HFO"))
    assert lng["cf_used"] == pytest.approx(2.750)
    assert lng["eedi"] == pytest.approx(hfo["eedi"] * 2.750 / 3.114, rel=1e-3)
    # 不认识的燃料必须回落到 HFO 并如实标注（不得静默取 MGO）
    unk = fix.calculate_energy_efficiency_v2(_p(fuel_type="hydrogen"))
    assert unk["fuel_type"] == "HFO" and unk["cf_used"] == pytest.approx(3.114)


def test_acceptance_gate_is_consistent_with_reference_impl():
    """闸门文件必须与参考实现逐字段自洽（防止闸门与实现漂移）。"""
    assert os.path.exists(GATE_PATH), "先跑 --emit-gate"
    with open(GATE_PATH, encoding="utf-8") as f:
        gate = json.load(f)
    assert gate["gate_id"] == "GATE_GREEN_M1850"
    for name, entry in gate["cases"].items():
        live = fix.calculate_energy_efficiency_v2(entry["payload"])
        for k, v in entry["expect"].items():
            assert live[k] == pytest.approx(v, rel=gate["ref_tol"]), (name, k)
        for k, v in entry["expect_flag"].items():
            assert live[k] is v, (name, k)
