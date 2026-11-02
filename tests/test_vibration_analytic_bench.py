"""振动噪声域（vibration & noise）解析基准判据测试 —— 该域此前**零验证测试**。

被测对象：``backend/api/domain/ship_vibration.py``（713 行，提供梁/板固有频率、
激励频率、隔振传递率、质量定律隔声、吻合频率、A 计权、dB 代数与 6 个端点）。

判据来源（全部**独立算路**，与实现零共享代码）：
  * ``artifacts/verif_vibration/ref_vibration.py``：等价方程形式 + Brent 求根、
    复阻抗、扩散场积分、弯曲波速求根、Gauss–Legendre Rayleigh 商；
  * 教科书/标准第二来源：均匀梁无量纲特征值手册常数（4.7300407…）、
    IEC 61672-1 的 A 计权圆整表值。

门槛（与 docs/audits/vibration_noise_verification.md 一致）：
  解析闭式/恒等式 1e-9~1e-12；独立数值算路 3%；A 计权表值 ±0.1 dB（带外 ±0.2 dB）；
  场入射 "-5 dB" 经验约定 ±3 dB。

运行：
    python3 -m pytest tests/test_vibration_analytic_bench.py -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VDIR = ROOT / "artifacts" / "verif_vibration"
sys.path.insert(0, str(VDIR))

import ref_vibration as R  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ship_vibration", ROOT / "backend" / "api" / "domain" / "ship_vibration.py"
)
SV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SV)

STEEL = dict(E=2.1e11, nu=0.30, rho=7850.0)
L_BEAM, B_BEAM, H_BEAM = 12.0, 2.0, 0.012
A_BEAM = B_BEAM * H_BEAM
I_BEAM = B_BEAM * H_BEAM ** 3 / 12.0
PLATE = dict(a=1.20, b=0.60, h=0.008)

BCS = ("cc", "cf", "cp", "pp", "ff")


# ---------------------------------------------------------------- V1 梁特征值
@pytest.mark.parametrize("bc", BCS)
@pytest.mark.parametrize("mode", [1, 2, 3, 4, 5])
def test_beam_eigenvalues_match_independent_roots(bc, mode):
    got = SV.beam_eigenvalues(bc, 5)[mode - 1]
    ref = R.beam_lambda_ref(bc, 5)[mode - 1]
    assert R.rel_err(got, ref) < 1e-9, f"{bc} λ{mode}: platform={got} ref={ref}"


@pytest.mark.parametrize("bc", BCS)
def test_beam_eigenvalues_match_handbook_table(bc):
    """第二来源：数值求根本身 vs 手册常数（前 4 阶）。"""
    for i, tab in enumerate(R.BEAM_LAMBDA_TABLE[bc]):
        assert R.rel_err(SV.beam_eigenvalues(bc, 4)[i], tab) < 1e-9


def test_beam_eigenvalues_reject_unknown_bc():
    with pytest.raises(ValueError):
        SV.beam_eigenvalues("spring-supported")


# ------------------------------------------------------- V2 梁频率与量纲标度
@pytest.mark.parametrize("mode", [0, 1, 2])
def test_beam_frequencies_closed_form(mode):
    got = SV.beam_natural_frequencies("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 3)
    ref = R.beam_freq_ref("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 3)
    assert R.rel_err(got[mode], ref[mode]) < 1e-9


def test_beam_frequency_scaling_laws_are_exact():
    f1 = SV.beam_natural_frequencies("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 1)[0]
    fL = SV.beam_natural_frequencies("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM / 2, 1)[0]
    fE = SV.beam_natural_frequencies("cc", 4 * STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 1)[0]
    fI = SV.beam_natural_frequencies("cc", STEEL["E"], 4 * I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 1)[0]
    assert R.rel_err(fL / f1, 4.0) < 1e-12      # f ∝ 1/L²
    assert R.rel_err(fE / f1, 2.0) < 1e-12      # f ∝ sqrt(EI)
    assert R.rel_err(fI / f1, 2.0) < 1e-12


def test_beam_frequency_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        SV.beam_natural_frequencies("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, -1.0, 1)


# ------------------------------------------------- V3 FEM vs 解析（内部交叉验证）
@pytest.mark.parametrize("bc", ["cc", "cf", "pp"])
def test_beam_fem_converges_to_closed_form(bc):
    ana = R.beam_freq_ref(bc, STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 3)
    coarse = SV.beam_fem_frequencies(bc, STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 8, 3)
    fine = SV.beam_fem_frequencies(bc, STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 64, 3)
    e_coarse = R.rel_err(coarse[0], ana[0])
    e_fine = R.rel_err(fine[0], ana[0])
    assert e_fine < 1e-6, f"{bc} FEM 64 elements: rel_err={e_fine:.3e}"
    assert e_fine < e_coarse, "FEM 误差应随网格加密下降"
    assert math.log2(e_coarse / e_fine) > 3.0, "Hermite 三次单元应观察到 ~4 阶收敛"


def test_beam_fem_rejects_free_free():
    with pytest.raises(ValueError):
        SV.beam_fem_frequencies("ff", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 8, 3)


# --------------------------------------------------------- V4/V5 板 Kirchhoff
def test_plate_rigidity_closed_form():
    got = SV.plate_rigidity(STEEL["E"], STEEL["nu"], PLATE["h"])
    ref = R.plate_rigidity_ref(STEEL["E"], STEEL["nu"], PLATE["h"])
    assert R.rel_err(got, ref) < 1e-12


@pytest.mark.parametrize("m,n", [(1, 1), (2, 1), (1, 2), (3, 2), (4, 3)])
def test_plate_ssss_matches_navier(m, n):
    got = SV.plate_natural_frequency(m, n, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                                     STEEL["rho"], PLATE["h"])
    ref = R.plate_navier_freq(m, n, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                              STEEL["rho"], PLATE["h"])
    assert R.rel_err(got, ref) < 1e-12


@pytest.mark.parametrize("m,n", [(1, 1), (3, 2), (4, 3)])
def test_plate_rayleigh_independent_integration(m, n):
    """平台 np.gradient+梯形 vs 本文件解析导数+Gauss–Legendre：门 3%。"""
    got = SV.plate_rayleigh_frequency(m, n, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                                      STEEL["rho"], PLATE["h"])
    ref = R.plate_rayleigh_ref(m, n, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                               STEEL["rho"], PLATE["h"])
    assert R.rel_err(got, ref) < 0.03, f"({m},{n}) platform={got} ref={ref}"


def test_plate_rayleigh_error_shrinks_with_grid():
    exact = R.plate_navier_freq(1, 1, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                                STEEL["rho"], PLATE["h"])
    e = [R.rel_err(SV.plate_rayleigh_frequency(1, 1, PLATE["a"], PLATE["b"], STEEL["E"],
                                               STEEL["nu"], STEEL["rho"], PLATE["h"], n), exact)
         for n in (50, 100, 200, 400)]
    assert all(e[i] > e[i + 1] for i in range(len(e) - 1)), f"离散误差非单调下降: {e}"
    assert e[-1] < 0.01


# --------------------------------------------------------------- V6 A 计权
@pytest.mark.parametrize("f", [31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0])
def test_a_weighting_formula_consistency(f):
    assert R.rel_err(SV.a_weighting(f), R.a_weighting_ref(f)) < 1e-12


@pytest.mark.parametrize("f", [31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0])
def test_a_weighting_vs_iec_table(f):
    gate = 0.1 if 63.0 <= f <= 8000.0 else 0.2   # 带外：解析式与其圆整表固有差 ~0.13 dB
    got = SV.a_weighting(f)
    assert abs(got - R.IEC_A_TABLE[f]) <= gate, f"{f} Hz: {got} vs table {R.IEC_A_TABLE[f]}"


def test_a_weighting_zero_at_1khz_by_definition():
    assert abs(SV.a_weighting(1000.0)) < 1e-3


def test_a_weighting_monotone_reference_at_4000_is_positive():
    """A 计权在 2–5 kHz 为正值（人耳最敏感带），越过即说明极零写错。"""
    assert SV.a_weighting(2000.0) > 0 and SV.a_weighting(4000.0) > 0


# ------------------------------------------------------------ V7 吻合频率
def test_coincidence_frequency_matches_wave_speed_root():
    got = SV.coincidence_frequency(STEEL["E"], STEEL["nu"], STEEL["rho"], PLATE["h"])
    ref = R.coincidence_freq_ref(STEEL["E"], STEEL["nu"], STEEL["rho"], PLATE["h"])
    assert R.rel_err(got, ref) < 1e-9


def test_coincidence_frequency_scales_as_inverse_thickness():
    f1 = SV.coincidence_frequency(STEEL["E"], STEEL["nu"], STEEL["rho"], PLATE["h"])
    f2 = SV.coincidence_frequency(STEEL["E"], STEEL["nu"], STEEL["rho"], PLATE["h"] / 2)
    assert R.rel_err(f2 / f1, 2.0) < 1e-12


# --------------------------------------------------- V8 质量定律（复阻抗算路）
@pytest.mark.parametrize("f,ms", [(63.0, 20.0), (125.0, 20.0), (500.0, 47.1), (2000.0, 78.5)])
def test_mass_law_matches_complex_impedance(f, ms):
    got = SV.panel_tl(f, ms)["tl_normal_db"]
    ref = R.panel_tl_normal_ref(f, ms)
    assert R.rel_err(got, ref) < 1e-12


def test_mass_law_octave_slope():
    d = SV.panel_tl(2000.0, 47.1)["tl_normal_db"] - SV.panel_tl(1000.0, 47.1)["tl_normal_db"]
    assert abs(d - 20.0 * math.log10(2.0)) < 0.15


# ------------------------------------- V9 场入射 "-5 dB" 经验约定的偏差上界
@pytest.mark.parametrize("f,ms", [(125.0, 20.0), (250.0, 47.1), (1000.0, 47.1), (4000.0, 78.5)])
def test_field_incidence_convention_within_3db(f, ms):
    conv = SV.panel_tl(f, ms)["tl_field_db"]
    exact = R.panel_tl_diffuse_ref(f, ms)
    assert abs(conv - exact) <= 3.0, f"{f}Hz: 约定 {conv:.2f} dB vs 扩散场积分 {exact:.2f} dB"


def test_field_incidence_convention_is_lower_bound_and_biased():
    """扩散场隔声量必低于正入射（掠入射透射更大）；-5 dB 是保守但有偏的近似。"""
    for f, ms in ((125.0, 20.0), (1000.0, 47.1)):
        normal = SV.panel_tl(f, ms)["tl_normal_db"]
        exact = R.panel_tl_diffuse_ref(f, ms)
        assert exact < normal
        assert abs((normal - 5.0) - exact) < 3.0


# ------------------------------------------------------- V10 隔振传递率
@pytest.mark.parametrize("zeta", [0.0, 0.05, 0.2, 0.5])
def test_transmissibility_unity_at_sqrt2(zeta):
    got = SV.isolation_transmissibility(math.sqrt(2.0), 1.0, zeta)["transmissibility"]
    assert R.rel_err(got, 1.0) < 1e-12


@pytest.mark.parametrize("zeta", [0.05, 0.2, 0.5])
def test_transmissibility_at_resonance(zeta):
    got = SV.isolation_transmissibility(1.0, 1.0, zeta)["transmissibility"]
    ref = math.sqrt(1.0 + (2.0 * zeta) ** 2) / (2.0 * zeta)
    assert R.rel_err(got, ref) < 1e-12


def test_transmissibility_isolation_region_monotone_and_below_one():
    rs = np.linspace(1.5, 6.0, 60)
    T = [SV.isolation_transmissibility(float(r), 1.0, 0.05)["transmissibility"] for r in rs]
    assert all(t < 1.0 for t in T)
    assert all(T[i] > T[i + 1] for i in range(len(T) - 1))


def test_transmissibility_matches_ref_over_sweep():
    for r in (0.2, 0.9, 1.1, 1.42, 2.0, 5.0):
        assert R.rel_err(SV.isolation_transmissibility(r, 1.0, 0.1)["transmissibility"],
                         R.transmissibility_ref(r, 1.0, 0.1)) < 1e-12


def test_undamped_resonance_returns_inf_not_exception():
    out = SV.isolation_transmissibility(1.0, 1.0, 0.0)
    assert math.isinf(out["transmissibility"]) and out["isolated"] is False


# ------------------------------------------------------- V11 dB 能量代数
@pytest.mark.parametrize("n", [2, 4, 10])
def test_combine_identical_levels(n):
    assert abs(SV.combine_levels_db([60.0] * n) - (60.0 + 10.0 * math.log10(n))) < 1e-12


def test_combine_is_order_invariant():
    assert R.rel_err(SV.combine_levels_db([61.0, 70.0, 64.0]),
                     R.combine_db_ref([70.0, 64.0, 61.0])) < 1e-12


def test_octave_bands_conserve_energy():
    out = SV.octave_band_levels(90.0, [63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0])
    tot = 10.0 * math.log10(sum(10.0 ** (lv / 10.0) for lv in out["band_levels_db"]))
    assert abs(tot - 90.0) < 1e-12


def test_required_tl_arithmetic():
    assert SV.required_tl(120.0, 65.0) == pytest.approx(55.0, abs=1e-12)


# --------------------------------------------------- V12 激励频率与裕度语义
def test_blade_rate_orders_definition():
    out = SV.blade_rate_orders(5, 120.0, 3)
    assert out["shaft_hz"] == pytest.approx(2.0, abs=1e-12)
    assert out["bpf_hz"] == pytest.approx(10.0, abs=1e-12)
    assert out["harmonics_hz"] == pytest.approx([10.0, 20.0, 30.0], abs=1e-12)


@pytest.mark.parametrize("stroke,expect", [(4, 45.0), (2, 90.0)])
def test_diesel_firing_frequency(stroke, expect):
    out = SV.diesel_firing_frequencies(900.0, 6, stroke, 3)
    assert out["firing_hz"] == pytest.approx(expect, abs=1e-12)


def test_resonance_margin_rule():
    assert SV.resonance_margin(1.5, 1.0, 0.15)["passed"] is True
    assert SV.resonance_margin(1.10, 1.0, 0.15)["passed"] is False
    assert SV.resonance_margin(0.90, 1.0, 0.15)["passed"] is False   # 下方 10% 裕度不足


def test_resonance_margin_boundary_is_floating_point_knife_edge():
    """记录已知边界语义：|margin| 恰为 15% 时因二进制舍入落入"不通过"。

    (1.15-1.0)/1.0 = 0.14999999999999991 < 0.15 —— 属边界鲁棒性问题（建议实现用
    |margin| + eps >= tol），不是物理误差，故此处显式钉住现状而非判 FAIL。
    """
    m = SV.resonance_margin(1.15, 1.0, 0.15)
    assert m["margin"] < 0.15           # (1.15-1)/1 = 0.14999999999999991
    assert m["passed"] is False
    m2 = SV.resonance_margin(0.85, 1.0, 0.15)
    assert abs(m2["margin"]) >= 0.15    # (0.85-1)/1 = -0.15000000000000002，同一语义边界落在另一侧
    assert m2["passed"] is True
    assert SV.resonance_margin(1.15 + 1e-9, 1.0, 0.15)["passed"] is True


# ------------------------------------- 证据完整性（verify.py 产出的判据账本）
def test_results_ledger_has_no_failures():
    path = VDIR / "results.json"
    if not path.exists():
        pytest.skip("results.json 未生成，先跑 artifacts/verif_vibration/verify.py")
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["summary"]["checks"] >= 100
    bad = [r for r in d["rows"] if r["verdict"] == "FAIL"]
    assert not bad, f"{len(bad)} 项未过门: {[r['label'] for r in bad][:5]}"


def test_gate_is_not_vacuous():
    """反空转守护：门本身必须能抓住 5% 偏差，否则测试集是摆设。"""
    f = SV.beam_natural_frequencies("cc", STEEL["E"], I_BEAM, STEEL["rho"], A_BEAM, L_BEAM, 1)[0]
    perturbed = f * 1.05
    assert R.rel_err(perturbed, f) > 1e-9     # 1e-9 门抓得住
    g = SV.plate_rayleigh_frequency(1, 1, PLATE["a"], PLATE["b"], STEEL["E"], STEEL["nu"],
                                    STEEL["rho"], PLATE["h"])
    assert R.rel_err(g * 1.05, g) > 0.03      # 3% 门抓得住