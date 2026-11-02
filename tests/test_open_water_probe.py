"""敞水端点欠账探针的机器判据测试（合成数据，完全离线）。

被测对象：``tools/probe_open_water_debt.py`` 的五条不变量裁决函数。
测试的意义：探针只在有网/有服务时才能跑真数据，但**判据本身必须离线可证**，
否则"判决"就是黑箱。这里用合成序列逐条钉死 PASS/FAIL/WITHHELD 的边界。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import probe_open_water_debt as P  # noqa: E402


def rows_of(js, kt_fn, kq_fn, eta_fn):
    return [{"J": j, "KT": kt_fn(j), "KQ": kq_fn(j), "eta0": eta_fn(j), "ok": True, "error": None}
            for j in js]


JS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]

# 物理正常的合成敞水曲线（KT/KQ 单调下降，η 由定义式给出）
def _kt(j):
    return 0.35 - 0.30 * j


def _kq(j):
    return 0.040 - 0.020 * j


def _eta(j):
    return j * _kt(j) / (2 * math.pi * _kq(j))


def test_identity_passes_on_definitionally_consistent_data():
    r = P.judge_identity(rows_of(JS, _kt, _kq, _eta))
    assert r["verdict"] == P.PASS and r["worst_rel_err_pct"] < 1e-6


def test_identity_fails_on_reported_eta_that_contradicts_kt_kq():
    # 端点实测病理：η0 与 KT/KQ 定义式脱钩（J=0.7 时报 0.85，定义式给 1.11）
    rows = rows_of([0.7], lambda j: 0.176, lambda j: 0.0176, lambda j: 0.85)
    r = P.judge_identity(rows)
    # 定义式给 0.7*10/(2*pi) = 1.114，端点报 0.85 → 相对误差 23.7%（J=1.0 处最差 46.6%）
    assert r["verdict"] == P.FAIL and r["worst_rel_err_pct"] > 20


def test_identity_withheld_when_no_comparable_rows():
    rows = [{"J": 0.1, "KT": None, "KQ": None, "eta0": None, "ok": False, "error": "boom"}]
    assert P.judge_identity(rows)["verdict"] == P.WITHHELD


@pytest.mark.parametrize("judge_id,expect", [("I2", P.PASS), ("I3", P.PASS)])
def test_monotonic_passes_on_physical_curves(judge_id, expect):
    rows = rows_of(JS, _kt, _kq, _eta)
    got = {c["id"]: c for c in P.judge_monotonic(rows)}
    assert got[judge_id]["verdict"] == expect
    assert got[judge_id]["violations"] == []


def test_monotonic_flags_rising_kq():
    rows = rows_of([0.1, 0.2, 0.3], _kt, lambda j: 0.01 + 0.02 * j, _eta)
    got = {c["id"]: c for c in P.judge_monotonic(rows)}
    assert got["I2"]["verdict"] == P.FAIL and got["I2"]["violations"]


def test_monotonic_withheld_on_short_series():
    got = {c["id"]: c for c in P.judge_monotonic(rows_of([0.1, 0.2], _kt, _kq, _eta))}
    assert got["I2"]["verdict"] == P.WITHHELD


def test_no_clamp_detects_hard_capped_plateau():
    """端点实测病理：J>=0.6 后 η0 恒为 0.85（4 段平台）。"""
    js = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    rows = rows_of(js, _kt, _kq, lambda j: min(0.85, 1.5915 * j))
    r = P.judge_no_clamp(rows)
    assert r["verdict"] == P.FAIL
    assert len(r["plateau_segments"]) == 4
    assert r["distinct_values"] < r["n_points"]


def test_no_clamp_passes_on_smooth_curve():
    r = P.judge_no_clamp(rows_of(JS, _kt, _kq, _eta))
    assert r["verdict"] == P.PASS and r["plateau_segments"] == []


def test_no_clamp_flags_eta_above_unity():
    rows = rows_of([0.1, 0.2, 0.3], _kt, _kq, lambda j: 1.2)
    r = P.judge_no_clamp(rows)
    assert r["verdict"] == P.FAIL and len(r["eta_ge_1"]) == 3


def test_sensitivity_detects_dead_parameter(monkeypatch):
    """η0 对 P_D 恒为零、KT 响应：说明参数只进了 KT 分支，效率是合成量。"""
    def fake_post(base, path, body, timeout=20.0):
        return {"KT": 0.132 + 0.132 * body["P_D"] / 2.0, "KQ": 0.0132, "eta0": 0.85}
    monkeypatch.setattr(P, "post", fake_post)
    got = {c["id"]: c for c in P.judge_sensitivity("http://x")}
    assert got["I5-P_D"]["verdict"] == P.PASS          # KT 有响应
    assert got["I5-P_D"]["delta_eta0"] == 0.0          # 但 η0 死掉 —— 记录在案


def test_sensitivity_fails_when_parameter_is_ignored(monkeypatch):
    monkeypatch.setattr(P, "post", lambda *a, **k: {"KT": 0.2, "KQ": 0.02, "eta0": 0.5})
    got = {c["id"]: c for c in P.judge_sensitivity("http://x")}
    assert all(got[f"I5-{p}"]["verdict"] == P.FAIL for p in ("P_D", "Ae_Ao", "Z"))


def test_verdict_is_worst_of_checks(monkeypatch):
    """整端点判级必须取最差项：任一 FAIL 即 FAIL，不可被多数 PASS 掩盖。"""
    monkeypatch.setattr(P, "sweep", lambda base, geom, js=P.J_SWEEP: rows_of(
        [0.6, 0.7, 0.8], _kt, _kq, lambda j: 0.85))
    monkeypatch.setattr(P, "post", lambda *a, **k: {"KT": 0.2, "KQ": 0.02, "eta0": 0.85})
    res = P.run("http://x")
    assert res["verdict"] == P.FAIL and "I1" in res["failed_invariants"]


def test_unreachable_endpoint_is_withheld_not_pass(monkeypatch):
    monkeypatch.setattr(P, "sweep", lambda base, geom, js=P.J_SWEEP: [
        {"J": 0.1, "KT": None, "KQ": None, "eta0": None, "ok": False, "error": "conn refused"}])
    monkeypatch.setattr(P, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    res = P.run("http://x")
    assert res["verdict"] == P.WITHHELD and res["endpoint_reachable"] is False
