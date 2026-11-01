"""振动噪声域解析基准测试（m1493）。

每个能力都用**独立**基准判定，不用被测函数自己算基准：

1. 梁特征值 vs Blevins《Formulas for Natural Frequency and Mode Shape》表值（相对误差 < 1e-6）；
2. 梁固有频率 vs Hermite 三次单元一致质量 FEM（离散误差随单元数收敛，测收敛阶）；
3. 板频率 vs Rayleigh 商 + 数值积分（变分原理独立复算，< 0.1%）；
4. 隔振传递率 vs 解析恒等式 T(sqrt2)=1、高频 1/r^2 渐近；
5. 隔声质量定律 vs 20log10(m''f)-42.53 渐近式（自洽性 + 5 dB 场入射修正）；
6. 吻合频率 vs 钢的经验式 f_c[kHz] ~ 12/h[mm]（< 2%）；
7. A 计权 vs IEC 61672-1 表值（±0.2 dB）；
8. dB 叠加 vs 能量公式（两个 60 dB -> 63.01 dB）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import importlib.util  # noqa: E402

# 按文件路径加载被测模块：backend/api/__init__.py 会级联导入 auth->database->psycopg2
# （本机/精简环境无该依赖，且纯数值单元测试不需要整站 app）。
# 这样本文件在完整 CI 与精简环境都能跑，不改变被测代码本身。
_MOD_PATH = BACKEND_DIR / "api" / "domain" / "ship_vibration.py"
_spec = importlib.util.spec_from_file_location("ship_vibration_under_test", _MOD_PATH)
sv = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sv
_spec.loader.exec_module(sv)

a_weighting = sv.a_weighting
beam_eigenvalues = sv.beam_eigenvalues
beam_fem_frequencies = sv.beam_fem_frequencies
beam_mode_shape = sv.beam_mode_shape
beam_natural_frequencies = sv.beam_natural_frequencies
blade_rate_orders = sv.blade_rate_orders
coincidence_frequency = sv.coincidence_frequency
combine_levels_db = sv.combine_levels_db
diesel_firing_frequencies = sv.diesel_firing_frequencies
isolation_transmissibility = sv.isolation_transmissibility
octave_band_levels = sv.octave_band_levels
panel_tl = sv.panel_tl
plate_frequencies = sv.plate_frequencies
plate_natural_frequency = sv.plate_natural_frequency
plate_rayleigh_frequency = sv.plate_rayleigh_frequency
plate_rigidity = sv.plate_rigidity
required_tl = sv.required_tl
resonance_margin = sv.resonance_margin
resonance_screening = sv.resonance_screening

STEEL_E = 2.1e11
STEEL_RHO = 7850.0
STEEL_NU = 0.3

# Blevins, Formulas for Natural Frequency and Mode Shape (1979), Table 8-1
BLEVINS = {
    "clamped-clamped": [4.73004074, 7.85320462, 10.99560784, 14.13716549],
    "cantilever": [1.87510407, 4.69409113, 7.85475744, 10.99554073],
    "clamped-pinned": [3.92660231, 7.06858275, 10.21017612, 13.35176878],
    "free-free": [4.73004074, 7.85320462, 10.99560784, 14.13716549],
    "pinned-pinned": [math.pi, 2 * math.pi, 3 * math.pi, 4 * math.pi],
}


def rel_err(calc: float, ref: float) -> float:
    return abs(calc - ref) / abs(ref)


# ---------------------------------------------------------------------------
# 1. 梁特征值 vs 文献表
# ---------------------------------------------------------------------------
class TestBeamEigenvalues:
    @pytest.mark.parametrize("bc", list(BLEVINS))
    def test_matches_blevins_table(self, bc):
        got = beam_eigenvalues(bc, 4)
        for i, (c, r) in enumerate(zip(got, BLEVINS[bc])):
            assert rel_err(c, r) < 1e-6, f"{bc} mode {i+1}: {c} vs {r}"

    def test_pinned_pinned_is_exact_npi(self):
        for n, lam in enumerate(beam_eigenvalues("pinned-pinned", 6), start=1):
            assert abs(lam - n * math.pi) < 1e-12

    def test_clamped_clamped_asymptote_odd_half_pi(self):
        """C-C 高阶特征值渐近 (2n+1)pi/2 —— 独立于求根器的解析交叉检查。"""
        lams = beam_eigenvalues("clamped-clamped", 8)
        for n, lam in enumerate(lams[1:], start=2):
            assert rel_err(lam, (2 * n + 1) * math.pi / 2) < 5e-4

    def test_cantilever_asymptote(self):
        """悬臂高阶渐近 (2n-1)pi/2：n=2 处仍差 0.39%（渐近式收敛慢），n>=3 才到 1e-4。"""
        lams = beam_eigenvalues("cantilever", 8)
        for n, lam in enumerate(lams[1:], start=2):
            assert rel_err(lam, (2 * n - 1) * math.pi / 2) < (5e-3 if n == 2 else 5e-4)

    def test_free_free_excludes_rigid_body(self):
        """F-F 前两阶是刚体模态（lambda=0），首阶必须是对称弯曲 4.7300。"""
        lams = beam_eigenvalues("free-free", 3)
        assert lams[0] > 4.0
        assert rel_err(lams[0], 4.73004074) < 1e-6

    def test_increases_monotonically(self):
        lams = beam_eigenvalues("clamped-clamped", 6)
        assert all(b > a for a, b in zip(lams, lams[1:]))

    def test_bad_bc_raises(self):
        with pytest.raises(ValueError):
            beam_eigenvalues("welded-magic", 3)


# ---------------------------------------------------------------------------
# 2. 梁固有频率 + FEM 独立交叉验证
# ---------------------------------------------------------------------------
class TestBeamFrequencies:
    def test_frequency_formula_hand_calc(self):
        """手算锚点：f1 = 4.73004074^2/(2 pi L^2) sqrt(EI/(rho A))。"""
        E, I, rho, A, L = STEEL_E, 1.6e-4, STEEL_RHO, 0.02, 6.0
        f1 = beam_natural_frequencies("clamped-clamped", E, I, rho, A, L, 1)[0]
        # Blevins 表值只到 9 位有效数字，故对比容差取 1e-6（求根器本身精度 1e-12）
        hand = 4.73004074 ** 2 / (2 * math.pi * L ** 2) * math.sqrt(E * I / (rho * A))
        assert rel_err(f1, hand) < 1e-6
        assert f1 > 0

    def test_scaling_with_length(self):
        """f ~ 1/L^2：长度加倍，频率降为 1/4。"""
        args = dict(E=STEEL_E, I=1.6e-4, rho=STEEL_RHO, A=0.02)
        f1 = beam_natural_frequencies("cantilever", L=4.0, n_modes=1, **args)[0]
        f2 = beam_natural_frequencies("cantilever", L=8.0, n_modes=1, **args)[0]
        assert rel_err(f2, f1 / 4.0) < 1e-12

    def test_scaling_with_stiffness(self):
        """f ~ sqrt(EI/(rho A))。"""
        f1 = beam_natural_frequencies("pinned-pinned", 2.1e11, 1.6e-4, 7850, 0.02, 6.0, 1)[0]
        f2 = beam_natural_frequencies("pinned-pinned", 4.2e11, 1.6e-4, 7850, 0.02, 6.0, 1)[0]
        assert rel_err(f2 / f1, math.sqrt(2.0)) < 1e-12

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            beam_natural_frequencies("clamped-clamped", -1.0, 1e-4, 7850, 0.02, 6.0, 1)

    @pytest.mark.parametrize("bc", ["pinned-pinned", "clamped-clamped", "cantilever", "clamped-pinned"])
    def test_fem_vs_analytic_within_3pct(self, bc):
        """FEM（20 单元）与解析解相对误差 < 3%（实践上远小于）。"""
        args = (STEEL_E, 1.6e-4, STEEL_RHO, 0.02, 6.0)
        fa = beam_natural_frequencies(bc, *args, n_modes=3)
        ff = beam_fem_frequencies(bc, *args, n_elem=20, n_modes=3)
        for i, (a, f) in enumerate(zip(fa, ff)):
            assert abs(f - a) / a * 100.0 < 3.0, f"{bc} mode {i+1}: fem={f} analytic={a}"

    def test_fem_converges_with_mesh_refinement(self):
        """一致质量 FEM 应随单元数加密单调收敛（误差下降至少 1 个数量级）。"""
        args = (STEEL_E, 1.6e-4, STEEL_RHO, 0.02, 6.0)
        fa = beam_natural_frequencies("clamped-clamped", *args, n_modes=1)[0]
        e_coarse = abs(beam_fem_frequencies("clamped-clamped", *args, n_elem=4, n_modes=1)[0] - fa) / fa
        e_fine = abs(beam_fem_frequencies("clamped-clamped", *args, n_elem=32, n_modes=1)[0] - fa) / fa
        assert e_fine < e_coarse / 10.0, f"no convergence: coarse={e_coarse:.3e} fine={e_fine:.3e}"

    def test_pinned_pinned_fem_tight_bound(self):
        """SS 边界下一致质量 FEM 收敛很快：8 单元误差 < 0.1%（且为上界）。"""
        args = (STEEL_E, 1.6e-4, STEEL_RHO, 0.02, 6.0)
        fa = beam_natural_frequencies("pinned-pinned", *args, n_modes=2)
        ff = beam_fem_frequencies("pinned-pinned", *args, n_elem=8, n_modes=2)
        for a, f in zip(fa, ff):
            assert 0.0 <= (f - a) / a < 1e-3, f"fem={f} analytic={a}"

    def test_free_free_fem_rejected(self):
        with pytest.raises(ValueError):
            beam_fem_frequencies("free-free", STEEL_E, 1.6e-4, STEEL_RHO, 0.02, 6.0)


# ---------------------------------------------------------------------------
# 3. 梁振型
# ---------------------------------------------------------------------------
class TestBeamModeShape:
    @pytest.mark.parametrize("bc", ["clamped-clamped", "cantilever", "pinned-pinned"])
    def test_normalized_and_endpoint_conditions(self, bc):
        ms = beam_mode_shape(bc, 1, 6.0, n_points=101)
        w = ms["w"]
        assert abs(max(abs(x) for x in w) - 1.0) < 1e-9
        assert abs(w[0]) < 1e-6, f"{bc}: w(0) must vanish"
        if bc == "pinned-pinned":
            assert abs(w[-1]) < 1e-6
        if bc == "clamped-clamped":
            assert abs(w[-1]) < 1e-6

    def test_counts(self):
        ms = beam_mode_shape("clamped-clamped", 2, 4.0, n_points=51)
        assert len(ms["x"]) == len(ms["w"]) == 51

    def test_orthogonality_like_zero_crossing_count(self):
        """第 n 阶振型内部零交叉数应随阶次增加（形态合理性检查）。"""
        def crossings(n):
            w = beam_mode_shape("clamped-clamped", n, 6.0, 401)["w"]
            return sum(1 for a, b in zip(w, w[1:])
                       if a * b < 0 and abs(a) > 1e-6 and abs(b) > 1e-6)
        assert crossings(3) > crossings(1)


# ---------------------------------------------------------------------------
# 4. 板
# ---------------------------------------------------------------------------
class TestPlate:
    def test_rigidity(self):
        D = plate_rigidity(STEEL_E, STEEL_NU, 0.012)
        hand = STEEL_E * 0.012 ** 3 / (12 * (1 - STEEL_NU ** 2))
        assert rel_err(D, hand) < 1e-12

    def test_f11_hand_value(self):
        """手算：f11 = (pi/2) sqrt(D/(rho h)) [(1/a)^2+(1/b)^2]。"""
        a, b, h = 3.0, 2.0, 0.012
        D = plate_rigidity(STEEL_E, STEEL_NU, h)
        f11 = plate_natural_frequency(1, 1, a, b, STEEL_E, STEEL_NU, STEEL_RHO, h)
        hand = (math.pi / 2) * math.sqrt(D / (STEEL_RHO * h)) * (1 / a ** 2 + 1 / b ** 2)
        assert rel_err(f11, hand) < 1e-12
        assert 5.0 < f11 < 30.0, f"sanity: {f11} Hz for 3x2x0.012 m steel plate"

    def test_square_plate_mode_degeneracy(self):
        """方板 f_12 = f_21（对称性）。"""
        f12 = plate_natural_frequency(1, 2, 2.0, 2.0, STEEL_E, STEEL_NU, STEEL_RHO, 0.01)
        f21 = plate_natural_frequency(2, 1, 2.0, 2.0, STEEL_E, STEEL_NU, STEEL_RHO, 0.01)
        assert rel_err(f12, f21) < 1e-12

    def test_thicker_plate_stiffer(self):
        f_thin = plate_natural_frequency(1, 1, 3, 2, STEEL_E, STEEL_NU, STEEL_RHO, 0.01)
        f_thick = plate_natural_frequency(1, 1, 3, 2, STEEL_E, STEEL_NU, STEEL_RHO, 0.02)
        assert rel_err(f_thick / f_thin, 2.0) < 1e-12  # f ~ h

    @pytest.mark.parametrize("m,n", [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1)])
    def test_rayleigh_independent_recompute_under_0p1pct(self, m, n):
        """Rayleigh 商（数值积分）独立复算 Navier 闭式解，误差 < 0.1%。"""
        a, b, h = 3.0, 2.0, 0.012
        nav = plate_natural_frequency(m, n, a, b, STEEL_E, STEEL_NU, STEEL_RHO, h)
        ray = plate_rayleigh_frequency(m, n, a, b, STEEL_E, STEEL_NU, STEEL_RHO, h, n_grid=161)
        assert abs(ray - nav) / nav * 100.0 < 0.1, f"({m},{n}) navier={nav} rayleigh={ray}"

    def test_frequencies_sorted_and_count(self):
        fr = plate_frequencies(3, 2, STEEL_E, STEEL_NU, STEEL_RHO, 0.012, n_max=3)
        assert len(fr) == 9
        assert all(b["f_hz"] >= a["f_hz"] for a, b in zip(fr, fr[1:]))
        assert fr[0]["m"] == 1 and fr[0]["n"] == 1

    def test_invalid_mn(self):
        with pytest.raises(ValueError):
            plate_natural_frequency(0, 1, 3, 2, STEEL_E, STEEL_NU, STEEL_RHO, 0.01)

    def test_invalid_nu(self):
        with pytest.raises(ValueError):
            plate_rigidity(STEEL_E, 0.7, 0.01)


# ---------------------------------------------------------------------------
# 5. 激励与共振筛查
# ---------------------------------------------------------------------------
class TestExcitation:
    def test_blade_rate(self):
        r = blade_rate_orders(5, 120.0, 3)   # 120 rpm = 2 rev/s
        assert rel_err(r["shaft_hz"], 2.0) < 1e-12
        assert rel_err(r["bpf_hz"], 10.0) < 1e-12
        assert r["harmonics_hz"] == [10.0, 20.0, 30.0]

    def test_diesel_4stroke(self):
        """4 冲程 6 缸 600 rpm -> 点火频率 = 3 * 10 = 30 Hz。"""
        r = diesel_firing_frequencies(600.0, 6, 4, 2)
        assert rel_err(r["firing_hz"], 30.0) < 1e-12
        assert r["harmonics_hz"] == [30.0, 60.0]

    def test_diesel_2stroke(self):
        r = diesel_firing_frequencies(600.0, 6, 2, 1)
        assert rel_err(r["firing_hz"], 60.0) < 1e-12

    def test_bad_stroke(self):
        with pytest.raises(ValueError):
            diesel_firing_frequencies(600.0, 6, 3)

    def test_margin_pass(self):
        r = resonance_margin(20.0, 10.0)          # +100%
        assert r["passed"] is True and r["margin_pct"] == pytest.approx(100.0)

    def test_margin_fail_boundary(self):
        # 10 Hz 固有频率、激励 11 Hz -> +10% < 15% 判失败
        r = resonance_margin(11.0, 10.0)
        assert r["passed"] is False and r["margin_pct"] == pytest.approx(10.0)
        r2 = resonance_margin(11.6, 10.0)         # +16%
        assert r2["passed"] is True

    def test_screening_flags_resonance(self):
        """固有频率钉在叶频上必须被判失败（不得静默通过）。"""
        sc = resonance_screening([10.0, 55.0], n_blades=5, rpm=120.0, tol=0.15)
        assert sc["passed"] is False
        assert sc["n_failed"] >= 1
        assert any(abs(r["margin_pct"]) < 1e-9 for r in sc["failed"])

    def test_screening_clean_case(self):
        sc = resonance_screening([3.3, 7.1, 44.0], n_blades=5, rpm=120.0, tol=0.15)
        assert sc["n_checks"] == 12  # (shaft + 3x BPF) x 3 个固有频率
        assert sc["worst"] is not None

    def test_screening_includes_diesel(self):
        sc = resonance_screening([30.5], n_blades=5, rpm=600.0, n_cylinders=6, stroke=4)
        sources = {r["excitation"] for r in sc["checks"]}
        assert any("diesel" in s for s in sources)


# ---------------------------------------------------------------------------
# 6. 隔振
# ---------------------------------------------------------------------------
class TestIsolation:
    def test_transmissibility_unity_at_sqrt2(self):
        """解析恒等式：r = sqrt(2) 时 T = 1，与 zeta 无关。"""
        for z in (0.0, 0.02, 0.1, 0.3):
            r = isolation_transmissibility(math.sqrt(2.0) * 10.0, 10.0, z)
            assert abs(r["transmissibility"] - 1.0) < 1e-9, f"zeta={z}"

    def test_static_limit(self):
        r = isolation_transmissibility(1e-4, 10.0, 0.05)
        assert abs(r["transmissibility"] - 1.0) < 1e-6

    def test_high_frequency_decay(self):
        """r >> 1 且 zeta 小时 T ~ 1/(r^2)。"""
        r = isolation_transmissibility(100.0, 1.0, 0.0)   # r = 100
        assert rel_err(r["transmissibility"], 1.0 / 100.0 ** 2) < 1e-2

    def test_known_value(self):
        """r=3, zeta=0.05 -> T = 0.13041..., 隔振量 -17.69 dB（独立手算）。"""
        r = isolation_transmissibility(30.0, 10.0, 0.05)
        hand = math.sqrt((1 + 0.09) / (64.0 + 0.09))
        assert rel_err(r["transmissibility"], hand) < 1e-12
        assert r["isolated"] is True
        assert r["isolation_db"] == pytest.approx(20 * math.log10(hand), abs=1e-9)

    def test_no_isolation_below_sqrt2(self):
        r = isolation_transmissibility(12.0, 10.0, 0.05)   # r=1.2
        assert r["isolated"] is False
        assert r["transmissibility"] > 1.0

    def test_resonance_amplification(self):
        z = 0.05
        r = isolation_transmissibility(10.0, 10.0, z)   # r=1
        hand = math.sqrt(1.0 + (2 * z) ** 2) / (2 * z)
        assert rel_err(r["transmissibility"], hand) < 1e-12

    def test_undamped_resonance_is_inf_not_zero_div(self):
        """zeta=0 且 r=1 时传递率应为 inf（曾因除零崩过，此处钉住行为）。"""
        r = isolation_transmissibility(10.0, 10.0, 0.0)
        assert math.isinf(r["transmissibility"])
        assert r["isolated"] is False
        assert "共振" in r["note"]

    def test_bad_zeta(self):
        with pytest.raises(ValueError):
            isolation_transmissibility(30.0, 10.0, 1.2)


# ---------------------------------------------------------------------------
# 7. 空气声隔声
# ---------------------------------------------------------------------------
class TestNoise:
    def test_mass_law_asymptote_consistency(self):
        """高频渐近：TL_n -> 20log10(m'' f) - 42.53 dB（自洽性检查，不硬编码 42.53 于被测代码）。"""
        r = panel_tl(2000.0, 47.1, field_incidence=False)
        assert abs(r["tl_normal_db"] - r["tl_asymptote_db"]) < 0.05

    def test_field_incidence_minus_5db(self):
        a = panel_tl(500.0, 47.1, field_incidence=True)
        b = panel_tl(500.0, 47.1, field_incidence=False)
        assert abs((b["tl_normal_db"] - a["tl_field_db"]) - 5.0) < 1e-12

    def test_doubling_mass_gives_6db(self):
        a = panel_tl(500.0, 47.1)
        b = panel_tl(500.0, 94.2)
        assert abs((b["tl_field_db"] - a["tl_field_db"]) - 6.0) < 0.3

    def test_doubling_frequency_gives_6db(self):
        a = panel_tl(500.0, 47.1)
        b = panel_tl(1000.0, 47.1)
        assert abs((b["tl_field_db"] - a["tl_field_db"]) - 6.0) < 0.3

    def test_tl_physical_range(self):
        r = panel_tl(1000.0, 47.1)
        assert 40.0 < r["tl_field_db"] < 52.0, f"6mm steel @1kHz field TL={r['tl_field_db']}"

    def test_coincidence_steel_empirical(self):
        """钢的经验式 f_c[kHz] ~ 12/h[mm]：5 mm -> 2.4 kHz，误差 < 2%。"""
        fc = coincidence_frequency(STEEL_E, STEEL_NU, STEEL_RHO, 0.005)
        assert abs(fc - 2400.0) / 2400.0 * 100.0 < 2.0, f"fc={fc}"

    def test_coincidence_scales_inverse_h(self):
        f1 = coincidence_frequency(STEEL_E, STEEL_NU, STEEL_RHO, 0.005)
        f2 = coincidence_frequency(STEEL_E, STEEL_NU, STEEL_RHO, 0.010)
        assert rel_err(f2, f1 / 2) < 1e-12

    def test_required_tl(self):
        assert required_tl(110.0, 60.0) == pytest.approx(50.0)

    def test_combine_two_equal(self):
        assert abs(combine_levels_db([60.0, 60.0]) - 63.0103) < 1e-3

    def test_combine_ten_equal(self):
        assert abs(combine_levels_db([60.0] * 10) - 70.0) < 1e-9

    def test_combine_single(self):
        assert combine_levels_db([73.4]) == pytest.approx(73.4)

    def test_combine_empty_raises(self):
        with pytest.raises(ValueError):
            combine_levels_db([])

    @pytest.mark.parametrize("f,ref", [
        (1000.0, 0.0), (500.0, -3.2), (250.0, -8.6), (125.0, -16.1),
        (4000.0, 1.0), (2000.0, 1.2), (8000.0, -1.1), (63.0, -26.2),
    ])
    def test_a_weighting_vs_iec61672(self, f, ref):
        """IEC 61672-1 A 计权表值比对（±0.2 dB）。"""
        assert abs(a_weighting(f) - ref) < 0.2, f"A({f})={a_weighting(f)} vs {ref}"

    def test_octave_band_sums_back(self):
        r = octave_band_levels(90.0, [63, 125, 250, 500, 1000, 2000, 4000, 8000])
        assert abs(combine_levels_db(r["band_levels_db"]) - 90.0) < 1e-9
        assert r["total_dba"] < 90.0  # A 计权后必然低于线性总级


# ---------------------------------------------------------------------------
# 8. 端点层（按路径加载模块后直接调用函数，不导入整站 app）
# ---------------------------------------------------------------------------
class TestEndpoints:
    def test_router_has_expected_routes(self):
        
        paths = {r.path for r in sv.router.routes}
        assert "/calculations/vibration/beam_natural" in paths
        assert "/calculations/vibration/plate_natural" in paths
        assert "/calculations/vibration/excitation" in paths
        assert "/calculations/vibration/isolation" in paths
        assert "/calculations/noise/panel_tl" in paths
        assert "/calculations/noise/levels" in paths

    def test_beam_endpoint(self):
        resp = sv.api_beam_natural(sv.BeamRequest(n_modes=3, fem_check=True, n_elem=16))
        assert resp["evidence"] == "REAL"
        assert len(resp["frequencies_hz"]) == 3
        assert len(resp["fem_vs_analytic_pct"]) == 3
        assert all(abs(x) < 3.0 for x in resp["fem_vs_analytic_pct"])

    def test_plate_endpoint(self):
        resp = sv.api_plate_natural(sv.PlateRequest(n_max=2, rayleigh_check=True))
        assert resp["evidence"] == "REAL"
        assert len(resp["modes"]) == 4
        assert abs(resp["rayleigh_vs_navier_pct"]) < 0.1

    def test_excitation_endpoint(self):
        resp = sv.api_excitation(sv.ExcitationRequest(n_blades=5, rpm=120, f_natural=[9.9, 40.0]))
        assert resp["evidence"] == "REAL"
        assert resp["screening"]["n_failed"] >= 1

    def test_isolation_endpoint(self):
        resp = sv.api_isolation(sv.IsolationRequest(f=30.0, f_n=10.0, zeta=0.05))
        assert resp["isolated"] is True

    def test_noise_endpoint_with_required_tl(self):
        resp = sv.api_panel_tl(sv.NoiseRequest(f=1000.0, surface_density=47.1,
                                         source_level=110.0, target_level=60.0))
        assert resp["evidence"] == "REAL"
        assert resp["required_tl_db"] == pytest.approx(50.0)
        assert resp["coincidence_hz"] > 0

    def test_levels_endpoint(self):
        resp = sv.api_levels(sv.LevelsRequest(levels=[60.0, 60.0], center_freqs=[1000.0]))
        assert abs(resp["combined_db"] - 63.0103) < 1e-3

    def test_invalid_input_maps_to_422(self):
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as ei:
            sv.api_beam_natural(sv.BeamRequest(bc="nonsense-bc"))
        assert ei.value.status_code == 422

    def test_pydantic_rejects_negative_geometry(self):
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            sv.PlateRequest(a=-1.0)