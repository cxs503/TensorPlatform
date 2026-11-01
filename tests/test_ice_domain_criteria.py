"""冰区航行域验证判据回归（m1517）。

两段：
1) 参考实现自检 —— Lindqvist (1989) 分量与 IACS UR I2 关系式的闭式性质
   （标度律、速度因子、区间端点次序、等级单调），确保参考实现本身可信；
2) 端点判决回归 —— 读取 artifacts/m1517/ice_probe.json（只读探针落盘），
   重算 11 条判据并断言记录的判决与关键数值，端点为 MOCK 级。

端点若被修复（响应律恢复），本文件的判决断言会由 FAIL 变 PASS 而失败——
这是有意设计：届时须同步更新工件与判据版本，而不是让 CI 静默通过。
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verif.ice.ice_ref import (  # noqa: E402
    G, IACS_PC_PRESSURE_MPA, KNOT, LINDQVIST_BAND, evaluate,
    iacs_plating_thickness_mm, lindqvist_band, lindqvist_resistance,
)

PROBE = ROOT / "artifacts" / "m1517" / "ice_probe.json"


@pytest.fixture(scope="module")
def probe():
    return json.loads(PROBE.read_text())


@pytest.fixture(scope="module")
def summary(probe):
    return evaluate(probe)


# ── 1) 参考实现自检 ────────────────────────────────────────────────
def test_crushing_component_scales_with_h_squared():
    a = lindqvist_resistance(150, 23, 8.5, 1.0, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    b = lindqvist_resistance(150, 23, 8.5, 2.0, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    assert b["R_c"] / a["R_c"] == pytest.approx(4.0, rel=1e-9)


def test_breaking_component_scales_with_h_1p5():
    a = lindqvist_resistance(150, 23, 8.5, 1.0, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    b = lindqvist_resistance(150, 23, 8.5, 1.0 * 4, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    assert b["R_b"] / a["R_b"] == pytest.approx(4 ** 1.5, rel=1e-9)


def test_velocity_factor_identity():
    v, h = 2.57, 1.0
    r = lindqvist_resistance(150, 23, 8.5, h, v, 1.5e6, 0.5e6, 0.15, 30)
    assert r["f_v"] == pytest.approx(1.0 + 1.4 * v / math.sqrt(G * h), rel=1e-12)


def test_submergence_component_increases_with_draft():
    a = lindqvist_resistance(150, 23, 6.0, 1.0, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    b = lindqvist_resistance(150, 23, 12.0, 1.0, 2.57, 1.5e6, 0.5e6, 0.15, 30)
    assert b["R_s"] > a["R_s"] > 0.0


def test_band_ordering_and_speed_monotonicity():
    lo = lindqvist_band(150, 23, 8.5, 1.0, 1.0 * KNOT)
    hi = lindqvist_band(150, 23, 8.5, 1.0, 5.0 * KNOT)
    assert lo["R_lo"] < lo["R_hi"]
    assert hi["R_lo"] > lo["R_lo"]           # 速度增加 → 下界抬升
    assert lo["R_hi"] / lo["R_lo"] < 10.0    # 区间宽度可控，非人为放大


def test_iacs_plating_monotonic_in_class():
    ts = [iacs_plating_thickness_mm(c, 0.35) for c in
          ("PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7")]
    assert all(ts[i] > ts[i + 1] for i in range(len(ts) - 1))
    assert 10.0 < ts[-1] < 40.0 and 20.0 < ts[0] < 60.0


def test_iacs_pressure_table_sanity():
    vals = list(IACS_PC_PRESSURE_MPA.values())
    assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    assert all(1.0 <= v <= 6.0 for v in vals)


# ── 2) 端点判决回归（记录工件）────────────────────────────────────
def test_probe_artifact_shape(probe):
    assert len(probe["rows"]) == 31
    base = [r for r in probe["rows"] if r["variant"] == "base"][0]["response"]
    assert base["method"].startswith("Lindqvist")


def test_verdict_is_mock(summary):
    assert summary["verdict"] == "MOCK"
    assert summary["n_fail"] >= 6


def test_speed_response_fails(summary):
    verdicts = {c["id"]: c["verdict"] for c in summary["criteria"]}
    assert verdicts["A2"] == "FAIL"   # dR/dV = 0
    assert verdicts["A3"] == "FAIL"   # R(5kn)/R(1kn) = 1.000


def test_draft_and_magnitude_fail(summary):
    verdicts = {c["id"]: c["verdict"] for c in summary["criteria"]}
    assert verdicts["A5"] == "FAIL"    # 吃水不参与
    assert verdicts["A6"] == "FAIL"    # 低于挤压下界
    assert verdicts["A10"] == "FAIL"   # 量级低于 Lindqvist 区间


def test_cap_and_plating_fail(summary):
    verdicts = {c["id"]: c["verdict"] for c in summary["criteria"]}
    assert verdicts["A7"] == "FAIL"    # 连续速度越过等级上限
    assert verdicts["A9"] == "FAIL"    # 壳板厚度对等级恒定


def test_passing_criteria_stay_passing(summary):
    verdicts = {c["id"]: c["verdict"] for c in summary["criteria"]}
    for cid in ("A1", "A4", "A8", "A11"):
        assert verdicts[cid] == "PASS", cid


def test_underprediction_factor_range(summary):
    f = summary["underprediction_factor"]
    assert f["lo"] > 10.0            # 至少低估一个量级
    assert 400.0 < summary["lindqvist_band_N"]["R_lo"] / 1e3 < 700.0
    assert 1500.0 < summary["lindqvist_band_N"]["R_hi"] / 1e3 < 2500.0


def test_platform_magnitude_pinned(summary):
    """端点若返回量级变化（修复或改模型），本测试报警。"""
    assert summary["platform"]["ice_resistance_N"] == pytest.approx(15174.0, rel=1e-6)
    assert summary["platform"]["ice_power_W"] == pytest.approx(39028.0, rel=1e-6)


def test_criteria_band_parameters_documented():
    assert set(LINDQVIST_BAND) == {"sigma_c", "sigma_f", "mu", "alpha_deg"}
    for lo, hi in LINDQVIST_BAND.values():
        assert lo < hi
