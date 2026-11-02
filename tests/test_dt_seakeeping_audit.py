"""DT 耐波代理谱积分审计的回归测试（m1847，全部离线、无网络）。

被钉住的性质分三类：
* 判据自证（``judge`` 的三态边界）；
* 物理恒等式（Hs^2 增阻律、0 波高归零、左右舷镜像、JONSWAP 归一化 m0 = Hs^2/16）；
* 参考离散的收敛性 + 负控制（粗积分必须被检出「超 3% 精度带」）。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.dt_seakeeping_audit.audit import (  # noqa: E402
    PLATFORM_GRID_HZ, added_resistance_law_checks, judge, jonswap_band_fraction,
    natural_period_bands, platform_irregular, ref_omega_grid, _integrate,
)
from services.digital_twin.models.seakeeping import jonswap  # noqa: E402


def test_judge_three_state_boundaries():
    assert judge(0.0) == "PASS"
    assert judge(0.0299) == "PASS"
    assert judge(0.03) == "FAIL"          # 边界归入 FAIL（不四舍五入放过）
    assert judge(0.15) == "FAIL"
    assert judge(0.1501) == "FAIL_GT15"
    assert judge(float("nan")) == "WITHHELD"


def test_reference_grid_spans_platform_band():
    w = ref_omega_grid(256)
    assert w[0] < 2 * math.pi * PLATFORM_GRID_HZ[0]
    assert w[-1] > 2 * math.pi * PLATFORM_GRID_HZ[1]
    assert np.all(np.diff(w) > 0)


def test_jonswap_normalised_to_hs():
    """m0 = Hs^2/16 ⇒ 4*sqrt(m0) = Hs（谱定义自洽，供参考积分使用）。"""
    w = ref_omega_grid(4096)
    for Hs, Tp in [(2.0, 6.0), (4.0, 10.0), (5.0, 14.0)]:
        m0 = float(np.trapezoid(jonswap(w, Hs, Tp), w))
        assert abs(4.0 * math.sqrt(m0) - Hs) / Hs < 0.01


def test_band_truncation_is_small():
    """出厂栅格 [0.03,0.50] Hz 截掉的能量 <1 %（证明误差不是尾部截断）。"""
    for Hs, Tp in [(2.0, 6.0), (4.0, 10.0), (5.0, 14.0), (6.0, 8.0)]:
        assert jonswap_band_fraction(Hs, Tp)["outside_fraction"] < 0.01


def test_added_resistance_quadratic_law_exact():
    for ship in ("KCS", "KVLCC2", "Ferry"):
        r = added_resistance_law_checks(ship)
        assert abs(r["quadratic_ratio_4_over_2"] - 4.0) < 1e-12
        assert r["zero_Hs_N"] == 0.0
        assert r["mirror_rel_diff"] < 1e-12


def test_added_resistance_declared_band():
    """模块自述「Hs=4 m 顶浪增阻占静水阻力 5–15 %」——按 15 % 上限硬裁。"""
    for ship in ("KCS", "KVLCC2", "Feeder120", "Aframax", "Ferry"):
        ratio = added_resistance_law_checks(ship)["added_over_calm"]
        assert 0.03 < ratio < 0.15, (ship, ratio)


def test_natural_periods_above_physical_floor():
    """附加质量非负 ⇒ T33 必须严格高于 A33 = 0 的下界；A33/m 落在工程带。"""
    for ship in ("KCS", "KVLCC2", "Feeder120", "Aframax", "Ferry"):
        p = natural_period_bands(ship)
        assert p["T33_over_lower_bound"] > 1.05
        assert 0.3 < p["A33_over_m"] < 1.6


def test_shipped_integration_close_for_low_tp():
    """短峰周期（Tp = 8 s）时出厂 24 点积分与收敛参考差 <3 %（PASS 侧）。"""
    ref = _integrate("KCS", 6.0, 8.0, 180.0, 7.716, False, decim=8)
    got = platform_irregular("KCS", 6.0, 8.0, 180.0, 7.716)
    err = abs(got["heave_rms_m"] - ref["heave_rms_m"]) / ref["heave_rms_m"]
    assert judge(err) == "PASS", err


def test_negative_control_coarse_grid_is_detected():
    """负控制：把出厂栅格抽到 6 点，判据必须报「超 3 % 精度带」。"""
    ref = _integrate("KCS", 4.0, 10.0, 180.0, 0.0, False, decim=8)
    coarse = platform_irregular("KCS", 4.0, 10.0, 180.0, 0.0, n_freq=6)
    err = abs(coarse["heave_rms_m"] - ref["heave_rms_m"]) / ref["heave_rms_m"]
    assert judge(err) != "PASS", err


def test_pitch_integrand_needs_finer_grid_than_heave():
    """已知缺陷钉扎：24 点栅格对纵摇（峰更尖）的误差大于对垂荡。"""
    ref = _integrate("Feeder120", 5.0, 14.0, 180.0, 7.202, False, decim=8)
    got = platform_irregular("Feeder120", 5.0, 14.0, 180.0, 7.202)
    e_h = abs(got["heave_rms_m"] - ref["heave_rms_m"]) / ref["heave_rms_m"]
    e_p = abs(got["pitch_rms_deg"] - ref["pitch_rms_deg"]) / ref["pitch_rms_deg"]
    assert e_p > e_h
    assert judge(e_p) == "FAIL"          # 3 % .. 15 %，不是伪造
    assert e_p < 0.15


def test_doppler_default_off_is_material():
    """已知缺陷钉扎：默认关遭遇频率时，head-sea heave RMS 与正确值偏差 >15 %。"""
    off = platform_irregular("KVLCC2", 4.0, 10.0, 180.0, 7.973, use_encounter=False)
    ref_on = _integrate("KVLCC2", 4.0, 10.0, 180.0, 7.973, True, decim=8)
    rel = abs(off["heave_rms_m"] - ref_on["heave_rms_m"]) / ref_on["heave_rms_m"]
    assert rel > 0.15, rel