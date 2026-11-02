"""Z 形超调角解析锚 + 核相位机修复的离线复判（m2044）。

跑：``python3 -m pytest tests/test_mmg_zigzag_anchor.py -q``
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
from scipy.integrate import solve_ivp

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.verifier.mmg_reference import MMGParams, self_propulsion_n, zigzag_indices  # noqa: E402
from tools.verifier.mmg_zigzag_anchor import (  # noqa: E402
    LinearConsistent,
    exact_zigzag,
    linear_matrix,
    propagate,
    run_all,
    stabilized_params,
)


@pytest.fixture(scope="module")
def res():
    return run_all()


@pytest.fixture(scope="module")
def prm_n():
    prm = MMGParams()
    return prm, self_propulsion_n(prm)


def test_linear_matrix_structure(prm_n):
    prm, n_p = prm_n
    M = linear_matrix(prm, n_p)
    assert M.shape == (4, 4)
    assert M[2, 1] == 1.0                       # ψ' = r
    assert np.all(M[3, :] == 0.0)               # δ 在保持段为常量
    assert M[0, 3] != 0.0 and M[1, 3] != 0.0    # 舵角进入 v', r'


def test_propagate_matches_independent_rk45(prm_n):
    """矩阵指数传播子 vs 独立 solve_ivp(DOP853, rtol=1e-12) —— 排除自证。"""
    prm, n_p = prm_n
    M = linear_matrix(prm, n_p)
    rate = math.radians(2.5) * prm.L / 7.97
    z0 = np.array([0.01, -0.02, 0.0, 0.0])
    h = 0.5
    z_exp = propagate(z0, h, M, -0.1745, rate)

    def rhs(_t, z):
        d = z[3]
        kappa = math.copysign(rate, -0.1745 - d) if abs(-0.1745 - d) > 1e-15 else 0.0
        return M @ z + np.array([kappa * M[0, 3] * 0.0, 0.0, 0.0, kappa])

    sol = solve_ivp(rhs, (0.0, h), z0, method="DOP853", rtol=1e-12, atol=1e-14)
    assert np.max(np.abs(sol.y[:, -1] - z_exp)) < 1e-6


def test_unstable_set_has_no_bounded_linear_zigzag(prm_n, res):
    """真导数组方向不稳定 ⇒ 线性 Z 形无界：锚点必须报告不可完成（诚实边界）。"""
    prm, n_p = prm_n
    rk = res["real_kernel"]
    assert rk["directionally_stable"] is False
    assert rk["max_real_lambda"] > 0.0
    assert rk["exact_zigzag_completed"] is False
    with pytest.raises(RuntimeError):
        exact_zigzag(linear_matrix(prm, n_p), math.radians(2.5) * prm.L / 7.97, 10.0, 10.0, tau_max=60.0)
    # 无界性量化：|ψ| 随 τ 单调放大且远超任何合理航向
    psis = [abs(d["psi_deg"]) for d in rk["divergence"]]
    assert psis == sorted(psis) and psis[-1] > 100.0 * psis[0]


def test_old_phase_machine_was_degenerate(res):
    """旧口径（二次执行处取值）恒 ≈0；修复后必须与舵角强相关。"""
    rows = res["kernel_rows"]
    for c in rows:
        assert abs(c["old_os1"]) < 0.5, c
        assert c["os1"] > 3.0, c
    by = {c["delta_deg"]: c["os1"] for c in rows if c["delta_deg"] == c["psi_deg"]}
    assert by[20.0] > by[10.0] * 1.3, "超调角必须随舵角单调分离"


def test_peak_heading_consistent_with_os1(res):
    """os1 必须等于「峰值航向 − ψ_stop」（内部自洽，防止又一次口径漂移）。"""
    for c in res["kernel_rows"]:
        assert c["peak_minus_exec2"] < 1e-9, c


def test_linear_consistent_implementation_under_3pct(res):
    """核相位机 + RK4 + 限幅 在严格线性动力上对矩阵指数精确解 <3%。"""
    rows = res["linear_consistent"]
    assert len(rows) == 3
    for c in rows:
        assert c["rel_err_os1"] < 0.03, c
        assert c["exact_os1"] > 1.0


def test_stabilized_set_is_stable():
    prm, n_p, scale, ev = stabilized_params()
    assert scale >= 1.0
    assert all(e.real < 0.0 for e in ev)
    # 实核 + 稳化核的线性矩阵必须不同（否则稳化无效）
    base = linear_matrix(MMGParams(), self_propulsion_n(MMGParams()))
    assert not np.allclose(base[:2, :2], linear_matrix(prm, n_p)[:2, :2])


def test_nonlinearity_reported_not_judged(res):
    """非线性残差只报告量级，不参与 <3% 判定（避免把物理当缺陷）。"""
    for c in res["nonlinearity"]:
        assert math.isfinite(c["rel_err"])
        assert c["rel_err"] > 0.0


def test_rudder_rate_affects_overshoot(res):
    """舵角速率限幅必须真实生效：不同速下 os1 不得全同。"""
    os1 = [r["os1"] for r in res["rate_sensitivity"]]
    assert len({round(v, 6) for v in os1}) > 1
    rates = [r["rate_nondim"] for r in res["rate_sensitivity"]]
    assert rates == sorted(rates, reverse=True)   # U↑ ⇒ 无量纲舵速↓


def test_linear_consistent_kernel_matches_same_matrix(prm_n):
    """LinearConsistent 的右端必须与 exact_zigzag 用的是同一个 M（同构性检查）。"""
    prm, n_p = prm_n
    sprm, sn_p, _scale, _ev = stabilized_params()
    lc = LinearConsistent(sprm, sn_p)
    s = np.array([0.01, -0.02, 0.0, 0.0, 0.0, 0.05])
    delta = 0.05
    ds = lc.kinematics(s, delta, sn_p)
    M = lc._M
    assert abs(ds[1] - (M[0, 0] * s[1] + M[0, 1] * s[2] + M[0, 3] * delta)) < 1e-14
    assert abs(ds[2] - (M[1, 0] * s[1] + M[1, 1] * s[2] + M[1, 3] * delta)) < 1e-14
    assert ds[0] == 0.0 and ds[5] == s[2]


def test_artifacts_and_report_generated():
    out = os.path.join(_ROOT, "artifacts", "mmg_zigzag_anchor")
    doc = os.path.join(_ROOT, "docs", "verification_debt", "maneuvering_zigzag_anchor", "REPORT.md")
    assert os.path.exists(os.path.join(out, "results.json"))
    assert os.path.exists(os.path.join(out, "fig_zigzag_anchor.png"))
    assert os.path.exists(doc)


def test_zigzag_indices_still_returns_legacy_keys(prm_n):
    """修复不得破坏既有调用方：历史键必须保留。"""
    prm, n_p = prm_n
    out = zigzag_indices(prm, n_p, delta_deg=10.0, psi_deg=10.0, U=7.97)
    for key in ("first_overshoot_deg", "second_overshoot_deg", "psi_end_deg",
                "delta_end_deg", "n_tau", "hist"):
        assert key in out
    assert out["hist"].shape[1] == 6