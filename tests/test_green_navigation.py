"""绿色航行域（EEDI/EEXI/CII）核验测试。

判据分四类：
  A 化学计量层：C_F 由 w_C·44/12 导出，与公布值逐项比对（<3%），且不依赖公布值本身
  B 达到值层：与测试内独立标量复算逐点相等（1e-12）+ 变形（metamorphic）标度关系
  C 规则层：内部一致性（单调性/阶段比例），并**强制**未知船型/阶段/燃料报错（禁止静默默认）
  D 诚实性：CII A–E 等级必须显式 NOT_SUPPORTED，不得伪装成 REAL

所有期望值在测试内由独立表达式生成，不手抄常量结果。
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

# 平台 API 包导入链会读数据库配置；根 tests/ 目录无 backend/conftest.py 的环境垫片，
# 因此在此显式钉住进程级环境（与 backend/conftest.py 保持一致），避免拉起 psycopg2。
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import green_navigation as gn  # noqa: E402


# ---------------------------------------------------------------------------
# A 化学计量层
# ---------------------------------------------------------------------------

def test_carbon_factors_match_published_within_3pct():
    rows = gn.fuel_factor_table()
    assert len(rows) >= 8
    for row in rows:
        assert row["rel_err_pct"] < 3.0, f"{row['fuel']} 偏差 {row['rel_err_pct']:.3f}% 超 3%"


def test_carbon_factor_worst_case_below_half_percent():
    worst = max(r["rel_err_pct"] for r in gn.fuel_factor_table())
    assert worst < 0.5, f"最大偏差 {worst:.4f}% 超过 0.5%（化学计量应几乎精确复现）"


def test_carbon_factor_is_independent_of_published_value(monkeypatch):
    """篡改公布值不得改变导出值 —— 证明导出真的来自化学计量。"""
    spec = gn.FUELS["hfo"]
    before = spec.cf()
    monkeypatch.setitem(gn.FUELS, "hfo", gn.FuelSpec(
        key=spec.key, label=spec.label,
        carbon_mass_fraction=spec.carbon_mass_fraction,
        cf_published=99.9, provenance=spec.provenance,
    ))
    assert gn.FUELS["hfo"].cf() == pytest.approx(before, rel=0, abs=1e-15)


def test_pure_fuel_carbon_fraction_from_formula():
    """纯物质 w_C 必须等于分子式质量比。"""
    molar = {"C": 12.011, "H": 1.008, "O": 15.999}
    for key in ("lng", "lpg_propane", "lpg_butane", "methanol", "ethanol"):
        spec = gn.FUELS[key]
        mass = sum(molar[s] * n for s, n in spec.atoms)
        carbon = sum(molar[s] * n for s, n in spec.atoms if s == "C")
        assert spec.derive_carbon_fraction() == pytest.approx(carbon / mass, rel=0, abs=1e-12)


def test_carbon_mass_balance_identity():
    """10 t HFO 的碳质量 × 44/12 必须精确等于模块给出的 CO₂ 质量。"""
    out = gn.voyage_co2_from_fuel("hfo", 10.0)
    carbon_t = 10.0 * gn.FUELS["hfo"].derive_carbon_fraction()
    assert out["co2_t"] == pytest.approx(carbon_t * gn.CO2_PER_C, rel=0, abs=1e-9)


# ---------------------------------------------------------------------------
# B 达到值层
# ---------------------------------------------------------------------------

def _chain(p_me=8800.0, sfoc=168.0, cf=None, p_ae=440.0, sfoc_ae=215.0):
    cf = gn.carbon_factor("hfo") if cf is None else cf
    return gn.PowerChain(
        p_me_kw=p_me, sfoc_me=sfoc, cf_me=cf,
        p_ae_kw=p_ae, sfoc_ae=sfoc_ae, cf_ae=cf,
    )


def test_attained_index_matches_independent_scalar_recompute():
    chain = _chain()
    cap, v = 82000.0, 14.0
    expected = (8800.0 * 168.0 * gn.carbon_factor("hfo")
                + 440.0 * 215.0 * gn.carbon_factor("hfo")) / (cap * v)
    assert gn.attained_index(chain, cap, v) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("factor,attr", [(2.0, "sfoc"), (2.0, "p_me"), (0.5, "sfoc")])
def test_attained_scales_linearly_with_energy_inputs(factor, attr):
    """辅机置零后，主机能量项必须是严格一次齐次（标度检验）。"""
    base = gn.attained_index(_chain(p_ae=0.0), 82000.0, 14.0)
    kw = dict(p_me=8800.0, sfoc=168.0, p_ae=0.0)
    kw[attr] = kw[attr] * factor
    scaled = gn.attained_index(_chain(**kw), 82000.0, 14.0)
    assert scaled == pytest.approx(base * factor, rel=1e-12)


def test_attained_inversely_scales_with_capacity_and_speed():
    base = gn.attained_index(_chain(), 82000.0, 14.0)
    assert gn.attained_index(_chain(), 164000.0, 14.0) == pytest.approx(base / 2, rel=1e-12)
    assert gn.attained_index(_chain(), 82000.0, 28.0) == pytest.approx(base / 2, rel=1e-12)


def test_energy_breakdown_sums_to_numerator():
    terms = _chain().co2_terms()
    recomposed = (terms["main_co2_g"] + terms["aux_co2_g"]
                  + terms["pti_co2_g"] - terms["eff_credit_g"])
    assert recomposed == pytest.approx(terms["numerator_g"], rel=1e-12)


def test_auxiliary_power_default_branches_and_boundary():
    assert gn.auxiliary_power_default(10000.0) == pytest.approx(0.025 * 10000.0 + 250.0)
    assert gn.auxiliary_power_default(20000.0) == pytest.approx(750.0)
    assert gn.auxiliary_power_default(8000.0) == pytest.approx(400.0)


def test_attained_cii_definition():
    assert gn.attained_cii(1.0e9, 82000.0, 5000.0) == pytest.approx(1.0e9 / (82000.0 * 5000.0), rel=1e-12)
    assert gn.attained_cii(1.0e9, 82000.0, 10000.0) == pytest.approx(
        gn.attained_cii(1.0e9, 82000.0, 5000.0) / 2, rel=1e-12)


def test_power_chain_and_fuel_mass_agree():
    """同一燃料：功率链 CO₂ 与 加油量×C_F 必须一致（跨路径互证）。"""
    hours, p, sfoc = 100.0, 8800.0, 168.0
    cf = gn.carbon_factor("hfo")
    via_power = gn.co2_from_power(p, sfoc, cf, hours) / 1.0e6  # t
    fuel_t = p * sfoc * hours / 1.0e6
    via_fuel = gn.voyage_co2_from_fuel("hfo", fuel_t)["co2_t"]
    assert via_power == pytest.approx(via_fuel, rel=1e-12)


# ---------------------------------------------------------------------------
# C 规则层内部一致性 + 禁止静默默认
# ---------------------------------------------------------------------------

def test_required_eedi_monotone_decreasing_in_capacity():
    caps = [20000.0, 50000.0, 82000.0, 180000.0, 300000.0]
    vals = [gn.required_eedi("bulk_carrier", c)["required_value"] for c in caps]
    assert all(a > b for a, b in zip(vals, vals[1:])), vals


def test_phase_reduction_ratios_exact():
    base = gn.required_eedi("tanker", 150000.0, "phase0")["required_value"]
    for phase, reduction in (("phase1", 0.10), ("phase2", 0.20), ("phase3", 0.30)):
        got = gn.required_eedi("tanker", 150000.0, phase)["required_value"]
        assert got == pytest.approx(base * (1 - reduction), rel=1e-12)


def test_required_value_is_reference_times_one_minus_reduction():
    out = gn.required_eexi("bulk_carrier", 82000.0)
    assert out["required_value"] == pytest.approx(
        out["reference_value"] * (1 - out["phase_reduction"]), rel=1e-12)


def test_required_cii_z_factor_schedule():
    cap = 82000.0
    years = [2023, 2024, 2025, 2026]
    vals = [gn.required_cii("bulk_carrier", cap, y)["required_value"] for y in years]
    assert all(a > b for a, b in zip(vals, vals[1:])), vals
    # 2027 及以后沿用最近确定值（不静默加深），必须与 2026 相等
    assert gn.required_cii("bulk_carrier", cap, 2030)["required_value"] == pytest.approx(vals[-1])
    assert gn.required_cii("bulk_carrier", cap, 2030)["z_factor"] == pytest.approx(0.11)


@pytest.mark.parametrize("call", [
    lambda: gn.carbon_factor("diesel_magic"),
    lambda: gn.required_eedi("submarine", 5000.0),
    lambda: gn.required_eedi("bulk_carrier", 82000.0, "phase9"),
    lambda: gn.required_eexi("ro_ro_pax", 20000.0),
    lambda: gn.required_cii("container_ship", 0.0, 2025),
    lambda: gn.attained_index(_chain(), 82000.0, 0.0),
    lambda: gn.auxiliary_power_default(0.0),
])
def test_no_silent_defaults_or_invalid_inputs(call):
    with pytest.raises((KeyError, ValueError)):
        call()


# ---------------------------------------------------------------------------
# D 诚实性 & 综合评估
# ---------------------------------------------------------------------------

def test_cii_rating_explicitly_not_supported():
    status = gn.cii_rating_status()
    assert status["evidence_grade"] == gn.EVIDENCE_NOT_SUPPORTED
    assert status["implemented"] is False
    assert status["reason"]


def test_evidence_summary_grades_layers():
    layers = {l["layer"]: l for l in gn.evidence_summary()["layers"]}
    assert layers["L1_carbon_factor_chemistry"]["evidence_grade"] == gn.EVIDENCE_REAL
    assert layers["L2_attained_index_energy_chain"]["evidence_grade"] == gn.EVIDENCE_REAL
    assert layers["L3_required_reference_line"]["evidence_grade"] == gn.EVIDENCE_WITHHELD
    assert layers["L4_cii_rating_A_E"]["evidence_grade"] == gn.EVIDENCE_NOT_SUPPORTED


def test_evaluate_eedi_verdict_consistency():
    cap, v = 82000.0, 14.0
    out = gn.evaluate_eedi("bulk_carrier", _chain(), cap, v, "phase2")
    assert out["attained_evidence_grade"] == gn.EVIDENCE_REAL
    assert out["required_evidence_grade"] == gn.EVIDENCE_WITHHELD
    assert out["compliant"] == (out["attained_value"] <= out["required_value"])
    assert out["ratio"] == pytest.approx(out["attained_value"] / out["required_value"], rel=1e-12)
    assert out["attained_value"] == pytest.approx(gn.attained_index(_chain(), cap, v), rel=1e-12)


def test_eexi_stricter_than_eedi_phase0_for_same_ship():
    """EEXI 削减 20% 相对 EEDI phase0 参考线，要求值必然更严。"""
    cap, v = 150000.0, 14.5
    eexi = gn.evaluate_eexi("tanker", _chain(), cap, v)["required_value"]
    eedi0 = gn.required_eedi("tanker", cap, "phase0")["required_value"]
    assert eexi == pytest.approx(eedi0 * 0.8, rel=1e-12)


def test_same_attained_for_eedi_and_eexi_when_inputs_identical():
    cap, v = 82000.0, 14.0
    a = gn.evaluate_eedi("bulk_carrier", _chain(), cap, v)["attained_value"]
    b = gn.evaluate_eexi("bulk_carrier", _chain(), cap, v)["attained_value"]
    assert a == pytest.approx(b, rel=1e-12)


# ---------------------------------------------------------------------------
# API 层冒烟（不强依赖运行中的服务，走 TestClient）
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.domain.ship_green import router as green_router  # noqa: E402


def _client():
    app = fastapi.FastAPI()
    app.include_router(green_router, prefix="/api/v1")
    return TestClient(app)


def test_api_fuel_factors_endpoint():
    r = _client().get("/api/v1/calculations/green/fuel_factors")
    assert r.status_code == 200
    body = r.json()
    assert body["evidence_grade"] == "REAL"
    assert body["within_3pct"] is True
    assert len(body["table"]) >= 8


def test_api_eedi_endpoint_returns_graded_layers():
    payload = {
        "ship_type": "bulk_carrier", "capacity_t": 82000, "v_ref_kn": 14.0,
        "p_me_kw": 8800, "main_fuel": {"fuel": "hfo", "sfoc_g_per_kwh": 168.0},
        "phase": "phase2",
    }
    r = _client().post("/api/v1/calculations/green/eedi", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attained_evidence_grade"] == "REAL"
    assert body["required_evidence_grade"] == "WITHHELD"
    assert body["p_ae_source"].startswith("default")
    # 8800 kW < 10 000 kW → 走 0.05·ΣMCR 分支（不是 0.025·ΣMCR+250）
    assert body["p_ae_kw"] == pytest.approx(0.05 * 8800)


def test_api_rejects_unknown_fuel_and_ship_type():
    client = _client()
    bad_fuel = {
        "ship_type": "bulk_carrier", "capacity_t": 82000, "v_ref_kn": 14.0, "p_me_kw": 8800,
        "main_fuel": {"fuel": "unobtainium", "sfoc_g_per_kwh": 168.0},
    }
    assert client.post("/api/v1/calculations/green/eedi", json=bad_fuel).status_code == 422
    bad_type = dict(bad_fuel, main_fuel={"fuel": "hfo", "sfoc_g_per_kwh": 168.0}, ship_type="submarine")
    assert client.post("/api/v1/calculations/green/eedi", json=bad_type).status_code == 422


def test_api_cii_requires_exactly_one_co2_source():
    client = _client()
    base = {"ship_type": "bulk_carrier", "capacity_t": 82000, "year": 2025, "distance_nm": 5000}
    assert client.post("/api/v1/calculations/green/cii", json=base).status_code == 422
    both = dict(base, co2_g=1e9, fuel_mass_t=100.0, fuel="hfo")
    assert client.post("/api/v1/calculations/green/cii", json=both).status_code == 422
    ok = dict(base, fuel_mass_t=100.0, fuel="hfo")
    r = client.post("/api/v1/calculations/green/cii", json=ok)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["co2_source"] == "fuel_mass_t·C_F"
    assert body["rating"]["evidence_grade"] == "NOT_SUPPORTED"