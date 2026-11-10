"""多物理场耦合守恒契约测试框架的验收测试 (p307w1-n04 / m9997).

运行::

    cd backend && python -m pytest tests/test_coupling_conservation.py -q

覆盖机器判据
------------
(a) 量纲检查: 注入量纲不匹配 → 100% 检出 (含"同量纲不同守恒量类别"这一
    量纲检查的盲区, 由 QUANTITY_KIND 兜住)
(b) 守恒验证: 精确守恒的解析界面 → 残差 == 0 (远优于 1e-10)
(c) 守恒违规检出: 注入相对泄漏 → 100% 检出且残差与注入量一致
(d) 时钟语义: 注入时间不同步 → 检出并报出时间差 (精确有理数)
(e) 组合验证: A→B→C 链式残差 == A→C 直达残差 (误差 0 < 1e-12);
    非守恒中间界面必被量化报出
(f) 零领域语义: 本包源码对 config/domain_word_denylist.json 零命中
(g) 用例数 ≥ 20 (本文件)

所有合成命名 (A/B/C, kg/s, J/s, N·m) 都是通用量纲示例, 不指向任何具体学科。
"""
from __future__ import annotations

import importlib.util
import json
import random
from fractions import Fraction
from pathlib import Path

import pytest

from services.coupling_contracts.dimension import DimensionError, dim, format_dim
from services.coupling_conservation import (
    EXPLICIT,
    IMPLICIT,
    SUBSTEP,
    AcceptanceReport,
    ClockEvent,
    CouplingChain,
    Criterion,
    ExchangePort,
    FluxLedger,
    FluxSample,
    INWARD,
    OUTWARD,
    TransferLaw,
    abs_error,
    acceptance_gate,
    additivity_report,
    adjudicate,
    as_fraction,
    check_balance,
    check_exchange,
    check_exchange_set,
    check_transitivity,
    load_report,
    port_from_dict,
    rel_residual,
    split_conserved,
    substep_schedule,
)

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "backend" / "services" / "coupling_conservation"
DENYLIST = ROOT / "config" / "domain_word_denylist.json"

MASS_FLOW = dim(M=1, T=-1)            # kg/s
ENERGY_FLOW = dim(M=1, L=2, T=-3)     # W
TORQUE = dim(M=1, L=2, T=-2)          # N·m  (与能量同量纲!)
ENERGY = dim(M=1, L=2, T=-2)          # J


def _pair(dim_in=MASS_FLOW, **over):
    """构造一对干净的 producer/consumer 端口 (默认应通过)."""
    p = dict(name="p", role="producer", quantity="mass_flow", dim=dim_in,
             unit="kg/s", orientation=OUTWARD)
    c = dict(name="c", role="consumer", quantity="mass_flow", dim=dim_in,
             unit="kg/s", orientation=INWARD)
    p.update(over.get("producer", {}))
    c.update(over.get("consumer", {}))
    return ExchangePort(**p), ExchangePort(**c)


# ---------------------------------------------------------------------------
# flux: 精确台账与残余算术
# ---------------------------------------------------------------------------
def test_exact_interface_balance_zero_residual():
    samples = [
        FluxSample("mass_flow", MASS_FLOW, Fraction(1, 3), "producer", "kg/s"),
        FluxSample("mass_flow", MASS_FLOW, Fraction(1, 3), "producer", "kg/s"),
        FluxSample("mass_flow", MASS_FLOW, Fraction(1, 3), "producer", "kg/s"),
        FluxSample("mass_flow", MASS_FLOW, Fraction(-1), "consumer", "kg/s"),
    ]
    b = check_balance(samples)
    assert b.net == 0 and b.rel_residual == 0 and b.passed
    assert b.abs_sum == 2


def test_balance_quantifies_injected_leak():
    leak = Fraction(1, 10 ** 9)
    samples = [
        FluxSample("mass_flow", MASS_FLOW, Fraction(1), "producer"),
        FluxSample("mass_flow", MASS_FLOW, -(Fraction(1) - leak), "consumer"),
    ]
    b = check_balance(samples, tolerance=Fraction(1, 10 ** 10))
    expected = leak / (Fraction(2) - leak)
    assert b.rel_residual == expected          # 精确, 无浮点尾数
    assert not b.passed
    assert b.excess > 4                         # 超差倍数可量化


