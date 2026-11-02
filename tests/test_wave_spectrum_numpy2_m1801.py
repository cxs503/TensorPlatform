"""m1801 回归：海浪谱/短期统计链（NumPy 2.0 断链 + 频率上限 + PM 峰位）。

两层：
  L-static  静态判据：ship backend 内不得存在 `np.trapz(` 直接属性调用（NumPy 2.0 已移除）；
  L-dynamic 进程内复算：import 修复后的模块，与 tools/verif/wave_spectrum/reference.py
            的解析矩（m0=A/(4B)、m2=(A/4)√(π/B)、Tz/Tp=1/(βπ)^{1/4}）逐量对照，3% 门。
动态层在依赖缺失（无 fastapi/pydantic 或无 ship 仓库）时 skip，不让环境问题伪装成通过。
"""
from __future__ import annotations

import importlib
import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verif" / "wave_spectrum"))
import reference as ref  # noqa: E402

SHIP = Path("/srv/tensor/workspace/ship-performance-platform")
WAVE_PY = SHIP / "backend/api/wave_spectrum.py"
ADV_PY = SHIP / "backend/api/seakeeping_advanced.py"
GATE = 3.0

# ---------------------------------------------------------------- 参考核自检（必须永远跑）
def test_reference_tz_over_tp_matches_textbook():
    """ITTC/Bretschneider 谱 Tz = 0.710 Tp（Journée & Massie §3.2.2）。"""
    assert abs(ref.analytic_Tz_over_Tp(1.0, ref.B_ITTC) - 0.710) < 0.005


def test_reference_moments_closure():
    """解析矩 vs 数值积分（20ωp 宽网格）：m0/m2 均应 <1%。"""
    Tp = 10.0
    wp = 2 * math.pi / Tp
    A, B = ref.ALPHA_PM * ref.G ** 2, ref.B_ITTC * wp ** 4
    w = ref.omega_grid(Tp, 20000, 20.0)
    mm = ref.discrete_moments(w, ref.spectrum_two_param(w, A, B))
    assert ref.rel_err(mm["m0"], ref.analytic_m0(A, B)) < 1.0
    assert ref.rel_err(mm["m2"], ref.analytic_m2(A, B)) < 1.0


def test_reference_peak_position_condition():
    """峰位条件 ωp=(0.8B)^{1/4}：B=1.25ωp⁴ ⇒ 比值 1.0；β=0.74 ⇒ 0.877（旧缺陷指纹）。"""
    assert abs(ref.pm_peak_ratio(1.25) - 1.0) < 1e-9
    assert abs(ref.pm_peak_ratio(0.74) - 0.8772) < 1e-3


# ---------------------------------------------------------------- L-static
@pytest.mark.skipif(not WAVE_PY.exists(), reason="ship-performance-platform 不在本机")
def test_no_direct_np_trapz_calls_in_ship_backend():
    bad = []
    for p in [WAVE_PY, ADV_PY]:
        if p.exists() and re.search(r"np\.trapz\(", p.read_text()):
            bad.append(p.name)
    assert not bad, f"NumPy 2.0 会抛 AttributeError 的直接调用仍存在: {bad}"


@pytest.mark.skipif(not ADV_PY.exists(), reason="ship-performance-platform 不在本机")
def test_frequency_range_scales_with_tp():
    """spectrum 端点的频率上限必须随 Tp 自适应（固定 1.5 rad/s 会截断 m2 → Tz 偏高）。"""
    src = ADV_PY.read_text()
    assert "frequency_range: Optional[List[float]] = None" in src
    assert "8.0 * omega_p0" in src


@pytest.mark.skipif(not ADV_PY.exists(), reason="ship-performance-platform 不在本机")
def test_pm_beta_paired_with_alpha():
    """PM 分支 β 必须与 α=0.0081 配对（β=1.25），否则谱峰落在 0.877ωp。"""
    src = ADV_PY.read_text()
    assert "beta = 1.25" in src
    assert "beta = 0.74" not in src


# ---------------------------------------------------------------- L-dynamic
@pytest.fixture(scope="module")
def adv_module():
    if not ADV_PY.exists():
        pytest.skip("ship-performance-platform 不在本机")
    sys.path.insert(0, str(SHIP / "backend"))
    try:
        return importlib.import_module("api.seakeeping_advanced")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ship backend 依赖不可用: {type(e).__name__}: {e}")


@pytest.mark.parametrize("hs,tp", [(3.0, 7.0), (6.0, 10.0), (9.0, 14.0)])
def test_spectrum_endpoint_matches_analytic_moments(adv_module, hs, tp):
    out = adv_module.calculate_wave_spectrum(adv_module.WaveSpectrumInput(
        Hs=hs, Tp=tp, spectrum_type="ittc", gamma=3.3, frequency_steps=400))
    f = np.array(out.frequencies)
    S = np.array(out.spectral_density)
    mm = ref.discrete_moments(f, S)
    tz = 2 * math.pi * math.sqrt(mm["m0"] / mm["m2"])
    assert ref.rel_err(4 * math.sqrt(mm["m0"]), hs) < GATE            # Hs 往返
    assert ref.rel_err(mm["m0"], hs ** 2 / 16) < GATE                  # 重标定后 m0 = Hs²/16
    assert ref.rel_err(tz, ref.analytic_Tz_over_Tp(tp, ref.B_ITTC) * tp) < GATE
    assert ref.rel_err(f[int(np.argmax(S))], 2 * math.pi / tp) < GATE  # 峰位兑现 Tp


@pytest.mark.skipif(not WAVE_PY.exists(), reason="ship-performance-platform 不在本机")
def test_statistics_endpoint_matches_closed_form():
    sys.path.insert(0, str(SHIP / "backend"))
    try:
        ws = importlib.import_module("api.wave_spectrum")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ship backend 依赖不可用: {type(e).__name__}: {e}")
    Tp = 10.0
    wp = 2 * math.pi / Tp
    A, B = ref.ALPHA_PM * ref.G ** 2, ref.B_ITTC * wp ** 4
    w = ref.omega_grid(Tp, 20000, 20.0)
    S = ref.spectrum_two_param(w, A, B).tolist()
    st = ws.WaveSpectrumCalculator.calculate_statistics(w.tolist(), S)
    assert ref.rel_err(st["m0"], ref.analytic_m0(A, B)) < GATE
    assert ref.rel_err(st["m2"], ref.analytic_m2(A, B)) < GATE
    assert ref.rel_err(st["Tz"], ref.analytic_Tz(A, B)) < GATE
    # Te = 2π m0/m_{-1}；m_{-1} = (A/4)B^{-5/4}Γ(5/4)（端点结果 round 到 2 位小数，容差放宽到 3%）
    te_ref = 2 * math.pi * ref.analytic_m0(A, B) / ref.analytic_m_minus1(A, B)
    assert ref.rel_err(st["Te"], te_ref) < GATE
    # ε 依赖积分上限（m4 对数发散），不作为判据 —— 只断言它在合法范围 [0,1)
    assert 0.0 <= st["spectral_width"] < 1.0