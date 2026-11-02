"""结构 / 振动噪声域验证欠账审计的验收测试 (m1600)。

覆盖三层：
  1. 参考实现层：闭式解对照教科书/标准值（Euler 板屈曲系数、自由-自由梁根、
     S-N 拐点连续性、Miner 手算、箱形剖面模数）；
  2. 台账层：用已落盘的实机 fixture 重放判据，断言判决与已知失效点一致
     （防止审计随源码漂移而静默失效）；
  3. 反例层：注入"物理正确"的合成 fixture，验证判据能把合格实现判成 PASS
     （避免判据恒 FAIL 的假审计）。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "audit"))

import structure_vibration_audit as A  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "structure_vibration_probe.json"


# ---------------------------------------------------------------- 参考实现层
def test_plate_buckling_coefficient_equals_textbook():
    """k=4 长板简支：C = 4π²E/(12(1-ν²)) /E = 3.6154（Roark/Timoshenko）。"""
    C = A.plate_buckling_coefficient()
    assert C == pytest.approx(3.61539, rel=1e-4)


def test_plate_buckling_stress_ordering():
    """临界应力 ∝ (t/b)²：板厚减半 ⇒ 临界应力降至 1/4。"""
    s1 = A.plate_buckling_stress(0.015, 0.9)
    s2 = A.plate_buckling_stress(0.0075, 0.9)
    assert s2 / s1 == pytest.approx(0.25, rel=1e-12)
    # 15 mm / 900 mm 钢板：σ_cr ≈ 207 MPa（C=3.6154 -> 3.6154·E·(t/b)²）
    assert 150e6 < s1 < 260e6


def test_freefree_beam_root_and_scaling():
    """自由-自由梁：n=1 根 4.7300；f ∝ L^-2、∝ sqrt(EI)、∝ m^-0.5。"""
    f = A.freefree_beam_freq(100.0, 1.0e9, 1000.0)
    assert f == pytest.approx((4.730040744862704 / 100.0) ** 2 / (2 * math.pi)
                              * math.sqrt(1.0e9 / 1000.0), rel=1e-12)
    assert A.freefree_beam_freq(200.0, 1.0e9, 1000.0) == pytest.approx(f / 4, rel=1e-12)
    assert A.freefree_beam_freq(100.0, 4.0e9, 1000.0) == pytest.approx(f * 2, rel=1e-12)
    assert A.freefree_beam_freq(100.0, 1.0e9, 4000.0) == pytest.approx(f / 2, rel=1e-12)


def test_box_section_modulus_scales_with_depth_power():
    """箱形剖面：Z 随型深的幂指数 ≈1.2（翼板 ∝D、腹板 ∝D²），且随板厚单调增。"""
    _, z1 = A.box_section_modulus(32.2, 19.5, 0.015)
    _, z2 = A.box_section_modulus(32.2, 39.0, 0.015)
    expo = math.log(z2 / z1) / math.log(2.0)
    assert 1.10 < expo < 1.40, expo
    assert A.box_section_modulus(32.2, 19.5, 0.03)[1] > z1


def test_sn_first_slope_exactness_and_monotone():
    """S-N 第一斜率 N = 10^log_a·Δσ^-3 逐位可复算；N 随 Δσ 单调减。"""
    a1 = 10 ** A.SN_DNV_C1_AIR["log_a1"]
    assert A.sn_life(80.0, **A.SN_DNV_C1_AIR) == pytest.approx(a1 / 80.0**3, rel=1e-12)
    ns = [A.sn_life(s, **A.SN_DNV_C1_AIR) for s in (40.0, 60.0, 80.0, 100.0)]
    assert all(x > y for x, y in zip(ns, ns[1:]))
    # 平台 /fatigue/analyze 报的 N(Δσ=80) 应落在标准值 ±15% 内（见 S10）
    assert A.sn_life(80.0, **A.SN_BS_D) == pytest.approx(2849246.0, rel=0.15)


def test_miner_hand_calculation():
    """Miner：两个 bin 手算 Σn/N（BS-D, m=3, log a=12.164）。"""
    D = A.miner_damage([80.0, 50.0], [1.0e6, 5.0e6], **A.SN_BS_D)
    a1 = 10 ** 12.164
    D_manual = 1e6 / (a1 / 80.0**3) + 5e6 / A.sn_life(50.0, **A.SN_BS_D)
    assert D == pytest.approx(D_manual, rel=1e-12)
    assert D == pytest.approx(0.5891, rel=5e-3)


# ---------------------------------------------------------------- 台账层
@pytest.fixture(scope="module")
def fixture_data() -> dict:
    if not FIX.exists():
        pytest.skip(f"fixture 缺失: {FIX}")
    return json.loads(FIX.read_text(encoding="utf-8"))["fixtures"]


@pytest.fixture(scope="module")
def ledger(fixture_data: dict) -> dict[str, A.Criterion]:
    return {c.cid: c for c in A.build_ledger(fixture_data)}


def test_ledger_size_and_domain_split(ledger):
    assert len(ledger) == 19
    assert sum(1 for c in ledger.values() if c.domain == "vibration") == 9
    assert sum(1 for c in ledger.values() if c.domain == "structure") == 10


@pytest.mark.parametrize("cid,verdict", [
    ("V1", "PASS"),   # 叶频恒等式
    ("V2", "FAIL"),   # 固有频率尺度律 -0.5（物理 -1）
    ("V3", "FAIL"),   # 固有频率对排水量零灵敏度
    ("V4", "PASS"),   # 量级落在闭式带内（仅量级）
    ("V5", "FAIL"),   # f_deck ≡ 2.5 f_hull
    ("V6", "FAIL"),   # 响应可被占位线性式复现
    ("V7", "FAIL"),   # ISO 6954 无量纲/计权
    ("V8", "PASS"),   # 风险旗标内部自洽
    ("V9", "FAIL"),   # /vibration/predict 常量响应
    ("S1", "FAIL"),   # 总纵应力 1e6 单位错误 + Z 多乘 D
    ("S2", "FAIL"),   # σ∝D^-2（物理 D^-1）
    ("S3", "FAIL"),   # 板厚通道缺失
    ("S4", "PASS"),   # 排水量→应力通道成立
    ("S5", "FAIL"),   # 板格屈曲系数 0.9 vs 3.615
    ("S6", "FAIL"),   # actual_stress ≡ 0.5σy
    ("S7", "FAIL"),   # stress_range ≡ 0.3σy
    ("S8", "FAIL"),   # S-N 隐含 log a=13.0
    ("S9", "FAIL"),   # 同服务双实现分歧 6.8×
    ("S10", "PASS"),  # Miner 内核与闭式 BS-D 一致
])
def test_expected_verdicts(ledger, cid, verdict):
    assert ledger[cid].verdict == verdict, (
        f"{cid} 判决漂移：{ledger[cid].verdict} != {verdict}；"
        f"platform={ledger[cid].platform} ref={ledger[cid].reference}")


def test_quantitative_magnitudes(ledger):
    """关键量级断言：应力差 ≥1e4 倍、S-N 差 6~7 倍、屈曲系数低估 75%。"""
    assert ledger["S1"].rel_err > 1e3
    assert 5.0 < ledger["S8"].platform / ledger["S8"].reference < 8.0
    assert ledger["S5"].rel_err == pytest.approx(0.75, abs=0.02)
    assert ledger["V2"].platform == pytest.approx(-0.5, abs=0.02)


# ---------------------------------------------------------------- 反例层
def _synth(**over) -> dict:
    """构造物理自洽的合成响应（用于验证判据不是恒 FAIL）。"""
    base = {
        "vib_base": {"data": {
            "blade_frequency": 6.67, "twice_blade_frequency": 13.33,
            "hull_natural_freq": A.hull_2node_vertical_freq(230.0, 32.2, 19.5,
                                                            0.015, 52030.0),
            "deck_natural_freq": 3.9, "vertical_vibration": 3.1,
            "longitudinal_vibration": 1.9, "vibration_level": "良好",
            "comfort_index": "舒适", "resonance_risk": "低风险"}},
        "vib_disp2x": {"data": {"hull_natural_freq": 0.62 / math.sqrt(2),
                                "deck_natural_freq": 3.9}},
        "vib_length_half_similar": {"data": {"hull_natural_freq": 1.24,
                                             "deck_natural_freq": 3.1}},
        "vib_length_1p5_similar": {"data": {"hull_natural_freq": 0.413,
                                            "deck_natural_freq": 4.2}},
        "vib_draft2x": {"data": {"hull_natural_freq": 0.62, "deck_natural_freq": 3.9}},
        "vib_rpm120": {"data": {"vertical_vibration": 4.0}},
        "vib_predict_a": {"data": {"acceleration": 0.15, "noise_level": 65.0}},
        "vib_predict_b": {"data": {"acceleration": 0.31, "noise_level": 71.0}},
        "struct_base": {"data": {"max_stress": 106.0}},
        "struct_D1p5x": {"data": {"max_stress": 106.0 * A.box_section_modulus(32.2, 19.5, 0.015)[1]
                                  / A.box_section_modulus(32.2, 29.25, 0.015)[1]}},
        "struct_t2x": {"data": {"max_stress": 53.0}},
        "struct_disp2x": {"data": {"max_stress": 212.0}},
        "plate_base": {"data": {"critical_stress": A.plate_buckling_stress(0.015, 0.9) / 1e6,
                                         "actual_stress": 90.0}},
        "struct_fatigue_base": {"data": {"stress_range": 70.5,
                                         "calculated_life": A.sn_life(70.5, **A.SN_BS_D) / 1e6}},
        "fatigue_analyze_bsd": {"data": {"total_damage":
                                         A.miner_damage([80.0, 50.0], [1e6, 5e6], **A.SN_BS_D),
                                         "damage_distribution": [
                                             {"stress_range": 70.5,
                                              "fatigue_life": A.sn_life(70.5, **A.SN_BS_D)}]}},
    }
    base.update(over)
    return base


def test_synthetic_physical_implementation_passes():
    """注入物理自洽响应：V1–V6、S1–S3、S5、S8–S10 应判 PASS（判据非恒 FAIL）。"""
    led = {c.cid: c for c in A.build_ledger(_synth())}
    for cid in ("V1", "V2", "V3", "V5", "V9", "S1", "S2", "S3", "S4",
                "S5", "S8", "S9", "S10"):
        assert led[cid].verdict == "PASS", (
            f"{cid} 对物理自洽实现误判 {led[cid].verdict}；"
            f"platform={led[cid].platform} ref={led[cid].reference}")