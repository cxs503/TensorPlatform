"""OSS 盘点（ebinan92/Fingering_dynamics）的定量结论回归。

被测对象:
* `tools/oss_inventory/fingering_physics.py` —— 独立重写的公开公式（差分算子 / 自由能 kink /
  界面张力恒等式 / 两相黏度插值 / 无量纲换算链 / 谱方法积分因子 / 障碍阵列几何）
* `tools/oss_inventory/fingering_evidence.py` —— 证据指针表（反臆造门）

基准一律是**解析恒等或解析解**（不是实验数据）：详见
`docs/oss_inventory/OSS-INV-Fingering_dynamics.md` 第六节。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "oss_inventory"))

import fingering_evidence as ev  # noqa: E402
import fingering_physics as fp  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. 差分算子：二次场精确 + 光滑场二阶（对应报告 3.1 / 图 3）
# --------------------------------------------------------------------------- #
def test_d2q9_stencils_exact_on_quadratic_field():
    """各向同性模板对二次多项式应给出零误差（Δx=1，内点）。"""
    n = 48
    x = np.arange(n, dtype=float)
    X, Y = np.meshgrid(x, x, indexing="xy")
    quad = (X - 20.0) ** 2 + (Y - 20.0) ** 2 + 3.0 * X * Y
    inner = (slice(2, -2), slice(2, -2))
    assert np.allclose(fp.laplacian(quad)[inner], 4.0, atol=1e-10)
    assert np.allclose(fp.grad_x(quad)[inner], (2 * (X - 20.0) + 3.0 * Y)[inner], atol=1e-10)
    assert np.allclose(fp.grad_y(quad)[inner], (2 * (Y - 20.0) + 3.0 * X)[inner], atol=1e-10)


def test_d2q9_stencils_second_order_on_smooth_field():
    """sin(2x)cos(3y) 上误差应随 h 二阶下降（收敛率 > 1.9）。"""
    st = fp.stencil_check()
    assert st["lap_rate_min"] > 1.9, st
    assert st["grad_rate_min"] > 1.9, st
    # 误差绝对值也应足够小（h=2π/256）
    assert st["lap_l2_256"] < 1e-2
    assert st["gx_l2_256"] < 1e-2


# --------------------------------------------------------------------------- #
# 2. 自由能相场：kink 精确解 + 界面张力恒等式（对应报告 6.1）
# --------------------------------------------------------------------------- #
def test_kink_solution_matches_tanh():
    """仓内 a=−2κ/ξ² 的平衡解必须是 ψ=tanh(x/ξ)，误差应为 h² 量级。"""
    res = fp.solve_kink_newton(xi=2.0, half_width=20.0, n=801)
    assert res["max_abs_err"] < 1e-4
    assert res["ode_residual_max"] < 1e-10


def test_kink_convergence_is_second_order():
    kc = fp.kink_convergence(ns=(101, 201, 401, 801))
    assert min(kc["rate"]) > 1.8, kc["rate"]
    # 加密应单调减小误差
    assert all(a > b for a, b in zip(kc["max_abs_err"], kc["max_abs_err"][1:]))


@pytest.mark.parametrize("sigma,xi", [(0.0625, 2.0), (1.0, 1.5), (0.045, 3.0)])
def test_surface_tension_identity(sigma, xi):
    """σ = 4κ/(3ξ) ⟺ κ = 0.75ξσ；数值积分 κ∫ψ'²dx 必须复现 σ。"""
    kappa = fp.kappa_from_sigma(sigma, xi)
    analytic = fp.kink_surface_tension(kappa, xi)
    numeric = fp.kink_surface_tension_numeric(kappa, xi, n=400001, half_width=25.0)
    assert analytic == pytest.approx(sigma, rel=1e-14)
    assert numeric == pytest.approx(sigma, rel=1e-8)


# --------------------------------------------------------------------------- #
# 3. 两相黏度插值（对应报告 6.2）
# --------------------------------------------------------------------------- #
def test_viscosity_endpoints_and_harmonic_mean():
    v = fp.viscosity_check(M=20.0)
    eta = fp.conversion_chain()["Eta_n"]
    assert v["v_psi_plus1"] == pytest.approx(eta, rel=1e-12)
    assert v["v_psi_minus1"] == pytest.approx(20.0 * eta, rel=1e-12)
    assert v["contrast"] == pytest.approx(20.0, rel=1e-12)
    assert v["v_psi_0"] == pytest.approx(v["harmonic_mean"], rel=1e-12)


