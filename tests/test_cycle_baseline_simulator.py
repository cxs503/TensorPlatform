"""IN55W3-N08 · 三策略调度模拟器测试（判据 a / e + 反例回归）。

重点不在"数字好看"，而在锁死三条容易悄悄退化的性质：
1. **确定性**：同 seed 必须完全可复现（否则报告里的数字不可复核）。
2. **反例回归（踩坑固化）**：曾经把 weighted 实现成"有空位就派给空闲 worker"，
   结果 weights=[1,2,3,4] 下计数仍近似均分（伪策略）。本测试要求加权策略下计数
   必须显著不均分，否则说明派发退化成 work-conserving。
3. **权重必须与产能匹配**：同负载下，产能不匹配时加权策略恶化平均等待；匹配时显著改善。
   —— 这是本节点唯一的实质性结论，必须可复现。
"""
from __future__ import annotations

import pytest

from tools.cycle_baseline import simulator, spec


def test_simulator_deterministic_for_seed():
    a = simulator.evaluate_policies(n_jobs=60, seed=12345, weights=[1, 2, 3, 4])
    b = simulator.evaluate_policies(n_jobs=60, seed=12345, weights=[1, 2, 3, 4])
    for p in a["policies"]:
        assert a["policies"][p]["measured"]["jain_count"] == pytest.approx(
            b["policies"][p]["measured"]["jain_count"])
        assert a["policies"][p]["measured"]["mean_wait_s"] == pytest.approx(
            b["policies"][p]["measured"]["mean_wait_s"])


def test_round_robin_allocates_equal_counts():
    jobs = simulator.make_workload(n_jobs=40, seed=7)
    workers = simulator.make_workers(4)
    sim = simulator.simulate("round_robin", [dict(j) for j in jobs], workers)
    counts = list(sim["counts"].values())
    assert max(counts) - min(counts) <= 1
    assert sim["n_jobs"] == 40


def test_weighted_actually_constrains_allocation_regression():
    """反例回归：加权策略必须产生与权重重成比例的计数（不是均分）。"""
    jobs = simulator.make_workload(n_jobs=80, seed=11)
    workers = simulator.make_workers(4)
    for w, wt in zip(workers, [1, 2, 3, 4]):
        w["weight"] = wt
    sim = simulator.simulate("weighted", [dict(j) for j in jobs], workers)
    shares = [sim["share_count"][w] for w in ("w0", "w1", "w2", "w3")]
    assert shares[3] > shares[0] * 2, f"加权派发退化为均分：{shares}"
    assert shares[3] == pytest.approx(0.4, abs=0.03)


def test_jain_recovery_within_tolerance_all_policies():
    out = simulator.evaluate_policies(weights=[1, 2, 3, 4], seed=spec.DEFAULT_SEED)
    for p, v in out["policies"].items():
        err = v["err"]["jain_recovery_abs"]
        assert err is not None and err <= spec.JAIN_RECOVERY_TOL, (p, err)
    assert out["verdict"]["jain_recovery_ok"]


def test_policy_theory_consistency_within_5pct():
    for speeds in (None, [1, 2, 3, 4]):
        out = simulator.evaluate_policies(weights=[1, 2, 3, 4], speeds=speeds,
                                          seed=spec.DEFAULT_SEED)
        for p, v in out["policies"].items():
            assert v["err"]["jain_rel"] <= spec.POLICY_THEORY_TOL, (speeds, p, v["err"])
            assert v["err"]["gini_rel"] <= spec.POLICY_THEORY_TOL, (speeds, p, v["err"])
        assert out["verdict"]["theory_ok"]


def test_weights_only_help_when_capacity_matched():
    homo = simulator.evaluate_policies(weights=[1, 2, 3, 4], seed=spec.DEFAULT_SEED)
    matched = simulator.evaluate_policies(weights=[1, 2, 3, 4], speeds=[1, 2, 3, 4],
                                          seed=spec.DEFAULT_SEED)
    homo_ratio = (homo["policies"]["weighted"]["measured"]["mean_wait_s"]
                  / homo["policies"]["round_robin"]["measured"]["mean_wait_s"])
    matched_ratio = (matched["policies"]["weighted"]["measured"]["mean_wait_s"]
                     / matched["policies"]["round_robin"]["measured"]["mean_wait_s"])
    assert homo_ratio > 1.0, f"产能不匹配时加权应更差，实测比 {homo_ratio}"
    assert matched_ratio < 0.2, f"产能匹配时加权应显著更好，实测比 {matched_ratio}"


def test_observation_cutoff_bias_is_quantified():
    out = simulator.evaluate_policies(weights=[1, 2, 3, 4], seed=spec.DEFAULT_SEED)
    cut = out["policies"]["weighted"]["recovery_cutoff"]
    assert cut["jain"] is not None and 0.0 < cut["n_observed"] < cut["n_total"]
    # 截断会带来偏差（本节点实测 0.024），必须被显式记录下来而不是忽略
    assert cut["abs_err"] is not None and cut["abs_err"] < 0.05