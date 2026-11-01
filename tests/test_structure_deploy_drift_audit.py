"""结构域部署面独立复核（m1667）的验收测试。

三层：
  1. 参照实现层：闭式解对照标准值（箱形剖面模数、Euler 板屈曲 C 系数、DNV-C1 S-N）；
  2. 台账层：用已落盘 fixture 重放 12 条判据，断言判决与已知失效点一致（防源码漂移静默失效）；
  3. 反例层：注入物理正确的合成 fixture，判据必须全部（或除硬编码类外）转 PASS，
     证明判据不是「恒 FAIL 的假审计」。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "audit"))

import structure_deploy_drift_audit as A  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "structure_deploy_probe.json"


@pytest.fixture(scope="module")
def fx():
    return json.loads(FIX.read_text())


@pytest.fixture(scope="module")
def ledger(fx):
    return A.build_ledger(fx)


# ------------------------------------------------------------ 1. 参照实现层
def test_box_section_modulus_closed_form():
    """箱形剖面 Z = (B D^3 - b d^3)/12 /(D/2)，与手算一致（15 mm 板）。"""
    Z = A.box_section_modulus(32.2, 19.5, 0.015)
    assert Z == pytest.approx(11.2965, rel=1e-4)


def test_plate_buckling_C_coefficient():
    """k=4 长板简支：C = k pi^2 E/(12(1-nu^2)) = 744739 MPa（单位板格）。"""
    assert A.plate_buckling_cr(1.0, 1.0) == pytest.approx(744739.4, rel=1e-5)
    # b=0.9 m, t=15 mm ⇒ 206.87 MPa（与 m1600 审计值一致，可交叉校验）
    assert A.plate_buckling_cr(0.015, 0.9) == pytest.approx(206.87, rel=1e-3)


def test_sn_curve_roundtrip():
    """DNV-C1 幂律 N = 10^12.164 dsigma^-3 与反解 log a 自洽。"""
    n = A.sn_cycles(70.5)
    assert n == pytest.approx(4.1633e6, rel=1e-3)
    assert A.sn_log_a_implied(n, 70.5) == pytest.approx(12.164, rel=1e-6)


def test_geometry_precheck_cb():
    """Cb 前置校验：Δ=52030 t 反算 Cb 与输入 0.651 的偏差 <15%（几何自洽）。"""
    cb = A.cb_check(230.0, 32.2, 10.8, 52030.0)
    assert abs(cb - 0.651) / 0.651 < 0.15


# ------------------------------------------------------------ 2. 台账层（实机 fixture 重放）
def test_ledger_shape(ledger):
    assert ledger["judgement_count"] == 12
    assert ledger["pass"] + ledger["fail"] == 12
    assert ledger["withheld"] == 0
    assert {r["id"] for r in ledger["rows"]} == {"J%d" % i for i in range(1, 13)}


@pytest.mark.parametrize("cid,expected", [
    ("J1", "FAIL"), ("J2", "FAIL"), ("J3", "FAIL"), ("J4", "FAIL"), ("J5", "FAIL"),
    ("J6", "FAIL"), ("J7", "PASS"), ("J8", "PASS"), ("J9", "FAIL"), ("J10", "FAIL"),
    ("J11", "FAIL"), ("J12", "FAIL"),
])
def test_verdicts_replay(ledger, cid, expected):
    row = next(r for r in ledger["rows"] if r["id"] == cid)
    assert row["verdict"] == expected, row


def test_stress_unit_failure_is_6e4(ledger):
    """应力单位错 1e3（kPa↔MPa）× 剖面模数错 ~63 倍 ⇒ 平台值/闭式 ≈ 6.4e4。"""
    row = next(r for r in ledger["rows"] if r["id"] == "J3")
    assert row["platform"] / row["reference"] == pytest.approx(6.39e4, rel=0.05)


def test_thickness_channel_dead(ledger, fx):
    """板厚通道完全失效：t×2 时强度端点返回值逐位不变。"""
    a = fx["endpoints"]["/structure/analyze"]
    assert a["t_x2"]["max_stress"] == a["base"]["max_stress"]


def test_buckling_coefficient_offset(ledger):
    """屈曲系数隐式 k≈1（而非 4）：平台/闭式 = 1/3.6154 ≈ 0.1106。"""
    row = next(r for r in ledger["rows"] if r["id"] == "J6")
    assert row["platform"] / row["reference"] == pytest.approx(0.1106, rel=0.02)


def test_fatigue_hardcode(ledger, fx):
    """疲劳：dsigma 恒 0.3 sigma_y，N=1e7(100/dsigma)^3 ⇒ log a=13.0（自称 DNV-C 12.164）。"""
    assert ledger["derived"]["log_a_implied"] == pytest.approx(13.0, abs=3e-3)
    f = fx["endpoints"]["/structure/fatigue"]
    assert f["disp_x2"]["calculated_life"] == f["base"]["calculated_life"]


def test_verdict_is_deploy_drift(ledger):
    v = ledger["verdict"]
    assert v["kind"] == "DEPLOY_DRIFT"
    assert v["evidence_level"] == "MOCK"
    assert v["deploy_same"] is False
    assert v["live_bytes"] < v["fixed_bytes"]


def test_deploy_drift_requires_distinct_fingerprints(fx):
    """若在线与分支指纹相同，判决必须翻为 CODE_DEFECT（防止判决逻辑只会一种结论）。"""
    fx2 = json.loads(json.dumps(fx))
    fx2["deploy"]["live"]["md5"] = fx2["deploy"]["fixed_branch"]["md5"]
    fx2["deploy"]["live"]["bytes"] = fx2["deploy"]["fixed_branch"]["bytes"]
    led = A.build_ledger(fx2)
    assert led["verdict"]["kind"] == "CODE_DEFECT"


# ------------------------------------------------------------ 3. 反例层（物理正确实现须判 PASS）
def _fixed_fixture(fx):
    fx2 = json.loads(json.dumps(fx))
    t = A.BASE["plate_thickness"]
    b = A.BASE["frame_spacing"]
    Z = A.box_section_modulus(A.BASE["B"], A.BASE["D"], t)
    sigma = 1.173e6 / Z / 1000.0
    cr = A.plate_buckling_cr(t, b)
    ds = 0.3 * A.BASE["yield_stress"]
    life = A.sn_cycles(ds) / (10 ** 7 * (100.0 / ds) ** 3 / 28.5)
    an = fx2["endpoints"]["/structure/analyze"]
    for k in an:
        an[k]["max_stress"] = sigma
        an[k]["allowable_stress"] = 175.0
    pl = fx2["endpoints"]["/structure/plate"]
    for k in pl:
        tt = 0.030 if k == "t_x2" else t
        pl[k]["critical_stress"] = A.plate_buckling_cr(tt, b if k != "fs_x2" else 2 * b)
        pl[k]["actual_stress"] = 0.35 * A.BASE["yield_stress"]
    fa = fx2["endpoints"]["/structure/fatigue"]
    for k in fa:
        fa[k]["calculated_life"] = life
    return fx2


def test_counterexample_physical_impl_passes(fx):
    """注入物理正确响应：除「型深尺度律/板格应力通道」这类需重建模型的判据外，
    量级类与规范类判据必须转 PASS。"""
    led = A.build_ledger(_fixed_fixture(fx))
    rows = {r["id"]: r for r in led["rows"]}
    for cid in ("J1", "J2", "J3", "J7", "J8", "J10", "J11", "J12"):
        assert rows[cid]["verdict"] == "PASS", (cid, rows[cid])
    # 该反例未改 D 通道与板格应力硬编码，二者应仍 FAIL —— 证明判据有分辨力
    assert rows["J5"]["verdict"] == "FAIL"
    assert led["pass"] >= 8