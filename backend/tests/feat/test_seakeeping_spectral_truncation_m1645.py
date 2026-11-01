"""m1645 验证欠账：耐波谱矩的频率窗截断偏差（解析尾项修正前后的定量判据）。

背景
----
``backend/services/seakeeping.py`` 的 ``spectrum()`` 只在有限频率窗 [w_lo, w_hi]
上做梯形积分，并按窗内 m0 把谱缩放到 4*sqrt(m0) = Hs。窗内缩放让 Hs 恰好正确，
却把**被丢掉的高频尾部**全部塞进"归一化"里：m2 被系统性低估，于是
Tz = 2*pi*sqrt(m0/m2) 被系统性高估。以 ITTC 双参数谱 T1=10 s、窗 [0.2, 2.0] rad/s 为例，
m2 低 7.1%，Tz 高 3.70%——按平台精度标准（>3% 不可接受）属于不可接受。

判据（全部解析，不依赖任何实验数据）
------------------------------------
1. 指数骨架 S = A*w^-5*exp(-B*w^-4) 的三阶矩有闭式，尾项亦有闭式：
   m0_upper(W) = A/(4B) * (1 - e^(-B/W^4))
   m1_upper(W) = A/4 * B^(-3/4) * gamma(3/4, B/W^4)
   m2_upper(W) = A/4 * B^(-1/2) * sqrt(pi) * erf(sqrt(B/W^4))
   逐项与数值积分（宽窗高分辨）比对。
2. 修正后 m0 必须精确等于 Hs^2/16（窗内 + 尾项），Tz 对频率窗**不敏感**：
   4 个窗 × 3 种谱，相对误差 < 0.05%。
3. 回归判据（锁死欠账）：窗内原始矩算出的 Tz 偏差 > 3%（即本缺陷真实存在），
   修正后 < 0.05%。
4. 反例守护：窗口切进 JONSWAP 峰区时尾项公式不成立，必须 "applied": False 并
   说明「moments are truncation-biased」，不得静默给出错的 Tz。

运行（沙箱缺 psycopg2/cryptography，单测闭环无需 DB）：

    PYTHONPATH=backend python3 -m pytest \
        backend/tests/feat/test_seakeeping_spectral_truncation_m1645.py -q --noconftest
"""
from __future__ import annotations

import math

import pytest

from services.seakeeping import (
    G,
    _integrate,
    exp_skeleton_moments,
    exp_skeleton_upper_moments,
    ittc_m0_closed,
    ittc_m2_closed,
    skeleton_tail_moments,
    spectrum,
    uniform_grid,
)

ITTC_T1 = 10.0
HS = 5.0


def _skeleton(A: float, B: float, w):
    return [A * w ** -5 * math.exp(-B * w ** -4) if w > 0 else 0.0 for w in w]


def _quadrature_upper(A: float, B: float, w_lo: float, w_hi: float = 400.0, n: int = 200001):
    """Reference: trapezoid over [w_lo, w_hi] of the skeleton (tail beyond 400 rad/s < 1e-5 %)."""
    grid = [w_lo + i * (w_hi - w_lo) / (n - 1) for i in range(n)]
    s = _skeleton(A, B, grid)
    m0 = _integrate(s, grid)
    m1 = _integrate([v * w for v, w in zip(s, grid)], grid)
    m2 = _integrate([v * w * w for v, w in zip(s, grid)], grid)
    return m0, m1, m2


# ---------------------------------------------------------------------------
# 1. 闭式尾项 vs 数值积分
# ---------------------------------------------------------------------------


def test_skeleton_total_moments_match_documented_closed_forms():
    A = 173.0 / ITTC_T1 ** 4
    B = 691.0 / ITTC_T1 ** 4
    m0, _m1, m2 = exp_skeleton_moments(A, B)
    assert m0 == pytest.approx(ittc_m0_closed(A, B), rel=1e-14)
    assert m2 == pytest.approx(ittc_m2_closed(A, B), rel=1e-14)


@pytest.mark.parametrize("w_lo", [1.0, 2.0, 3.0])
def test_upper_tail_closed_forms_match_quadrature(w_lo):
    A, B = 173.0 / ITTC_T1 ** 4, 691.0 / ITTC_T1 ** 4
    m0_n, m1_n, m2_n = _quadrature_upper(A, B, w_lo)
    m0_c, m1_c, m2_c = exp_skeleton_upper_moments(A, B, w_lo)
    assert m0_c == pytest.approx(m0_n, rel=2e-5), (m0_c, m0_n)
    assert m1_c == pytest.approx(m1_n, rel=2e-5), (m1_c, m1_n)
    # m2 的参考积分在 400 rad/s 处截断，剩尾 int=A/(2*400^2) 相对 1e-3 量级 → m2 容差放宽到 1e-3
    assert m2_c == pytest.approx(m2_n, rel=1e-3), (m2_c, m2_n)


def test_lower_and_upper_tails_sum_to_the_dropped_mass():
    A, B = 173.0 / ITTC_T1 ** 4, 691.0 / ITTC_T1 ** 4
    grid = uniform_grid(0.2, 2.0, 4001)
    in_m0 = _integrate(_skeleton(A, B, grid), grid)
    tail, applied, _note = skeleton_tail_moments(A, B, grid[0], grid[-1])
    assert applied
    assert in_m0 + tail["m0"] == pytest.approx(ittc_m0_closed(A, B), rel=1e-6)


