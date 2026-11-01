"""几何/静水力域验证工具的自检与变异体测试.

原则: 先证「验证器本身」可靠（解析闭式解 + 变异体必须判负），再谈平台判决。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.verifier.geometry_audit import (  # noqa: E402
    analytic_selfcheck, evaluate, integrate_table, load_snapshot, simpson,
)

SNAP = os.path.join(ROOT, "artifacts", "geometry_audit", "raw_probe.json")


# ── ① 积分内核: Simpson 精确到三次多项式 ──
def test_simpson_exact_for_cubic():
    h = 0.05
    xs = [i * h for i in range(21)]          # 0..1
    y = [1.0 + 2 * x - 3 * x ** 2 + 4 * x ** 3 for x in xs]
    want = 1.0 + 2 * 0.5 - 3 / 3 + 4 * 0.25  # ∫0^1 f dx = 1+1-1+1 = 2
    assert want == pytest.approx(2.0)
    assert simpson(y, h) == pytest.approx(want, rel=1e-12)


def test_simpson_even_points_falls_back_to_trapezoid_tail():
    y = [0.0, 1.0, 4.0, 9.0]        # 4 点 → 末段梯形
    got = simpson(y, 1.0)
    assert got == pytest.approx((1 / 3) * (0 + 4 * 1 + 4) + 0.5 * (4 + 9))


# ── ② 解析闭式解自检（箱型驳 / Wigley / 棱柱驳） ──
def test_analytic_selfcheck_all_pass():
    sc = analytic_selfcheck()
    bad = [c for c in sc["cases"] if not c["pass"]]
    assert sc["pass"], f"解析自检未过: {bad}"


def test_box_barge_cb_is_one_by_definition():
    g = [[1.0] * 11 for _ in range(21)]
    r = integrate_table(g)
    assert r["Cb"] == pytest.approx(1.0, rel=1e-12)
    assert r["Cp"] == pytest.approx(1.0, rel=1e-12)


def test_wigley_cb_closed_form_four_ninths_when_waterline_at_deck():
    # u=(1-(2ξ-1)^2)(1-ζ^2): Cb=(2/3)(2/3)=4/9, Cm=Cp=2/3
    g = [[(1 - (2 * i / 20 - 1) ** 2) * (1 - (j / 10) ** 2) for j in range(11)] for i in range(21)]
    r = integrate_table(g)
    assert r["Cb"] == pytest.approx(4 / 9, rel=1e-9)
    assert r["Cm"] == pytest.approx(2 / 3, rel=1e-9)
    assert r["Cp"] == pytest.approx(2 / 3, rel=1e-9)


# ── ③ 门禁变异体: 每道门必须能判负 ──
def _snap(cb, volumes, verify_cb=None, verify_stations=0, verify_max=None):
    """构造最小快照: 只影像门禁关心的字段。"""
    drafts = {str(round(f * 10.8, 4)): {"ok": True, "data": {"hydrostatics": {
        "Cb": cb, "displacement_volume": v, "Awp": 1.0, "LCB": 1.0,
        "LCB_from_fp": 1.0, "VCB": 1.0, "draft": 10.8}}} for f, v in volumes.items()}
    return {
        "api": "fixture", "ts": "fixture",
        "probes": {"KCS": {
            "geometry": {"ok": True, "data": {"geometry": {"L": 230.0, "B": 32.2, "T": 10.8, "Cb": 0.651}}},
            "verify": {"ok": True, "data": {"ship": "KCS", "stations_checked": verify_stations,
                                            "avg_error_pct": 0.0, "max_error_pct": verify_max}},
            "offsets": {"ok": True, "data": {"table": [[1.0] * 11 for _ in range(21)]}},
        }},
        "draft_sweep": {"KCS": drafts},
    }


def test_g1_flags_cb_above_one():
    res = evaluate(_snap(cb=1.1949, volumes={0.2: 1.0, 0.5: 2.0, 1.0: 4.0}))
    assert res["gates"]["G1_Cb_physical"]["pass"] is False
    assert res["verdict"]["evidence_level"] == "MOCK"


def test_g4_flags_frozen_volume():
    res = evaluate(_snap(cb=0.65, volumes={0.2: 4.0, 0.5: 4.0, 1.0: 4.0}, verify_stations=4, verify_max=1.0))
    assert res["gates"]["G4_draft_parameter_live"]["pass"] is False


def test_g6_flags_vacuous_self_verify():
    res = evaluate(_snap(cb=0.65, volumes={0.2: 1.0, 0.5: 2.0, 1.0: 4.0}, verify_stations=0))
    assert res["gates"]["G6_self_verify_nonvacuous"]["pass"] is False


def test_g6_flags_large_reference_error():
    res = evaluate(_snap(cb=0.65, volumes={0.2: 1.0, 0.5: 2.0, 1.0: 4.0},
                         verify_stations=4, verify_max=141.1))
    assert res["gates"]["G6_self_verify_nonvacuous"]["pass"] is False


def test_healthy_fixture_passes_physical_and_consistency_gates():
    """箱型驳型值表 (Cb=1, Cm=Cp=1) + 服务端 Cb=1 → 物理/恒等式/一致性门应全过。"""
    res = evaluate(_snap(cb=1.0, volumes={0.2: 1.0, 0.5: 2.5, 1.0: 5.0},
                         verify_stations=5, verify_max=0.5))
    for gate in ("G1_Cb_physical", "G5_identity_Cb_eq_Cm_Cp", "G7_offsets_vs_served_Cb",
                 "G4_draft_parameter_live", "G6_self_verify_nonvacuous"):
        assert res["gates"][gate]["pass"] is True, gate


# ── ④ 快照回放（无网络也能复现判决；快照缺失则跳过） ──
@pytest.mark.skipif(not os.path.exists(SNAP), reason="快照未生成: 先跑 geometry_audit.py --write")
def test_snapshot_replay_reproduces_verdict():
    res = evaluate(load_snapshot())
    assert res["analytic_selfcheck"]["pass"] is True
    failed = set(res["verdict"]["gates_failed"])
    assert {"G1_Cb_physical", "G3_served_Cb_vs_literature", "G7_offsets_vs_served_Cb"} <= failed
    kcs = next(m for m in res["models"] if m["model"] == "KCS")
    assert kcs["api_hydro"]["Cb"] > 1.0          # 物理不可容许
    assert kcs["api_hydro"]["displacement_volume"] == pytest.approx(95574.7, rel=1e-6)