def test_balance_rejects_mixed_quantities():
    with pytest.raises(ValueError):
        check_balance([
            FluxSample("mass_flow", MASS_FLOW, 1, "a"),
            FluxSample("energy_flow", ENERGY_FLOW, -1, "b"),
        ])


def test_balance_rejects_mixed_dimensions():
    with pytest.raises(DimensionError):
        check_balance([
            FluxSample("q", MASS_FLOW, 1, "a"),
            FluxSample("q", ENERGY_FLOW, -1, "b"),
        ])


def test_as_fraction_uses_decimal_literal():
    assert as_fraction(0.1) == Fraction(1, 10)
    assert as_fraction("1/3") == Fraction(1, 3)
    assert as_fraction(3) == Fraction(3)
    with pytest.raises(TypeError):
        as_fraction(True)


def test_rel_residual_edge_cases():
    assert rel_residual(0, 0) == 0
    assert rel_residual(0, 5) == 1
    assert abs_error(1, Fraction(99, 100)) == Fraction(1, 100)


def test_split_conserved_is_exact_and_validated():
    parts = split_conserved(1, (Fraction(1, 3),) * 3)
    assert sum(parts) == 1
    with pytest.raises(ValueError):
        split_conserved(1, (Fraction(1, 3), Fraction(1, 3)))


def test_flux_ledger_accumulates_and_flags_drift():
    good = FluxLedger("mass_flow", MASS_FLOW)
    bad = FluxLedger("mass_flow", MASS_FLOW)
    for _ in range(5):
        good.record([
            FluxSample("mass_flow", MASS_FLOW, Fraction(1), "in"),
            FluxSample("mass_flow", MASS_FLOW, Fraction(-1), "out"),
        ])
        bad.record([
            FluxSample("mass_flow", MASS_FLOW, Fraction(1), "in"),
            FluxSample("mass_flow", MASS_FLOW, -(Fraction(999, 1000)), "out"),
        ])
    assert good.cumulative_rel_residual == 0 and good.all_passed
    assert not bad.all_passed and bad.cumulative_rel_residual > Fraction(1, 10000)


# ---------------------------------------------------------------------------
# exchange: 场交换契约检查 (判据 a)
# ---------------------------------------------------------------------------
def test_clean_exchange_passes():
    p, c = _pair()
    r = check_exchange(p, c)
    assert r.passed, r.to_dict()


def test_dimension_mismatch_detected_with_delta():
    p, c = _pair(dim_in=MASS_FLOW, consumer={"dim": ENERGY_FLOW})
    r = check_exchange(p, c)
    assert not r.passed
    assert "DIM_MISMATCH" in r.codes
    delta = r.violations[0].detail["delta"]
    assert delta[1] == 2 and delta[2] == -2      # L+2 T-2
    assert "L+2" in r.violations[0].detail["delta_text"]


def test_same_dim_different_quantity_kind_detected():
    """量纲检查的盲区: 力矩与能量同为 M L² T⁻², 但守恒量类别不同。"""
    p = ExchangePort("p", "producer", "torque", TORQUE, "N*m", 0, None, OUTWARD)
    c = ExchangePort("c", "consumer", "energy", ENERGY, "J", 0, None, INWARD)
    r = check_exchange(p, c)
    assert not r.passed
    assert r.codes == ("QUANTITY_KIND",)
    assert r.violations[0].detail["same_dim"] is True


def test_tensor_order_and_missing_frame_detected():
    vec = dim(M=1, L=1, T=-2)     # 力, 一阶张量
    p, c = _pair(dim_in=vec, producer={"tensor_order": 1}, consumer={"tensor_order": 0})
    r = check_exchange(p, c)
    assert "TENSOR_ORDER" in r.codes
    assert "FRAME_MISSING" in r.codes


