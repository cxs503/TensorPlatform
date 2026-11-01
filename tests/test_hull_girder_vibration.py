"""m1680 船体梁垂向振动内核的解析判据测试。

判据来源：均匀自由-自由梁 ``cos(bL)cosh(bL)=1`` 与悬臂 ``=-1`` 的解析根
（Timoshenko, *Vibration Problems in Engineering*），以及单自由度频响闭式。
这些是「求解器误差」判据（不是模型误差），因此可按 <0.1 % 验收。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.ship_performance.vibration import hull_girder as hg          # noqa: E402
from services.digital_twin.models.geometry import HullForm                  # noqa: E402

EI, MASS, LEN = 1.0e10, 2000.0, 100.0


def _uniform(n=160, n_modes=4):
    K, M, x = hg.build_beam(LEN, n, lambda _x: EI, lambda _x: MASS)
    return hg.solve_free_free(K, M, x, n_modes=n_modes)


def test_free_free_matches_analytic_roots():
    eig = _uniform(n=80)
    exact = hg.uniform_free_free_freqs(EI, MASS, LEN, 4)
    rel = np.abs(eig.freq_hz / exact - 1.0)
    assert rel.max() < 1e-3, f"max rel err {rel.max():.2e} > 0.1 %"
    # 解析根本身必须与经典常数一致（防止基准漂移）
    assert np.allclose(
        hg.FREE_FREE_ROOTS[:4],
        [4.730040744862704, 7.853204624095838, 10.995607838001671, 14.137165491257464],
        rtol=0, atol=0,
    )


def test_free_free_converges_monotonically():
    errs = []
    for n in (20, 40, 80, 160):
        e = _uniform(n=n, n_modes=1).freq_hz[0]
        errs.append(abs(e / hg.uniform_free_free_freqs(EI, MASS, LEN, 1)[0] - 1.0))
    assert errs[0] > errs[1] > errs[2] and errs[1] > errs[3]
    assert errs[3] < 1e-4   # 细网已进入舍入噪声平台


def test_clamped_free_matches_analytic_roots():
    K, M, _x = hg.build_clamped_free(LEN, 80, EI, MASS)
    lam, _ = hg.generalized_eigh(K, M)
    f = np.sqrt(np.maximum(lam[:3], 0.0)) / (2 * math.pi)
    exact = hg.uniform_clamped_free_freqs(EI, MASS, LEN, 3)
    assert np.abs(f / exact - 1.0).max() < 1e-3


def test_modes_are_mass_normalised_and_orthogonal_to_rigid():
    K, M, x = hg.build_beam(LEN, 40, lambda _x: EI, lambda _x: MASS)
    eig = hg.solve_free_free(K, M, x, n_modes=5)
    assert hg.mode_norm_error(eig) < 1e-10
    Pr = eig.rigid_modes()
    assert np.allclose(Pr.T @ M @ Pr, np.eye(2), atol=1e-10)
    assert np.abs(Pr.T @ M @ eig.phi).max() < 1e-8   # 与其他模态 M-正交


def test_scaling_law_frequency_scales_with_sqrt_ei():
    f1 = _uniform(n=40, n_modes=1).freq_hz[0]
    K, M, x = hg.build_beam(LEN, 40, lambda _x: 4.0 * EI, lambda _x: MASS)
    f2 = hg.solve_free_free(K, M, x, n_modes=1).freq_hz[0]
    assert abs(f2 / f1 - 2.0) < 1e-9


def test_sdof_receptance_matches_closed_form():
    wn, zeta = 2 * math.pi * 1.0, 0.01
    for r in (0.5, 0.9, 1.0, 1.1, 2.0):
        om = r * wn
        amp = abs(hg.sdof_receptance(om, wn, zeta))
        closed = 1.0 / math.sqrt((1 - r ** 2) ** 2 + (2 * zeta * r) ** 2) / wn ** 2
        assert abs(closed / amp - 1.0) < 1e-12


def test_static_modal_sum_satisfies_equilibrium():
    K, M, x = hg.build_beam(LEN, 40, lambda _x: EI, lambda _x: MASS)
    eig_all = hg.solve_all_flex_modes(K, M, x)
    cl = hg.static_closure(eig_all, -0.4 * LEN, 0.0)
    assert cl["equilibrium_residual_rel"] < 1e-3, cl


def test_generalized_eigh_solves_small_pencil():
    A = np.array([[4.0, 1.0], [1.0, 3.0]])
    B = np.array([[2.0, 0.0], [0.0, 1.0]])
    lam, vec = hg.generalized_eigh(A, B)
    for i, l in enumerate(lam):
        assert np.allclose(A @ vec[:, i], l * (B @ vec[:, i]), atol=1e-12)
    assert np.allclose(vec.T @ B @ vec, np.eye(2), atol=1e-12)


def test_solver_avoids_scipy_generalized_driver():
    """本机 LAPACK dsygvd 段错误（m1680），内核不得引用 scipy。"""
    src = Path(hg.__file__).read_text(encoding="utf-8")
    assert "scipy" not in src.split('"""', 2)[2] or "scipy" not in src.replace(
        "scipy.linalg.eigh", ""
    ).split("# ", 1)[0]
    assert hasattr(hg, "generalized_eigh")


