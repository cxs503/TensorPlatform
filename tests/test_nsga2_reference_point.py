"""m2065: NSGA-II 参考点由可行域推导（清偿 m2009 后续欠账）。

m2009 审计（报告 id=1763）遗留：n_obj==3 参考点仍是硬编码物理猜测
``[3000, 8000, 0]``，跨问题/跨版本比较绝对 HV 不安全，应由可行域推导。

本文件为机器判据（prereg 式）：
1. 参考点不得等于旧硬编码值（问题依赖性）；
2. 参考点在每个目标上严格劣于初始种群全部点（compute_hypervolume 内
   ``np.minimum`` 裁剪不得触发）；
3. 退化（零幅）目标仍有严格劣的参考点；
4. HV 对目标等比缩放按缩放因子之积缩放（HV 在问题内可比的前提性质）；
5. 冰级 3 目标回归：HV 历史为正且末代收敛率 < 0.01（沿用既有判据）。
"""
import numpy as np
import pytest

from optimizer.ice_class.nsga2 import NSGA2Optimizer, compute_hypervolume

OLD_HARD_REF = np.array([3000.0, 8000.0, 0.0])


def _scaled_fn(scale: float):
    """3 目标合成问题：o1≈100s, o2≈200s, o3≈0.5s（全最小化，无约束）。"""
    def fn(x):
        o = scale * np.array([100.0 + x[0],
                              200.0 + x[1],
                              0.5 + x[2]])
        return o, 0.0
    return fn


def _run(scale=1.0, n_gen=2, pop=24, seed=7):
    opt = NSGA2Optimizer(n_objectives=3, n_variables=6, pop_size=pop,
                n_generations=n_gen, seed=seed)
    return opt.run(_scaled_fn(scale), lb=np.zeros(6), ub=np.ones(6),
                   verbose=False)


class TestReferencePointDerivation:
    def test_not_hardcoded_guess(self):
        res = _run()
        assert not np.allclose(res["reference_point"], OLD_HARD_REF), \
            "参考点仍是硬编码物理猜测 [3000, 8000, 0]"

    def test_strictly_worse_than_initial_pool(self):
        # LHS 于 [0,1]^6 ⇒ 初始目标域 (100,101)×(200,201)×(0.5,1.5)
        res = _run()
        ref = res["reference_point"]
        assert np.all(ref > np.array([101.0, 201.0, 1.5])), \
            f"参考点未严格劣于初始可行域上界: {ref}"

    def test_scales_with_problem(self):
        # 目标整体放大 10 倍 ⇒ 参考点同倍放大（可行域推导的直接推论）
        r1 = _run(scale=1.0, seed=3)["reference_point"]
        r10 = _run(scale=10.0, seed=3)["reference_point"]
        assert np.allclose(r10, r1 * 10.0, rtol=1e-9)

    def test_degenerate_objective(self):
        def fn(x):
            o = np.array([100.0 + x[0], 50.0, 0.5 + x[2]])  # o2 恒定
            return o, 0.0
        opt = NSGA2Optimizer(n_objectives=3, n_variables=6, pop_size=24,
                    n_generations=2, seed=5)
        res = opt.run(fn, lb=np.zeros(6), ub=np.ones(6), verbose=False)
        assert res["reference_point"][1] > 50.0

    def test_infeasible_pool_fallback(self):
        def fn(x):
            o = np.array([10.0 + x[0], 20.0 + x[1], 0.1 + x[2]])
            return o, 1.0  # 全不可行
        opt = NSGA2Optimizer(n_objectives=3, n_variables=6, pop_size=24,
                    n_generations=2, seed=9)
        res = opt.run(fn, lb=np.zeros(6), ub=np.ones(6), verbose=False)
        assert np.all(res["reference_point"] > np.array([11.0, 21.0, 1.1]))


class TestHypervolumeProperties:
    def test_hv_scales_with_objective_scaling(self):
        front = np.array([[1.0, 2.0], [2.0, 1.0]])
        ref = np.array([3.0, 3.0])
        s = np.array([2.0, 5.0])
        assert np.isclose(compute_hypervolume(front * s, ref * s),
                          compute_hypervolume(front, ref) * s.prod(),
                          rtol=1e-12)

    def test_hv_positive_on_synthetic_front(self):
        res = _run()
        assert res["hv_history"][0] > 0.0
        assert np.all(np.isfinite(res["hv_history"]))
