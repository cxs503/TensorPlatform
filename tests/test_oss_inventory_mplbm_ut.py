"""OSS 盘点（je-santos/MPLBM-UT）里被复用的公式的可回归校验。

被测对象: runs/oss_mplbm_ut/wetting_law.py
基准: 仓内自身标定/代数恒等（非实验），详见 docs/oss-inventory/MPLBM-UT.md 第六节。
"""
import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "runs" / "oss_mplbm_ut" / "wetting_law.py"
_spec = importlib.util.spec_from_file_location("mplbm_wetting_law", _PATH)
wl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wl)


@pytest.mark.parametrize("g_ads,theta_repo", wl.CALIBRATION)
def test_wetting_law_reproduces_repo_calibration(g_ads, theta_repo):
    """ShanChen.cpp:573 的 cosθ 关系必须重现 contact_angle_droplet_test.py:61 的三点标定 (<3%)。"""
    theta = wl.theta_deg(g_ads)
    rel_err = abs(theta - theta_repo) / theta_repo
    assert rel_err < 0.03, f"G_ads={g_ads}: {theta:.2f}° vs {theta_repo}° -> {rel_err:.2%}"


def test_abs_variant_collapses_wetting_direction():
    """adversarial: 仓内 abs() 版把 156°(疏水) 折叠成 ~23.6°(亲水) —— 移植时必须去 abs。"""
    assert wl.theta_deg(-0.4) == pytest.approx(156.4, abs=0.2)
    assert wl.theta_deg_abs(-0.4) == pytest.approx(23.6, abs=0.2)


@pytest.mark.parametrize("min_radius", [1, 2, 4, 8])
@pytest.mark.parametrize("g_ads", [-0.4, 0.0, 0.4])
def test_drainage_step_equals_laplace(min_radius, g_ads):
    """Δp = Δρ/3 与 Laplace 2σ|cosθ|/r_min 必须代数恒等（0 误差）。"""
    dp = wl.step_pressure(min_radius, g_ads)
    ref = wl.laplace_entry_pressure(min_radius, g_ads)
    assert dp == pytest.approx(ref, rel=1e-12)


def test_monotonic_entry_pressure_with_radius():
    """喉道越细入口压力越高（物理单调性 sanity）。"""
    pcs = [wl.step_pressure(r, -0.4) for r in (1, 2, 4, 8)]
    assert all(a > b for a, b in zip(pcs, pcs[1:]))


def test_neutral_wetting_gives_zero_capillary_pressure():
    """G_ads=0（中性润湿，ShanChen.cpp:304-305 tag 5）-> cosθ=0 -> 无毛管压差。"""
    assert wl.step_pressure(1, 0.0) == pytest.approx(0.0, abs=1e-12)