def test_frame_mismatch_only_when_transform_missing():
    vec = dim(M=1, L=1, T=-2)
    p, c = _pair(
        dim_in=vec,
        producer={"tensor_order": 1, "frame": "frame_a", "unit": ""},
        consumer={"tensor_order": 1, "frame": "frame_b", "unit": ""},
    )
    assert check_exchange(p, c, frames_connected=True).passed
    r = check_exchange(p, c, frames_connected=False)
    assert "FRAME_MISMATCH" in r.codes


def test_sign_convention_detected():
    p, c = _pair(consumer={"orientation": OUTWARD})
    r = check_exchange(p, c)
    assert "SIGN_CONVENTION" in r.codes


def test_unit_inconsistent_detected():
    p, c = _pair(consumer={"unit": "W"})           # 声明 kg/s 却写 W
    r = check_exchange(p, c)
    assert "UNIT_INCONSISTENT" in r.codes
    p2, c2 = _pair(producer={"unit": "furlong/s"})  # 无法解析
    assert "UNIT_INCONSISTENT" in check_exchange(p2, c2).codes


def test_exchange_batch_summary():
    good = _pair()
    bad = _pair(consumer={"dim": ENERGY_FLOW})
    summary = check_exchange_set([good, bad])
    assert summary["n_checks"] == 2 and summary["n_passed"] == 1
    assert not summary["passed"] and summary["n_failed"] == 1


def test_port_from_dict_parses_declared_dim():
    port = port_from_dict(
        {"name": "q", "role": "producer", "quantity": "mass_flow", "dim": "M·T^-1",
         "unit": "kg/s", "orientation": OUTWARD}
    )
    assert port.dim == MASS_FLOW and port.unit == "kg/s"
    payload = {"name": "x", "role": "consumer", "quantity": "q", "dim": [1, 0, -1, 0, 0, 0, 0]}
    assert port_from_dict(payload).dim == MASS_FLOW


def test_randomized_dimension_mismatch_detection_rate_is_one():
    """判据 (a): 200 组随机端口对, 注入量纲不匹配 → 检出率必须 == 100%。

    同时做正向对照 (量纲相同 → 不报 DIM_MISMATCH), 防"一律报错"的假绿。
    """
    rng = random.Random(20261110)
    n_mismatch, detected, n_match, false_alarm = 0, 0, 0, 0
    for _ in range(200):
        base = [rng.randint(-2, 2) for _ in range(7)]
        p_dim = tuple(base)
        if rng.random() < 0.5:
            idx = rng.randrange(7)
            base[idx] += rng.choice([-1, 1])
            c_dim = tuple(base)
        else:
            c_dim = p_dim
        p = ExchangePort("p", "producer", "q", p_dim, "", 0, None, OUTWARD)
        c = ExchangePort("c", "consumer", "q", c_dim, "", 0, None, INWARD)
        hit = "DIM_MISMATCH" in check_exchange(p, c).codes
        if p_dim != c_dim:
            n_mismatch += 1
            detected += int(hit)
        else:
            n_match += 1
            false_alarm += int(hit)
    assert n_mismatch > 50 and detected == n_mismatch
    assert false_alarm == 0


# ---------------------------------------------------------------------------
# clock: 时钟语义裁决 (判据 d)
# ---------------------------------------------------------------------------
def _ev(**over):
    base = dict(producer="A", consumer="B", field="f", t_write=Fraction(0),
                window=(Fraction(0), Fraction(1)), t_read=Fraction(0))
    base.update(over)
    return ClockEvent(**base)


def test_explicit_synchronized_passes():
    r = adjudicate([_ev(t_write=Fraction(0))], EXPLICIT)
    assert r.passed and r.max_time_gap == 0


def test_time_travel_detected_and_gap_reported():
    r = adjudicate([_ev(t_write=Fraction(1, 10), window=(Fraction(0), Fraction(1)),
                        t_read=Fraction(0))], EXPLICIT)
    assert not r.passed and "TIME_TRAVEL" in r.codes
    assert r.max_time_gap == Fraction(1, 10)


def test_explicit_late_data_out_of_window():
    r = adjudicate([_ev(t_write=Fraction(12, 100), window=(Fraction(0), Fraction(1, 10)),
                        t_read=Fraction(12, 100))], EXPLICIT)
    assert not r.passed and "OUT_OF_WINDOW" in r.codes
    assert r.max_time_gap == Fraction(12, 100)      # = t_write - window.start


