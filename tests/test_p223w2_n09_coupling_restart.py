"""跨求解器 checkpoint / restart 一致性机制的机器判据测试（AC1–AC4）。

被测对象：``tools/hygiene/coupling_restart.py``
测试原则：每条断言对应一个可被推翻的命题。特别地：

* 位级可复现（sha256 相等）与能量守恒（残差/漂移阈值）是**两个不同**的不变量，
  分别断言 —— 只测其中一个会漏掉另一类失效（本模块开发中就撞上过「位级一致但能量
  指数发散」的反力符号 bug）。
* 非同步点 checkpoint 的危害必须是**被证明的**（strict 拒绝 + lenient 发散），
  不是被假设的；同时要有对照：同一 lenient 代码路径从同步点续跑必须位级一致。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from hygiene import coupling_restart as C  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def scenarios():
    return C.default_scenarios()


@pytest.fixture(scope="module")
def lockstep():
    """等步长（cycle=1）场景：时间轴上不存在非同步点。"""
    a = C.SolverSpec("a", n=8, dt=0.25, omega_dt=0.05)
    b = C.SolverSpec("b", n=8, dt=0.25, omega_dt=0.05, interface_index=2)
    return C.Scenario(key="lockstep", description="等步长", specs=(a, b),
                      pairs=(C.CouplingPair("a", "b", 0.004),))


# ═══════════════════════════════════════════════════════════════════════════
# 内核：离散能量与辛积分（位级再一致也救不了符号错误）
# ═══════════════════════════════════════════════════════════════════════════
def test_force_is_restoring_on_highest_mode():
    """二阶差分最高模态 (-1)^i：力必须与位移同号反向？——不，必须是恢复力（与 q 反向）。"""
    spec = C.SolverSpec("a", n=8, dt=0.25, omega_dt=0.05)
    chain = C.QuadraticChain(spec)
    q = np.array([(-1.0) ** i for i in range(8)])
    f = chain.force(q)
    # 恢复力：q_i>0 处力必须为负
    assert np.all(f * q < 0)
    # 幅值 = 4k*q（D2 的最大特征值 -4 ⇒ F = +k*D2 ⇒ F = -4k q）
    np.testing.assert_allclose(f, -4.0 * spec.stiffness * q, rtol=0, atol=1e-15)


def test_chain_energy_conserved_by_verlet():
    spec = C.SolverSpec("a", n=32, dt=0.25, omega_dt=0.05)
    chain = C.QuadraticChain(spec)
    e0 = chain.energy()
    for _ in range(1000):
        chain.step(spec.dt)
    assert abs(chain.energy() - e0) / abs(e0) < 1e-4


def test_spec_stiffness_matches_omega_dt():
    for dt in (0.125, 0.25, 0.5, 1.0):
        spec = C.SolverSpec("x", n=8, dt=dt, omega_dt=0.05)
        omega_max = np.sqrt(4.0 * spec.stiffness)
        assert omega_max * dt == pytest.approx(0.05, rel=1e-12)


def test_energy_is_positive_and_additive(scenarios):
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    bd = runner.energy_breakdown()
    assert runner.energy() > 0
    assert sum(bd.values()) == pytest.approx(runner.energy(), rel=1e-14)
    for name in runner.order:
        assert bd[name] > 0


# ═══════════════════════════════════════════════════════════════════════════
# 构造期拒绝（错的配置要在构造期炸，不能带病跑）
# ═══════════════════════════════════════════════════════════════════════════
def test_duplicate_solver_names_rejected():
    a = C.SolverSpec("dup", n=4, dt=0.25)
    with pytest.raises(C.RestartError):
        C.CoupledRunner([a, a], [])


def test_non_multiple_dt_rejected():
    a = C.SolverSpec("a", n=4, dt=0.25)
    b = C.SolverSpec("b", n=4, dt=0.3)  # 0.3 不是 0.25 的整数倍
    with pytest.raises(C.RestartError):
        C.CoupledRunner([a, b], [])


def test_empty_solver_list_rejected():
    with pytest.raises(C.RestartError):
        C.CoupledRunner([], [])


def test_unknown_pair_endpoint_rejected():
    a = C.SolverSpec("a", n=4, dt=0.25)
    with pytest.raises(C.RestartError):
        C.CoupledRunner([a], [C.CouplingPair("a", "ghost", 0.01)])


def test_negative_advance_rejected():
    runner = C.CoupledRunner([C.SolverSpec("a", n=4, dt=0.25)], [])
    with pytest.raises(C.RestartError):
        runner.advance(-1)


# ═══════════════════════════════════════════════════════════════════════════
# 时钟：cycle、相位、同步点
# ═══════════════════════════════════════════════════════════════════════════
def test_cycle_is_lcm_and_nsub(scenarios):
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner(sc.specs, sc.pairs)
    assert runner.n_sub == {"s_fine": 1, "s_med": 2, "s_coarse": 4}
    assert runner.cycle == 4
    assert runner.dt_micro == 0.25


def test_sync_flag_only_at_cycle_boundary(scenarios):
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    flags = []
    for _ in range(runner.cycle):
        flags.append(runner.at_sync())
        runner.micro_step_once()
    assert flags[0] is True
    assert all(not f for f in flags[1:])
    assert runner.at_sync() is True


def test_lockstep_has_no_off_sync_point(lockstep):
    out = C.unsync_verdict(lockstep, n_micro=40)
    assert out["applicable"] is False
    assert out["cycle"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# AC1 位级可复现（3 档场景）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key", ["pair_2way", "chain_3way", "all_to_all_3way"])
def test_ac1_bitwise_reproducible(scenarios, key, tmp_path):
    res = C.restart_verdict(scenarios[key], n_micro=400, tmpdir=str(tmp_path))
    assert res.at_sync is True
    assert res.bitwise_equal is True, (
        f"{key}: 重启摘要 {res.digest_resumed} != 连续摘要 {res.digest_continuous}"
    )


def test_ac1_determinism_two_runs(scenarios):
    a = C.run_continuous(scenarios["chain_3way"], 200)
    b = C.run_continuous(scenarios["chain_3way"], 200)
    assert a["digest"] == b["digest"]


def test_ac1_repeated_restarts_bitwise(scenarios, tmp_path):
    """连续三次 checkpoint/restart（每 100 微步一次）仍与不中断运行位级一致。"""
    sc = scenarios["chain_3way"]
    continuous = C.run_continuous(sc, 400)
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    led = C.EnergyLedger(runner)
    store = C.ckpt_mod.CheckpointStore(str(tmp_path / "ck"))
    for _ in range(3):
        runner.advance(100, led)
        man = C.save_runner(store, runner)
        runner = C.load_runner(store, man["step"], specs=sc.specs, pairs=sc.pairs, strict_sync=True)
    runner.advance(100, led)
    assert runner.micro_step == 400
    assert runner.digest() == continuous["digest"]
    reports = store.verify_all()
    assert len(reports) == 3 and all(r["ok"] for r in reports)


def test_state_digest_is_order_independent(scenarios):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    runner.advance(10)
    arrays = runner.arrays()
    reversed_arrays = {k: arrays[k] for k in reversed(list(arrays))}
    assert C.state_digest(arrays, runner.scalars()) == C.state_digest(reversed_arrays, runner.scalars())


# ═══════════════════════════════════════════════════════════════════════════
# AC2 能量账本
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key", ["pair_2way", "chain_3way", "all_to_all_3way"])
def test_ac2a_checkpoint_energy_residual_below_1e10(scenarios, key, tmp_path):
    res = C.restart_verdict(scenarios[key], n_micro=400, tmpdir=str(tmp_path))
    assert res.e_residual < 1e-10
    assert res.e_loaded == res.e_at_save  # 位级相同 ⇒ 残差恰为 0


@pytest.mark.parametrize("key", ["pair_2way", "chain_3way", "all_to_all_3way"])
def test_ac2b_energy_drift_below_0_1_percent(scenarios, key, tmp_path):
    res = C.restart_verdict(scenarios[key], n_micro=1000, tmpdir=str(tmp_path))
    assert abs(res.e_drift_after_restart) < 1e-3
    # 连续性：重启段漂移与不中断段漂移量级一致（不存在「重启引入能量」）
    assert abs(res.e_drift_after_restart - res.e_drift_continuous) < 1e-9


def test_energy_ledger_records_every_step(scenarios):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    led = C.EnergyLedger(runner)
    runner.advance(50, led)
    assert len(led.rows) == 51
    assert led.at(50) is not None and led.at(51) is None
    assert led.max_abs_rel_drift() < 1e-3


def test_energy_ledger_missing_step_raises(scenarios):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    led = C.EnergyLedger(runner)
    runner.advance(5, led)
    with pytest.raises(C.LedgerMismatch):
        led.drift_between(0, 99)


# ═══════════════════════════════════════════════════════════════════════════
# AC3 时间同步点裁决
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key", ["pair_2way", "chain_3way", "all_to_all_3way"])
def test_ac3_strict_refuses_off_sync_checkpoint(scenarios, key, tmp_path):
    out = C.unsync_verdict(scenarios[key], n_micro=200, tmpdir=str(tmp_path))
    assert out["ckpt_at_sync"] is False
    assert out["strict_refused_full_schema"] is True
    assert out["strict_refused_naive_schema"] is True


@pytest.mark.parametrize("key", ["pair_2way", "chain_3way", "all_to_all_3way"])
def test_ac3_lenient_off_sync_resume_diverges(scenarios, key, tmp_path):
    out = C.unsync_verdict(scenarios[key], n_micro=200, tmpdir=str(tmp_path))
    assert out["lenient_diverges"] is True
    # 对照：同一 lenient 路径从同步点续跑必须位级一致 ⇒ 发散归因于相位丢失，而非代码路径
    assert out["control_matches_continuous"] is True


def test_ac3_missing_sync_block_raises_specific_error(scenarios, tmp_path):
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    runner.advance(4)
    store = C.ckpt_mod.CheckpointStore(str(tmp_path / "ck2"))
    man = C.save_runner(store, runner, with_sync_block=False)
    with pytest.raises(C.SyncPointViolation):
        C.load_runner(store, man["step"], specs=sc.specs, pairs=sc.pairs, strict_sync=True)


def test_ac3_pending_half_kick_is_recorded(scenarios):
    """非同步点上「悬挂耦合半踢」必须体现在 leg 标志里。"""
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    runner.advance(2)  # cycle=4 的中点
    assert runner.leg == 1
    assert runner.sync_block()["at_sync"] is False
    runner.advance(2)
    assert runner.leg == 0 and runner.at_sync() is True


def test_load_runner_rejects_missing_arrays(scenarios, tmp_path):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    store = C.ckpt_mod.CheckpointStore(str(tmp_path / "ck3"))
    arrays = {k: v for k, v in runner.arrays().items() if not k.startswith("s_peer")}
    man = store.save(0, arrays, runner.scalars())
    with pytest.raises(C.RestartError):
        C.load_runner(store, man["step"], specs=sc.specs, pairs=sc.pairs, strict_sync=True)


# ═══════════════════════════════════════════════════════════════════════════
# AC4 压缩无损
# ═══════════════════════════════════════════════════════════════════════════
def test_ac4_zstd_roundtrip_lossless(scenarios):
    sc = scenarios["chain_3way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    runner.advance(20)
    out = C.compression_roundtrip(runner.arrays(), "zstd")
    assert out["codec"] == "zstd"
    assert out["all_lossless"] is True
    assert out["state_digest_equal"] is True


def test_ac4_lzma_fallback_lossless(scenarios):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    out = C.compression_roundtrip(runner.arrays(), "lzma")
    assert out["all_lossless"] is True and out["state_digest_equal"] is True


def test_ac4_corruption_detected(scenarios):
    sc = scenarios["pair_2way"]
    runner = C.CoupledRunner.build(sc.specs, sc.pairs)
    raw = runner.arrays()["s_fine.q"].tobytes()
    packed = C.compress_bytes(raw, "zstd")
    assert C.corruption_detected(packed, "zstd")["detected"] is True


def test_codec_list_always_has_fallback():
    codecs = C.compression_codecs()
    assert "lzma" in codecs
    assert codecs[0] in ("zstd", "zstd-py", "lzma")


# ═══════════════════════════════════════════════════════════════════════════
# 端到端证据包：AC1–AC4 全绿
# ═══════════════════════════════════════════════════════════════════════════
def test_build_evidence_all_ac_green():
    ev = C.build_evidence(n_micro=200)
    assert ev["ac"] == {
        "AC1_bitwise_reproducible": True,
        "AC2a_energy_residual": True,
        "AC2b_energy_drift": True,
        "AC3_sync_point_adjudication": True,
        "AC4_compression_lossless": True,
    }
    assert ev["totals"]["max_e_residual"] < 1e-10
    assert ev["totals"]["max_abs_e_drift"] < 1e-3
    assert len(ev["scenarios"]) == 3