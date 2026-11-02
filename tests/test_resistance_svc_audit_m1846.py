"""服务层阻力通道复核的回归测试（mission m1846）。

两条线：
1. **判据自身的正确性**（合成输入 → 期望判决）：保证 J1..J6 不是「永远 FAIL」的假判据，
   对合法物理给 PASS、对伪造伪造给 FAIL，含负向样本。
2. **服务层行为指纹**：以 artifacts/m1846/results.json（本单实跑工件）为 fixture，
   锁定 2026-11-02 观测到的服务层数值。端点若被修好，这些测试会 FAIL——
   这是**预期**的：届时须重跑 `python3 -m tools.resistance_svc.audit_m1846` 并更新本案卷。

离线可跑（不访问 :8001）：pytest tests/test_resistance_svc_audit_m1846.py -q
"""

from __future__ import annotations

import json
import os

import pytest

from tools.resistance_svc import audit_m1846 as A

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "artifacts", "m1846", "results.json")


@pytest.fixture(scope="module")
def res():
    with open(RESULTS, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 1. 判据正确性（正/负向样本）
# --------------------------------------------------------------------------- #
def test_j1_accepts_vanishing_residual():
    """Holtrop 型兴波在 Fn=0.02 处的值应远低于 1e-4 → PASS。"""
    assert A.judge_zero_froude({0.02: 1.0e-6, 0.10: 3.0e-4})["pass"] is True


def test_j1_rejects_constant_offset():
    """常数份额 2.5e-3（本单实测缺陷形态）→ FAIL。"""
    out = A.judge_zero_froude({0.02: 0.002625, 0.10: 0.00375})
    assert out["pass"] is False
    assert out["cr"] == pytest.approx(0.002625)


def test_j2_accepts_viscous_dominated():
    rows = [{"case_id": "x", "froude_number": 0.13, "cr": 2.0e-4, "ct": 2.8e-3}]
    assert A.judge_viscous_share(rows)["pass"] is True


def test_j2_rejects_residual_dominated():
    rows = [{"case_id": "x", "froude_number": 0.13, "cr": 4.6e-3, "ct": 6.4e-3}]
    out = A.judge_viscous_share(rows)
    assert out["pass"] is False
    assert out["cases"][0]["cr_share"] == pytest.approx(4.6 / 6.4, rel=1e-3)


def test_j2_ignores_high_froude_rows():
    """Fn>0.2 不在黏性主导区间，不参与判决。"""
    rows = [{"case_id": "fast", "froude_number": 0.36, "cr": 0.01, "ct": 0.014}]
    out = A.judge_viscous_share(rows)
    assert out["cases"] == []  # 判据不适用于该区间，既不给 PASS 也不给反证


def test_j3_accepts_monotone_sensitive():
    series = {0.55: 1.0e-4, 0.65: 2.0e-4, 0.70: 3.0e-4, 0.82: 1.0e-3}
    assert A.judge_fullness(series)["pass"] is True


def test_j3_rejects_plateau():
    series = {0.55: 8.0e-3, 0.60: 8.0e-3, 0.65: 8.0e-3, 0.70: 8.0e-3, 0.82: 9.2e-3}
    out = A.judge_fullness(series)
    assert out["pass"] is False and out["plateau_in_band"] is True


def test_j3_rejects_non_monotone():
    series = {0.55: 3.0e-4, 0.65: 2.0e-4, 0.82: 5.0e-4}
    assert A.judge_fullness(series)["monotone"] is False


def test_j4_gate_boundary():
    rows = [
        {"case_id": "ok", "ct_pred": 0.0115, "ct_efd": 0.0100},
        {"case_id": "bad", "ct_pred": 0.0130, "ct_efd": 0.0100},
        {"case_id": "held", "ct_pred": 0.02, "ct_efd": None},
    ]
    out = A.judge_efd(rows)
    assert [c["pass"] for c in out["cases"]] == [True, False]
    assert out["with_held_from_judgement"] == ["held"]
    assert out["n_total"] == 2 and out["n_pass"] == 1
    assert out["max_abs_err_pct"] == pytest.approx(30.0)


def test_j5_boundary():
    rows = [{"case_id": "a", "ct_a": 0.006427, "ct_b": 0.006427 * 1.04},
            {"case_id": "b", "ct_a": 0.0064, "ct_b": 0.0042}]
    out = A.judge_cross_channel(rows)
    assert [c["pass"] for c in out["cases"]] == [True, False]


def test_j6_band():
    rows = [{"case_id": "kvlcc2", "wetted_surface": 27603.3, "length": 320.0, "beam": 58.0},
            {"case_id": "bad", "wetted_surface": 100.0, "length": 320.0, "beam": 58.0}]
    out = A.judge_wetted_band(rows)
    assert [c["pass"] for c in out["cases"]] == [True, False]


def test_wetted_band_reference_matches_canon():
    """KVLCC2 的 S/(L·B)=1.487 落在 canonical R6 形体带 [1.0, 3.2] 内。"""
    rows = [{"case_id": "k", "wetted_surface": 27603.33, "length": 320.0, "beam": 58.0}]
    assert rows[0]["wetted_surface"] / (320.0 * 58.0) == pytest.approx(1.4873, rel=2e-3)


# --------------------------------------------------------------------------- #
# 2. 服务层行为指纹（fixture = 本单实跑工件）
# --------------------------------------------------------------------------- #
def test_fixture_reachable(res):
    assert res["status"] == "ok" and res["reachable"] is True


def test_fixture_verdict_is_mock(res):
    v = res["verdict"]
    assert v["evidence_level_service_layer"] == "MOCK"
    assert v["efd_pass"] == "0/8"


def test_fixture_zero_froude_offset_recorded(res):
    """服务层在 Fn=0.02 处仍有 Cr≈2.766e-3（常数份额指纹）。"""
    j = res["judgements"]["J1_zero_froude"]
    assert j["cr"] == pytest.approx(0.002766, abs=2e-6)
    assert j["pass"] is False


def test_fixture_kvlcc2_efd_errors_recorded(res):
    """KVLCC2 四行 Ct 相对 canonical v3.0 高估 108%–130%。"""
    errs = {c["case_id"]: c["err_pct"] for c in res["judgements"]["J4_efd"]["cases"]}
    assert 100.0 < errs["KVLCC2-01"] < 160.0
    assert 200.0 < errs["DTMB5415-04"] < 320.0


def test_fixture_cross_channel_gap_recorded(res):
    """同船同速两通道 Ct 相差约 35%（应 <5%）。"""
    j = res["judgements"]["J5_cross_channel"]
    assert j["pass"] is False
    assert 30.0 < j["max_abs_diff_pct"] < 40.0


def test_fixture_cb_plateau_recorded(res):
    cb = res["cb_sweep"]["cr_by_cb"]
    vals = [cb["0.55"], cb["0.6"], cb["0.65"], cb["0.7"]]
    assert max(vals) - min(vals) < 1e-9, "Cb∈[0.55,0.70] 平台是本单核心证据"


def test_fixture_channel_b_hardcodes_seawater_viscosity(res):
    """通道 B 不接受流体参数：模型试验（淡水 ν=1.004e-6）被按海水 ν≈1.19e-6 计算。"""
    nu = [c["nu_b_implied"] for c in res["cases"] if c.get("nu_b_implied")]
    assert nu and all(1.15e-6 < v < 1.22e-6 for v in nu)


def test_fixture_coverage_gap_declared(res):
    assert "submerged_bodies" in res["coverage_gap"]