def test_substep_alignment_and_misalignment():
    sched = substep_schedule((Fraction(0), Fraction(1, 10)), Fraction(1, 100))
    assert len(sched) == 11 and sched[3] == Fraction(3, 100)
    events = [_ev(t_write=t, window=(Fraction(0), Fraction(1, 10)),
                  t_read=Fraction(1, 10), dt_fine=Fraction(1, 100)) for t in sched[:-1]]
    assert adjudicate(events, SUBSTEP).passed

    bad = _ev(t_write=Fraction(25, 1000), window=(Fraction(0), Fraction(1, 10)),
              t_read=Fraction(1, 10), dt_fine=Fraction(1, 100))
    r = adjudicate([bad], SUBSTEP)
    assert "UNALIGNED" in r.codes and r.max_time_gap == Fraction(5, 1000)


def test_substep_schedule_rejects_non_dividing_dt():
    with pytest.raises(ValueError):
        substep_schedule((Fraction(0), Fraction(1, 10)), Fraction(3, 100))


def test_data_race_detected_with_overlap():
    a = _ev(producer="A", window=(Fraction(0), Fraction(1, 2)), t_write=Fraction(0))
    b = _ev(producer="C", window=(Fraction(1, 4), Fraction(3, 4)), t_write=Fraction(1, 4))
    r = adjudicate([a, b], IMPLICIT)
    assert "DATA_RACE" in r.codes and r.max_time_gap == Fraction(1, 4)
    same = adjudicate([a, a], IMPLICIT)
    assert "DATA_RACE" not in same.codes


def test_iteration_regression_detected():
    e1 = _ev(t_write=Fraction(1, 2), window=(Fraction(0), Fraction(1)), iteration=2)
    e2 = _ev(t_write=Fraction(1, 2), window=(Fraction(0), Fraction(1)), iteration=1)
    r = adjudicate([e1, e2], IMPLICIT)
    assert "ITERATION_REGRESSION" in r.codes


def test_implicit_window_overflow_detected():
    r = adjudicate([_ev(t_write=Fraction(3, 2), window=(Fraction(0), Fraction(1)),
                        t_read=Fraction(3, 2))], IMPLICIT)
    assert "OUT_OF_WINDOW" in r.codes and r.max_time_gap == Fraction(1, 2)


# ---------------------------------------------------------------------------
# composition: 组合验证与传递性 (判据 e)
# ---------------------------------------------------------------------------
def _chain(leak=Fraction(0), weight_sum_ok=True):
    ch = CouplingChain(nodes=("A", "B", "C"))
    ch.add_law(TransferLaw("A", "B", "mass_flow", MASS_FLOW, MASS_FLOW,
                           unit_scale=Fraction(1, 1000),
                           weights=(Fraction(1, 3),) * 3))
    ch.add_law(TransferLaw("B", "C", "mass_flow", MASS_FLOW, MASS_FLOW,
                           weights=(Fraction(1, 2), Fraction(1, 2)), loss=leak))
    return ch


def _direct():
    """A→C 直达路径: 只做单位换算 (1000 g/s -> 1 kg/s), 不带任何损失。"""
    return TransferLaw("A", "C", "mass_flow", MASS_FLOW, MASS_FLOW,
                       unit_scale=Fraction(1, 1000))


def test_chain_transitivity_exact():
    ch = _chain()
    res = check_transitivity(ch, upstream="A", downstream="C",
                             conserved_value=1000, measured_downstream=1,
                             direct_law=_direct())
    assert res.direct_residual == 0 and res.chain_residual == 0
    assert res.mismatch == 0 and res.passed
    assert res.path == ("A", "B", "C")


