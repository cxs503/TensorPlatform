"""结构/振动域解析基准复核工装的回归测试（m1593）。

覆盖三件事：
1. 参考核本身的正确性（对教科书闭式值/恒等式）；
2. 裁决逻辑（3%/15% 分档、MOCK 检测、膝点连续性）；
3. 落盘审计工件与重算一致（artifact lock，防结论漂移）。

全部离线：只用 runs/m1593/live_responses.json 缓存，不触网。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))

import struct_vib_audit as A  # noqa: E402

RUN = os.path.join(ROOT, "runs", "m1593")
CACHE = os.path.join(RUN, "live_responses.json")
AUDIT = os.path.join(RUN, "audit_struct_vib.json")


# ─────────────── 1. 参考核：板格屈曲 ───────────────

@pytest.mark.parametrize("gamma,k_ref", [
    (1.0, 4.0), (2.0, 4.0), (3.0, 4.0), (1.5, 4.3403), (3.7, 4.0295), (6.0, 4.0),
])
def test_plate_k_compression(gamma, k_ref):
    """Timoshenko & Gere §9.1：k=min_m (m/γ+γ/m)²；整数 γ 时 k=4。"""
    assert A.plate_k_compression(gamma) == pytest.approx(k_ref, rel=2e-3)


def test_plate_k_lower_bound():
    """下界定理：四边简支板压缩屈曲系数 k_min ≥ 4，整数长宽比取等号。"""
    for gamma in [0.3 + 0.05 * i for i in range(120)]:
        assert A.plate_k_compression(gamma) >= 4.0 - 1e-9


def test_plate_k_shear():
    """k_s = 5.34 + 4/(a/b)²；长板极限 → 5.34。"""
    assert A.plate_k_shear(4.0) == pytest.approx(5.59, abs=1e-6)
    assert A.plate_k_shear(100.0) == pytest.approx(5.3404, abs=1e-3)


def test_sigma_cr_hand_value():
    """k=4, t/b=0.02, E=210 GPa, ν=0.3 → 303.68 MPa（手算：4·π²·210000/(12·0.91)·4e-4）。"""
    ref = 4 * math.pi ** 2 * 210000.0 / (12 * 0.91) * 0.02 ** 2
    assert A.sigma_cr(4.0, 0.02) == pytest.approx(ref, rel=1e-12)
    assert A.sigma_cr(4.0, 0.02) == pytest.approx(303.68, abs=0.02)


def test_sigma_cr_thickness_scaling():
    """σ_cr ∝ t²：板厚 ×2 → 4 倍（与平台 /structure/plate 的 300% 增量对照）。"""
    assert A.sigma_cr(4.0, 0.030 / 0.6) / A.sigma_cr(4.0, 0.015 / 0.6) == pytest.approx(4.0)


# ─────────────── 2. 参考核：弹塑性折减 ───────────────

def test_johnson_ostenfeld_regimes():
    y = A.SIGMA_Y
    assert A.johnson_ostenfeld(0.4 * y) == pytest.approx(0.4 * y)      # 弹性区不变
    assert A.johnson_ostenfeld(0.5 * y) == pytest.approx(0.5 * y)      # 分界连续
    assert A.johnson_ostenfeld(2.0 * y) == pytest.approx(y * (1 - 1 / 8))
    assert A.johnson_ostenfeld(1e6) < y                                 # 上界 σ_y


# ─────────────── 3. 参考核：S-N 与 Miner ───────────────

def test_sn_knee_continuity_dnv():
    """DNV-RP-C203 D 曲线自身在 N=1e7 膝点必须连续。"""
    s_knee = A.s_knee_dnv_d()
    assert A.sn_life_dnv_d(s_knee) == pytest.approx(1e7, rel=1e-9)
    # 表参数 3 位/5 位有效数字的四舍五入会让残差落在 1e-3 量级，故按 0.3% 判自洽
    assert abs(A.sn_continuity_residual(12.164, 15.606)) < 3e-3


def test_sn_life_values():
    """N(80 MPa)=2.85e6（m=3 段）；N(40 MPa)=3.94e7（m=5 段，膝点 52.6 MPa 以下）。"""
    assert A.sn_life_dnv_d(80.0) == pytest.approx(2.849e6, rel=5e-3)
    assert A.sn_life_dnv_d(40.0) == pytest.approx(3.937e7, rel=5e-3)
    assert A.sn_life_dnv_d(80.0) < A.sn_life_dnv_d(40.0)   # 单调性


def test_sn_continuity_detects_platform_residual():
    """平台隐含参数（log a1=12.164, log a2=15.817）→ 膝点不连续度 ≈ +62%。"""
    r = A.sn_continuity_residual(12.164, 15.817)
    assert 0.60 < r < 0.65


def test_miner_damage_additive():
    d1 = A.miner_damage([80.0], [1e6])
    d2 = A.miner_damage([50.0], [1e6])
    d = A.miner_damage([80.0, 50.0], [1e6, 1e6])
    assert d == pytest.approx(d1 + d2)
    assert d1 == pytest.approx(1e6 / A.sn_life_dnv_d(80.0))
    with pytest.raises(ValueError):
        A.miner_damage([80.0], [1e6, 1e5])


# ─────────────── 4. 参考核：船体梁 ───────────────

def test_box_girder_symmetry_and_thin_wall_limit():
    s = A.box_girder_section(22.0, 12.0, 0.015, 0.015, 0.015)
    assert s["y_bar"] == pytest.approx(6.0)                 # 上下等厚 → 中性轴居中
    assert s["Z_deck"] == pytest.approx(s["Z_bottom"], rel=1e-12)
    # 薄壁近似 I ≈ t(B D²/2 + D³/6)（上下翼板 + 双舷侧）
    thin = 0.015 * (22 * 12 ** 2 / 2 + 2 * 12 ** 3 / 12)
    assert s["I"] == pytest.approx(thin, rel=3e-2)


def test_wave_bending_moment_and_stress():
    mw = A.iacs_wave_bending_moment(150.0, 22.0, 0.75)
    assert mw == pytest.approx(0.19 * 150 ** 2 * 22 * 1.45, rel=1e-12)
    z = A.box_girder_section(22.0, 12.0, 0.015, 0.015, 0.015)["Z_deck"]
    assert A.hull_girder_stress(mw, z) == pytest.approx(mw / z * 1e-3, rel=1e-12)
    assert 20.0 < A.hull_girder_stress(mw, z) < 40.0        # 量级带 20–40 MPa


# ─────────────── 5. 裁决逻辑 ───────────────

@pytest.mark.parametrize("err,expect", [
    (0.0, "PASS"), (2.99, "PASS"), (-3.01, "WARN"), (14.9, "WARN"),
    (15.01, "FAIL"), (-89.0, "FAIL"), (None, "WITHHELD"),
])
def test_verdict_thresholds(err, expect):
    assert A.verdict(err) == expect


@pytest.mark.parametrize("rel_off", [0.0, 1e-12, -1e-9])
def test_verdict_pass_band_is_3pct(rel_off):
    assert A.verdict(100 * rel_off) == "PASS"


# ─────────────── 6. 工件锁：缓存重算 == 落盘结论 ───────────────

pytestmark_artifact = pytest.mark.skipif(
    not (os.path.exists(CACHE) and os.path.exists(AUDIT)), reason="缺审计工件")


@pytestmark_artifact
def test_replay_reproduces_locked_audit():
    with open(CACHE) as f:
        probes = json.load(f)["probes"]
    with open(AUDIT) as f:
        locked = json.load(f)
    fresh = A.judge(probes)
    assert fresh["summary"] == locked["summary"]
    assert fresh["scored"] == locked["scored"] == 19
    got = {c["id"]: c["verdict"] for c in fresh["criteria"]}
    want = {c["id"]: c["verdict"] for c in locked["criteria"]}
    assert got == want


@pytestmark_artifact
def test_locked_audit_key_findings():
    """结论锚点：MOCK/单位/膝点/free 边界/弯曲载荷/Ostenfeld 必须判 FAIL，屈曲基础用例必须 PASS。"""
    with open(AUDIT) as f:
        rows = {c["id"]: c for c in json.load(f)["criteria"]}
    for cid in ("S1", "S2", "S3", "F2", "F3", "P5", "P7", "P9", "P8", "V1", "L2"):
        assert rows[cid]["verdict"] == "FAIL", cid
    for cid in ("F1", "P2", "P3", "P4", "P6", "L1"):
        assert rows[cid]["verdict"] == "PASS", cid
    assert rows["P1"]["verdict"] == "WARN"
    # 每个判据都要有可追溯的说明
    assert all(r["note"] for r in rows.values())


@pytestmark_artifact
def test_locked_audit_numeric_anchors():
    """关键量数值锚点（防止缓存被静默替换）。"""
    with open(AUDIT) as f:
        rows = {c["id"]: c for c in json.load(f)["criteria"]}
    assert rows["P3"]["platform"] == pytest.approx(303.68)      # k=4 基准平台正确
    assert rows["P4"]["platform"] == pytest.approx(531.44)      # clamped k=7
    assert rows["P6"]["platform"] == pytest.approx(424.39)      # shear k_s
    assert rows["P1"]["err_pct"] == pytest.approx(-7.84, abs=0.05)
    assert rows["P5"]["err_pct"] > 1000                          # free 边界 15 倍量级错误
    assert rows["P9"]["err_pct"] == pytest.approx(-83.26, abs=0.05)
    assert rows["F2"]["platform"] == pytest.approx(62.31, abs=0.05)
    assert rows["S2"]["err_pct"] > 1e7                           # 单位混用 1e6 倍