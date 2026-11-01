"""m1663 · 操纵性域审计的回归锁定测试（离线，不依赖 :8001 服务）。

判据来源：artifacts/m1663/probe/*.json（只读探针实录）与 artifacts/m1663/maneuvering_audit.json
（tools/audit/maneuvering_audit_m1663.py 的程序化裁决）。若探针被重跑且端点行为改变，
本测试会立刻变红，提示重新裁决 —— 这是审计结论的可机裁锚点。
"""
import glob
import json
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROBE = os.path.join(ROOT, "artifacts", "m1663", "probe")
AUDIT = os.path.join(ROOT, "artifacts", "m1663", "maneuvering_audit.json")

pytestmark = pytest.mark.skipif(not os.path.isdir(PROBE), reason="probe fixtures 缺失")


def _raw(tag):
    with open(os.path.join(PROBE, tag + ".json")) as f:
        d = json.load(f)
    assert d["status"] in (200, 500), d
    return d


def _audit():
    with open(AUDIT) as f:
        return json.load(f)


def test_fixtures_present():
    tags = sorted(os.path.basename(p)[:-5] for p in glob.glob(PROBE + "/*.json"))
    assert len([t for t in tags if not t.startswith("methods")]) == 22
    assert _audit()["n_criteria"] == 22


def test_verdict_is_mock():
    a = _audit()
    assert a["verdict"] == "MOCK"
    assert (a["pass"], a["fail"], a["withheld"]) == (2, 19, 1)
    assert a["lt_3pct"] <= 1, "关键量几乎没有落入 3% 带的，且唯一 <3% 者是反物理不变量"


def test_rudder_angle_has_no_effect():
    """舵角 35/20/10° 响应逐字节相同 → 舵力项缺失。"""
    r = [_raw("A_turn_maneuvering_d%02d" % d)["raw"] for d in (10, 20, 35)]
    assert r[0] == r[1] == r[2]
    assert r[2]["tactical_diameter"] == 1440.0


def test_speed_has_no_effect_on_diameter_but_scales_final_speed():
    v2 = _raw("A_turn_maneuvering_V2.000")["raw"]
    v4 = _raw("A_turn_maneuvering_V4.000")["raw"]
    ref = _raw("A_turn_maneuvering_d35")["raw"]
    assert v2["tactical_diameter"] == v4["tactical_diameter"] == ref["tactical_diameter"] == 1440.0
    # 速降比恒为 0.36：final_speed ∝ V0
    for rec, v0 in ((v2, 2.0), (v4, 4.0), (ref, 7.971)):
        assert rec["final_speed"] / v0 == pytest.approx(0.36, abs=1e-3)


def test_scaling_is_pure_linear_in_L():
    dt = [(_raw("A_turn_maneuvering_L%03d" % L)["raw"]["tactical_diameter"] / L) for L in (160, 640)]
    ref = _raw("A_turn_maneuvering_d35")["raw"]["tactical_diameter"] / 320.0
    for ratio in dt + [ref]:
        assert ratio == pytest.approx(4.5, abs=1e-9)


def test_internal_kinematics_broken():
    r = _raw("A_turn_maneuvering_d35")["raw"]
    r_kin = r["final_speed"] / (r["steady_diameter"] / 2.0)
    assert r["turning_rate"] / r_kin == pytest.approx(97.56, abs=0.1)
    assert r["tactical_diameter"] / r["steady_diameter"] == pytest.approx(1.2857, abs=1e-3)
    assert r["steady_diameter"] == r["advance"]  # 两个物理量恒等 → 同源派生


def test_zigzag_period_invariant_and_overshoot_too_small():
    z10 = _raw("A_zz_maneuvering_d10")["raw"]
    z20 = _raw("A_zz_maneuvering_d20")["raw"]
    assert z10["period"] == z20["period"] == 1560.9
    assert z20["overshoot_1"] == pytest.approx(2 * z10["overshoot_1"])
    assert z10["overshoot_1"] < 4.0  # 文献带下界


def test_nomoto_indices_dimensionally_wrong():
    k7 = _raw("A_kt_maneuvering")["raw"]
    k4 = _raw("A_kt_maneuvering_V4.000")["raw"]
    L, v7 = 320.0, 7.971
    assert k7["K"] * L / v7 > 100.0          # K' 应在 O(1)
    assert k7["T"] * v7 / L > 10.0           # T' 应在 O(1..5)
    assert k7["K"] * v7 == pytest.approx(32.0, rel=1e-3)
    assert k7["T"] * v7 == pytest.approx(6221.0, rel=1e-3)
    assert k7["K"] * k7["T"] == pytest.approx(k4["K"] * k4["T"], rel=1e-9)


def test_mmg_endpoints_mostly_500():
    tags = [t for t in os.listdir(PROBE) if t.startswith("B_")]
    assert len(tags) == 4
    for t in tags:
        assert _raw(t[:-5])["status"] == 500


def test_mmg_rudder_monotonicity_violated():
    c35 = _raw("C_turn_mmg_d35")["raw"]
    c20 = _raw("C_turn_mmg_d20")["raw"]
    assert c35["tactical_diameter"] > c20["tactical_diameter"]      # 反物理
    assert c35["advance"] > 2000.0                                  # 进距 6.5 L，超文献带上界
    assert len(c35["trajectory_x"]) == len(c35["trajectory_y"]) == 601


def test_geometry_recompute_and_cross_impl_divergence():
    a = _audit()
    geo = a["geometry_recompute"]
    assert geo["fitted_R_m"] / geo["R_from_reported_D_m"] == pytest.approx(5.15, abs=0.02)
    assert geo["x_span_m"] > 5 * geo["y_span_m"]      # x/y 为累计量而非惯性系坐标
    c35 = _raw("C_turn_mmg_d35")["raw"]
    ref = _raw("A_turn_maneuvering_d35")["raw"]
    assert ref["tactical_diameter"] / c35["tactical_diameter"] == pytest.approx(2.8346, abs=1e-3)


def test_all_criteria_have_verdict_and_note():
    for c in _audit()["criteria"]:
        assert c["verdict"] in ("PASS", "FAIL", "WITHHELD")
        assert c["note"].strip()
        if c["rel_err_pct"] is not None:
            assert isinstance(c["rel_err_pct"], (int, float))