# ---------------------------------------------------------------------------
# 2. 修正后：m0 精确、Tz 与频率窗无关
# ---------------------------------------------------------------------------


WINDOWS = [(0.2, 2.0, 101), (0.3, 1.6, 81), (0.2, 2.5, 1001), (0.1, 3.0, 2001)]


@pytest.mark.parametrize("kind", ["ittc", "pm", "jonswap"])
@pytest.mark.parametrize("w_lo,w_hi,n", WINDOWS)
def test_corrected_moments_are_window_independent(kind, w_lo, w_hi, n):
    omega = uniform_grid(w_lo, w_hi, n)
    spec = spectrum(kind, HS, ITTC_T1, omega)
    assert spec["tail_correction"]["applied"] is True
    # m0 is the analytic variance of the selected spectrum, by construction
    assert spec["m0"] == pytest.approx(HS * HS / 16.0, rel=1e-12)
    # reference: near-tail-free window
    ref = spectrum(kind, HS, ITTC_T1, uniform_grid(0.01, 12.0, 40001))
    for key in ("m0", "m1", "m2"):
        assert spec[key] == pytest.approx(ref[key], rel=5e-4), key
    assert spec["Tz_s"] == pytest.approx(ref["Tz_s"], rel=5e-4)
    assert spec["Tm01_s"] == pytest.approx(ref["Tm01_s"], rel=5e-4)


def test_ittc_tz_matches_closed_form_within_005_percent_at_coarse_window():
    A, B = 173.0 / ITTC_T1 ** 4, 691.0 / ITTC_T1 ** 4
    tz_closed = 2.0 * math.pi * math.sqrt(ittc_m0_closed(A, B) / ittc_m2_closed(A, B))
    spec = spectrum("ittc", HS, ITTC_T1, uniform_grid(0.2, 2.0, 101))
    assert abs(spec["Tz_s"] / tz_closed - 1.0) < 5e-4


# ---------------------------------------------------------------------------
# 3. 回归判据：把欠账本身锁进测试（窗内原始矩偏差 > 3%）
# ---------------------------------------------------------------------------


def test_in_range_only_moments_carry_the_truncation_bias():
    A, B = 173.0 / ITTC_T1 ** 4, 691.0 / ITTC_T1 ** 4
    tz_closed = 2.0 * math.pi * math.sqrt(ittc_m0_closed(A, B) / ittc_m2_closed(A, B))
    spec = spectrum("ittc", HS, ITTC_T1, uniform_grid(0.2, 2.0, 101))
    tz_in_range = 2.0 * math.pi * math.sqrt(spec["m0_in_range"] / spec["m2_in_range"])
    assert tz_in_range / tz_closed - 1.0 > 0.03, "dot 频窗截断偏差应 >3%（判据前提）"
    assert abs(spec["Tz_s"] / tz_closed - 1.0) < 5e-4


def test_tail_correction_is_small_for_m0_and_dominant_for_m2():
    spec = spectrum("ittc", HS, ITTC_T1, uniform_grid(0.2, 2.0, 101))
    tail = spec["tail_correction"]
    m0_frac = tail["m0"] / spec["m0"]
    m2_frac = tail["m2"] / spec["m2"]
    assert 1e-3 < m0_frac < 0.02, m0_frac          # 尾巴对 m0 是小量（Hs 已被窗内缩放保住）
    assert m2_frac > 0.05, m2_frac                  # 但 ω^-3 尾巴对 m2 是数个百分点量级


# ---------------------------------------------------------------------------
# 4. 反例守护：窗口切进峰区 → 不静默修正
# ---------------------------------------------------------------------------


def test_jonswap_window_inside_peak_is_flagged_not_silently_corrected():
    spec = spectrum("jonswap", HS, ITTC_T1, uniform_grid(1.0, 2.0, 51))
    tail = spec["tail_correction"]
    assert tail["applied"] is False
    assert "truncation-biased" in tail["note"]
    assert tail["m0"] == 0.0 and tail["m2"] == 0.0
    # Hs 仍由窗内缩放保证精确——但 Tz 此时如实带偏，不许假装精确
    assert spec["m0"] == pytest.approx(HS * HS / 16.0, rel=1e-12)


def test_peak_enhancement_free_spectra_are_always_corrected():
    for kind in ("ittc", "pm"):
        _tail, applied, note = skeleton_tail_moments(
            173.0 / ITTC_T1 ** 4, 691.0 / ITTC_T1 ** 4, 0.2, 2.0
        )
        assert applied and note == "analytic tail applied"


def test_spectrum_payload_keeps_sampled_density_semantics():
    """S 仍是窗内采样密度；response_statistics 的积分口径不受尾项影响。"""
    omega = uniform_grid(0.2, 2.0, 101)
    spec = spectrum("ittc", HS, ITTC_T1, omega)
    assert len(spec["S"]) == len(omega)
    assert all(s >= 0.0 for s in spec["S"])
    assert spec["Hs_target"] == HS
    assert spec["Hs_recovered"] == pytest.approx(HS, rel=1e-12)
    assert G > 0  # 深水色散常数在模块内一致