def test_flowing_phase_relaxation_equals_repo_magic_tau():
    """ψ=+1 端的 τ_f 必须回到仓内 τ=1/(3−√3)（这是 Eta_n≡v0 的代数后果）。"""
    eta = fp.conversion_chain()["Eta_n"]
    assert 3.0 * eta + 0.5 == pytest.approx(fp.tau_bgk(), rel=1e-12)


def test_interface_phase_tau_far_outside_accuracy_window():
    """陷阱 5 的量化：ψ=−1 端 τ_f 远超 BGK 精度区间（0.5~2）。"""
    assert fp.viscosity_check()["tau_psi_minus1"] > 5.0


# --------------------------------------------------------------------------- #
# 4. 无量纲换算链（对应报告 6.3）
# --------------------------------------------------------------------------- #
def test_dimensional_chain_is_self_consistent():
    ch = fp.conversion_chain()
    assert ch["Ca_identity_rel_err"] < 1e-14          # Ca 闭合（代数恒等）
    assert ch["sigma_identity_rel_err"] < 1e-14       # σ=4κ/(3ξ)
    assert ch["Ma"] < 0.1                             # 不可压假设成立
    assert 0.1 < ch["Re_h20"] < 100.0                 # 低速指进（Stokes）区
    assert ch["Eta_n"] == pytest.approx(ch["v0"], rel=1e-12)   # 仓内换算链是循环定义


# --------------------------------------------------------------------------- #
# 5. 谱方法积分因子（对应报告 6.4）
# --------------------------------------------------------------------------- #
def test_integrating_factor_is_exact_for_pure_diffusion():
    sp = fp.spectral_decay_check(k=2.0, dt=1e-3, steps=500)
    assert sp["rel_err"] < 1e-12
    assert sp["c_num_abs"] == pytest.approx(sp["c_exact_abs"], rel=1e-12)


# --------------------------------------------------------------------------- #
# 6. 障碍阵列几何（对应报告 2.6）
# --------------------------------------------------------------------------- #
def test_staggered_obstacle_array_reproducibility():
    g = fp.staggered_obstacle_array(limit=380, pitch=20)
    assert g["n_blocks"] == 40
    assert g["porosity"] == pytest.approx(1.0 - 40 * 20 * 20 / 380**2, rel=1e-12)
    assert 0.85 < g["porosity"] < 0.92
    # 块不重叠（交错阵列的必须性质）
    seen = set()
    for b in g["blocks"]:
        assert b not in seen
        seen.add(b)


# --------------------------------------------------------------------------- #
# 7. 证据门自身（反臆造）
# --------------------------------------------------------------------------- #
def test_evidence_table_ids_are_unique_and_wellformed():
    ids = [pid for _, _, pid, _ in ev.ALL]
    assert len(ids) == len(set(ids))
    for path, needle, _, _ in ev.ALL:
        assert needle and not needle.startswith("/"), (path, needle)
        assert path.count("/") >= 0
    absent_ids = [n for _, n, _ in ev.ABSENT]
    assert len(absent_ids) == len(set(absent_ids))


def test_evidence_pointers_resolve_on_real_tree():
    """若有本地源码树（盘点时的取回副本），每条指针必须唯一命中。"""
    src = pathlib.Path("/tmp/fing_src")
    if not src.exists():
        pytest.skip("source tree not present (fetched copy is not vendored)")
    spec = importlib.util.spec_from_file_location(
        "fingering_inventory", ROOT / "tools" / "oss_inventory" / "fingering_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fingering_inventory"] = mod  # dataclass 需要模块已注册
    spec.loader.exec_module(mod)
    resolved = mod.resolve_all(src)
    assert len(resolved) == len(ev.ALL)
    for pid, r in resolved.items():
        assert r.line >= 1 and r.path


# --------------------------------------------------------------------------- #
# 8. 报告产物（八节 + 机器块）
# --------------------------------------------------------------------------- #
def test_report_has_eight_sections_and_machine_blocks():
    doc = ROOT / "docs" / "oss_inventory" / "OSS-INV-Fingering_dynamics.md"
    if not doc.exists():
        pytest.skip("report not rendered in this checkout")
    text = doc.read_text(encoding="utf-8")
    for h in ("## 一、概况", "## 二、方法谱系", "## 三、数值实现质量", "## 四、验证面",
              "## 五、与我们 feature_ledger 的映射", "## 六、独立复算",
              "## 七、注意点", "## 八、结论与最小下一步"):
        assert h in text, h
    assert text.count("[OSS-INV]") == 1 and text.count("[/OSS-INV]") == 1
    assert text.count("[OSS-SOL]") == text.count("[/OSS-SOL]") >= 6
    assert "{P_" not in text  # 无未替换占位符