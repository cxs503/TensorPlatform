"""耐波性域复核判据的离线回归测试（无网络，确定性重放）。

用录制好的 ``artifacts/m1515/seakeeping_audit/probes.json`` 重放 ``judge()``，
锁定判决与关键数字；任何判据逻辑被无意改动都会在此失败。
另对解析基线函数做量纲/量级自检。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "seakeeping_audit"))

import audit_seakeeping as aud  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1515", "seakeeping_audit")


@pytest.fixture(scope="module")
def probes():
    path = os.path.join(ART, "probes.json")
    if not os.path.exists(path):
        pytest.skip("probes.json 未生成（需先跑 audit_seakeeping.py）")
    return json.load(open(path))


@pytest.fixture(scope="module")
def verdict(probes):
    return aud.judge(probes)


def test_replay_matches_recorded_verdict(probes, verdict):
    """重放判决须与落盘 verdict.json 完全一致（12 条判据逐一）。"""
    rec = json.load(open(os.path.join(ART, "verdict.json")))
    got = {c["id"]: c["verdict"] for c in verdict["criteria"]}
    exp = {c["id"]: c["verdict"] for c in rec["criteria"]}
    assert got == exp
    assert verdict["summary"] == rec["summary"]


def test_verdict_is_mock_and_all_criteria_present(verdict):
    ids = [c["id"] for c in verdict["criteria"]]
    assert ids == [f"C{i}" for i in range(1, 12)]
    assert verdict["summary"]["grade"] == "MOCK"
    assert verdict["summary"]["fail"] >= 4      # MOCK 判据门槛


def test_optional_inputs_ignored(verdict):
    """C1 指纹：改 wave_frequency/heading 后曲线逐点相同。"""
    c1 = {c["id"]: c for c in verdict["criteria"]}["C1"]
    assert c1["verdict"] == "FAIL"
    assert len(c1["evidence"]["identical_pairs"]) == 12
    assert all(v == 0.0 for v in c1["evidence"]["max_rel_diff"].values())


def test_heave_equals_pitch_degeneracy(verdict):
    """C2 指纹：垂荡与纵摇 RAO 逐点恒等（自由度退化）。"""
    c2 = {c["id"]: c for c in verdict["criteria"]}["C2"]
    assert c2["verdict"] == "FAIL"
    assert set(c2["evidence"]["identical_ships"]) == {"KVLCC2", "S175", "BARGE60"}
    assert all(v == 0.0 for v in c2["evidence"]["max_rel_diff"].values())


def test_motion_amplitudes_hardcoded(verdict):
    """C6 指纹：/motion 幅值对任意输入恒为 1.5/3.0/8.0。"""
    c6 = {c["id"]: c for c in verdict["criteria"]}["C6"]
    assert c6["verdict"] == "FAIL"
    for amps in c6["evidence"]["amplitudes"].values():
        for a in amps.values():
            assert a == {"heave": 1.5, "pitch": 3.0, "roll": 8.0}


def test_heave_period_below_physical_lower_bound(verdict):
    """C8：自报 T33 低于无附加质量下界（等价 A33=0，非物理）。"""
    c8 = {c["id"]: c for c in verdict["criteria"]}["C8"]
    assert c8["verdict"] == "FAIL"
    for name, e in c8["evidence"]["cases"].items():
        assert e["ratio"] < 1.0, name


def test_strip_added_mass_scale_off_by_three_orders(verdict):
    """C9：strip 自报 A33/m ≈ 8.5e-4（物理量级 0.5~1.5），且与自报 T33 不自洽。"""
    c9 = {c["id"]: c for c in verdict["criteria"]}["C9"]
    assert c9["verdict"] == "FAIL"
    for e in c9["evidence"]["cases"].values():
        assert e["A33_over_mass"] < 1e-2
        assert e["inconsistency_pct"] > 5.0


def test_cross_endpoint_pitch_period_discrepancy(verdict):
    """C11：纵摇自然周期两端点相差一个量级。"""
    c11 = {c["id"]: c for c in verdict["criteria"]}["C11"]
    assert c11["verdict"] == "FAIL"
    pitch = c11["evidence"]["cases"]["KVLCC2"]["pitch"]
    assert pitch["ratio"] > 5.0


def test_long_wave_criterion_honestly_not_applicable(verdict):
    """C4：不适用算例必须标 applicable=False，不得虚报通过。"""
    c4 = {c["id"]: c for c in verdict["criteria"]}["C4"]
    assert c4["evidence"]["cases"]["KVLCC2"]["applicable"] is False
    assert c4["evidence"]["cases"]["BARGE60"]["applicable"] is True


# ---------------------------------------------------------------- 解析基线自检
def test_dispersion_and_encounter_frequency():
    w = 0.6
    assert aud.k_deep(w) == pytest.approx(w * w / 9.80665)
    # 顶浪 β=180°：ωe = ω + kU
    we = aud.encounter(w, 7.0, 180.0)
    assert we == pytest.approx(w + aud.k_deep(w) * 7.0)
    # 顺浪 β=0°：ωe = |ω - kU|，且 ω 很小时随浪追上 ⇒ ωe→0
    assert aud.encounter(0.2, 7.0, 0.0) == pytest.approx(
        abs(0.2 - aud.k_deep(0.2) * 7.0))


def test_reference_periods_units_and_magnitude():
    """单位自检：吨→m³ 用 ρ=1.025 t/m³；KVLCC2 三周期落在工程量级内。"""
    s = aud.SHIPS["KVLCC2"]
    r = aud.ref_periods(s)
    assert 11.0 < r["T_heave"] < 12.5      # A33/m=0.8 时的垂荡周期
    assert 7.5 < r["T_pitch"] < 9.5        # k_yy=0.30L
    assert 12.0 < r["T_roll"] < 15.0       # k_xx=0.35B
    assert r["Aw"] == pytest.approx(aud.cwp_from_cb(s["Cb"]) * 320.0 * 58.0, rel=1e-9)


def test_lower_bound_is_strict_for_zero_added_mass():
    """T_min 必须严格小于同 Aw 且 A33=0 的周期（构造性自检）。"""
    s = aud.SHIPS["S175"]
    nabla = s["displacement"] / 1.025
    mass = 1025.0 * nabla
    Aw = aud.cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
    t_min = 2 * math.pi * math.sqrt(mass / (1025.0 * 9.80665 * Aw))
    t_full = aud.ref_periods(s)["T_heave"]
    assert t_full > t_min