def test_chain_transitivity_with_measured_direct_residual():
    """直达残差非零时, 链式残差必须与它相等 (差 < 1e-12)。"""
    ch = _chain()
    measured = Fraction(1) - Fraction(1, 10 ** 6)
    res = check_transitivity(ch, upstream="A", downstream="C",
                             conserved_value=1000, measured_downstream=measured,
                             direct_law=_direct())
    assert res.direct_residual == res.chain_residual
    assert res.mismatch == 0 and res.mismatch < Fraction(1, 10 ** 12)
    assert res.transitive
    # 1e-6 的相对失配超过守恒容差 1e-10: 传递性成立但守恒必须判负 (honest FAIL)
    assert not res.conservation_ok and not res.passed

    tiny = check_transitivity(ch, upstream="A", downstream="C",
                              conserved_value=1000,
                              measured_downstream=Fraction(1) - Fraction(1, 10 ** 14),
                              direct_law=_direct())
    assert tiny.passed and tiny.direct_residual <= Fraction(1, 10 ** 10)


def test_chain_detects_non_conservative_intermediate():
    ch = _chain(leak=Fraction(1, 10 ** 6))
    res = check_transitivity(ch, upstream="A", downstream="C",
                             conserved_value=1000, measured_downstream=1,
                             direct_law=_direct())
    assert not res.passed and not res.transitive
    assert res.mismatch == Fraction(1, 10 ** 6)      # 与注入损失精确相等


def test_chain_dim_discontinuity_reported():
    ch = CouplingChain(nodes=("A", "B", "C"))
    ch.add_law(TransferLaw("A", "B", "q", MASS_FLOW, ENERGY_FLOW))
    ch.add_law(TransferLaw("B", "C", "q", MASS_FLOW, MASS_FLOW))
    problems = ch.check_dims()
    assert problems and "量纲" in problems[0]


def test_chain_rejects_missing_edge_and_backward_path():
    ch = _chain()
    with pytest.raises(ValueError):
        ch.propagate("C", "A", 1)
    with pytest.raises(KeyError):
        CouplingChain(nodes=("A", "B")).propagate("A", "B", 1)


def test_transfer_law_rejects_empty_weights():
    with pytest.raises(ValueError):
        TransferLaw("A", "B", "q", MASS_FLOW, MASS_FLOW, weights=())


def test_additivity_report_consistent_and_inconsistent():
    ok = additivity_report({"total": Fraction(1, 100), "a": Fraction(3, 1000),
                            "b": Fraction(7, 1000)},
                           total_key="total", partial_keys=["a", "b"])
    assert ok["consistent"] and ok["diff"] == 0
    bad = additivity_report({"total": Fraction(2, 100), "a": Fraction(3, 1000),
                             "b": Fraction(7, 1000)},
                            total_key="total", partial_keys=["a", "b"])
    assert not bad["consistent"]


# ---------------------------------------------------------------------------
# report + 零领域语义 (判据 f)
# ---------------------------------------------------------------------------
def test_acceptance_report_roundtrip_and_gate(tmp_path):
    rep = AcceptanceReport(mission_id="9997")
    rep.add(Criterion("a", "dim", True, {"rate": 1.0}))
    rep.add(Criterion("b", "cons", True, {"residual": 0.0}))
    assert rep.passed
    path = rep.write(tmp_path / "acc.json")
    loaded = load_report(path)
    assert loaded["passed"] and loaded["n_criteria"] == 2
    assert acceptance_gate(loaded, ["a", "b"])["passed"]
    gate = acceptance_gate(loaded, ["a", "b", "e"])
    assert not gate["passed"] and gate["missing"] == ["e"]


def test_report_fails_when_any_criterion_fails():
    rep = AcceptanceReport(mission_id="9997")
    rep.add(Criterion("c", "leak", False, {"detected": False}))
    assert not rep.passed and rep.failed_ids == ["c"]
    assert "FAIL" in rep.summary_table()


def test_framework_has_zero_domain_semantics():
    """判据 (f): 包内源码对平台域词表 (数据注入) 零命中。"""
    assert DENYLIST.exists(), "域词表文件缺失, 无法做零领域语义扫描"
    spec = importlib.util.spec_from_file_location(
        "domain_leak_scan", ROOT / "tools" / "audit" / "domain_leak_scan.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rel_pkg = PKG.relative_to(ROOT)
    result = mod.scan([rel_pkg], denylist_path=DENYLIST, root=ROOT)
    assert result["hit_count"] == 0, json.dumps(result["hits"], ensure_ascii=False)
    assert len(result["files_scanned"]) >= 5