@pytest.mark.parametrize("length,tb,cb", [(320.0, 22.0, 0.82), (200.0, 12.5, 0.83)])
def test_ship_girder_quantities_in_engineering_band(length, tb, cb):
    hull = HullForm(length, 60.0 if length > 300 else 32.0, tb, cb)
    g = hg.ShipGirder(hull)
    I_mid = g.girder_inertia_m4()
    f2 = hg.solve_free_free(*hg.build_beam(g.length, 40, g.EI, g.mass_per_length),
                            n_modes=1).freq_hz[0]
    # 回归守护：首版把浸没剖面面积当钢面积，I_mid 达 38639 m^4（高 15 倍）
    assert 120.0 < I_mid < 5000.0, f"I_mid={I_mid:.0f} m^4 超出工程量级"
    assert 0.4 < f2 < 1.6, f"f2={f2:.3f} Hz 超出 2 节点垂向量级"
    assert 25.0 < f2 * 60.0 < 100.0


def test_blade_rate_harmonics_and_margin_verdict():
    exc = hg.BladeRateExcitation(shaft_rps=1.4, blades=4, thrust_N=1e6, harmonics=3)
    assert abs(exc.blade_rate_hz - 5.6) < 1e-12
    assert np.allclose(exc.harmonic_freqs_hz(), [5.6, 11.2, 16.8])
    assert np.allclose(exc.harmonic_forces_n(), [2e4, 1e4, 2e4 / 3])
    # 固有频率恰好等于叶频 → FAIL；远离 → PASS
    bad = hg.blade_rate_margin([5.6], exc, margin=0.10)
    assert bad["verdict"] == "FAIL_RESONANCE_RISK"
    good = hg.blade_rate_margin([3.0], exc, margin=0.10)
    assert good["verdict"] == "PASS"


def test_forced_response_harmonic_content_and_rms():
    K, M, x = hg.build_beam(LEN, 40, lambda _x: EI, lambda _x: MASS)
    eig = hg.solve_free_free(K, M, x, n_modes=6)
    exc = hg.BladeRateExcitation(shaft_rps=1.0, blades=3, thrust_N=1e5, harmonics=2)
    r = hg.forced_response(eig, exc, x_load=-0.4 * LEN, x_out=0.0, zeta=0.01, n_modes=6)
    assert len(r["harmonics"]) == 2
    assert r["vel_rms_mm_s"] > 0.0 and np.isfinite(r["vel_rms_mm_s"])
    # 谐波 RMS 与分量自洽：v_rms = sqrt(sum 0.5 v_k^2)
    v = np.array([h["vel_mm_s"] for h in r["harmonics"]])
    assert abs(r["vel_rms_mm_s"] - math.sqrt(0.5 * np.sum(v ** 2))) < 1e-9


@pytest.mark.parametrize(
    "kw",
    [
        {"steel_ratio": 0.9},
        {"eta_I": 0.01},
        {"added_mass_coeff": -0.5},
        {"long_eff": 1.5},
        {"depth_factor": 5.0},
        {"deck_x_frac": 1.0},
    ],
)
def test_ship_girder_rejects_out_of_range(kw):
    with pytest.raises(hg.GirderModelError):
        hg.ShipGirder(HullForm(200.0, 32.0, 12.5, 0.83), **kw)


def test_build_beam_rejects_bad_inputs():
    with pytest.raises(hg.GirderModelError):
        hg.build_beam(0.0, 10, lambda _x: EI, lambda _x: MASS)
    with pytest.raises(hg.GirderModelError):
        hg.build_beam(100.0, 3, lambda _x: EI, lambda _x: MASS)
    with pytest.raises(hg.GirderModelError):
        hg.build_beam(100.0, 10, lambda _x: -1.0, lambda _x: MASS)
    with pytest.raises(hg.GirderModelError):
        hg.BladeRateExcitation(shaft_rps=0.0, blades=4, thrust_N=1e5)
    with pytest.raises(hg.GirderModelError):
        hg.BladeRateExcitation(shaft_rps=1.0, blades=1, thrust_N=1e5)


def test_evaluate_girder_end_to_end_contract():
    hull = HullForm(320.0, 60.0, 22.0, 0.82)
    g = hg.ShipGirder(hull)
    doc = hg.evaluate_girder(
        g, hg.BladeRateExcitation(shaft_rps=1.4, blades=4, thrust_N=2.4e6), n_elem=40,
        n_modes=4, with_sensitivity=False,
    )
    for key in ("geometry", "girder", "modes", "excitation", "response",
                "static_closure", "resonance", "iso6954_unweighted", "evidence_grade"):
        assert key in doc
    assert doc["modes"]["freq_hz"][0] < doc["modes"]["freq_hz"][1]
    assert doc["static_closure"]["equilibrium_residual_rel"] < 1e-3
    assert set(doc["iso6954_unweighted"]) == {"A_accommodation", "B_crew", "C_working"}
    assert doc["evidence_grade"]["solver"].startswith("REAL")
    assert "WITHHELD" in doc["evidence_grade"]["single_ship_absolute"]
