"""耐波 / 操纵域验证欠账清偿 — 离线回归测试.

两部分：
  A. 参考实现自检（闭式公式本身必须对，否则判决不可信）
  B. 判决台账回放（读 fixtures 快照 → 判决集合必须锁定，防判决漂移）

全部离线：不依赖 :8001 实时服务。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from verifier import tp_ver_sea_man as V  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "tools", "verifier", "fixtures", "sea_man_probe_snapshot.json")


# ---------------------------------------------------------------------------
# A. 参考实现自检
# ---------------------------------------------------------------------------
def test_jonswap_m0_matches_hs_definition():
    """JONSWAP 谱零阶矩必须满足 m0 = Hs²/16（<3%）。"""
    for Hs, Tp in ((5.0, 9.0), (2.0, 6.0), (8.0, 13.0)):
        m0 = V.jonswap_m0(Hs, Tp, 3.3)
        assert abs(m0 - Hs * Hs / 16.0) / (Hs * Hs / 16.0) < 0.03, (Hs, Tp, m0)


def test_deep_water_dispersion_identity():
    """深水色散：k=ω²/g 与 λ=2πg/ω² 必须互相自洽。"""
    for w in (0.3, 0.8, 1.5):
        k = V.deep_water_wavenumber(w)
        assert abs(2 * math.pi / k - V.deep_water_wavelength(w)) < 1e-9
        assert abs(math.sqrt(V.G * k) - w) < 1e-12


def test_box_heave_period_matches_wall_sided_analytic():
    """壁式剖面 ω_n=√(g·Cwp/(Cb·T)) 的数值与手算一致。"""
    T, Cb, Cwp = 10.8, 0.6505, 0.868
    tn = V.box_heave_natural_period(T, Cb, Cwp)
    hand = 2 * math.pi / math.sqrt(V.G * Cwp / (Cb * T))
    assert abs(tn - hand) < 1e-12
    assert 5.0 < tn < 7.0  # KCS 量级（a33=0 下界 ≈5.7 s）


def test_roll_period_sqrt_gm_scaling():
    """横摇周期必须按 1/√GM 缩放（GM 加倍 ⇒ 周期 /√2）。"""
    k_xx = 8.0
    r = V.roll_natural_period(k_xx, 1.2) / V.roll_natural_period(k_xx, 2.4)
    assert abs(r - math.sqrt(2.0)) < 1e-12


def test_nomoto_and_turning_kinematics():
    """Nomoto 稳态 r=Kδ 与回转半径恒等式 R=U/r 必须互逆。"""
    K, delta, U = 0.5, math.radians(35.0), 7.7
    r = V.nomoto_steady_yaw_rate(K, delta)
    assert abs(r - 0.5 * math.radians(35.0)) < 1e-12
    R = V.turning_radius_from_rate(U, r)
    assert abs(U / R - r) < 1e-12


# ---------------------------------------------------------------------------
# B. 判决台账回放
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def audit():
    assert os.path.exists(SNAPSHOT), f"缺证据快照 {SNAPSHOT}"
    with open(SNAPSHOT, encoding="utf-8") as f:
        snap = json.load(f)
    verdicts = V.evaluate(snap["probes"], snap["ship"])
    return snap, verdicts, {v.cid: v for v in verdicts}


def test_criteria_count_and_uniqueness(audit):
    _, verdicts, by_id = audit
    assert len(verdicts) == 19
    assert len(by_id) == 19, "判据编号必须唯一"
    assert {v.domain for v in verdicts} == {"geometry", "manoeuvring", "seakeeping"}


def test_every_criterion_declares_basis_and_tolerance(audit):
    """台账完整性：每条判据都必须写明解析/规范依据与容差。"""
    _, verdicts, _ = audit
    for v in verdicts:
        assert v.basis and len(v.basis) > 8, v.cid
        assert v.tol and len(v.tol) >= 2, v.cid
        assert v.verdict in {"PASS", "FAIL", "ERROR", "SKIP"}, v.cid


def test_geometry_gate_passes(audit):
    """GEO-0：KCS 几何 Cb=∇/(LBT) 必须通过（<1%）才允许后续判决。"""
    snap, _, by_id = audit
    assert by_id["GEO-0"].verdict == "PASS"
    cb = snap["ship"]["displacement"] / (snap["ship"]["L"] * snap["ship"]["B"] * snap["ship"]["T"])
    assert abs(cb - snap["ship"]["Cb"]) / snap["ship"]["Cb"] < 0.01


def test_manoeuvring_domain_all_fail(audit):
    """操纵域 9 条判据全 FAIL（MOCK）：锁定判决，防无声回归。"""
    _, verdicts, by_id = audit
    man = [v.cid for v in verdicts if v.domain == "manoeuvring"]
    assert len(man) == 9
    for cid in man:
        assert by_id[cid].verdict == "FAIL", (cid, by_id[cid].verdict, by_id[cid].note)
        assert V.evidence_grade(by_id[cid]) == "MOCK"


def test_seakeeping_fail_set(audit):
    """耐波域：SEA-1/SEA-4 PASS（REAL），其余 7 条 FAIL。"""
    _, verdicts, by_id = audit
    assert by_id["SEA-1"].verdict == "PASS"
    assert by_id["SEA-4"].verdict == "PASS"
    for cid in ("SEA-2", "SEA-3", "SEA-5", "SEA-6", "SEA-7", "SEA-8", "SEA-9"):
        assert by_id[cid].verdict == "FAIL", cid


def test_key_error_magnitudes_are_reproducible(audit):
    """关键误差量必须可从快照复算（数字不可手抄）。"""
    snap, _, by_id = audit
    pr = snap["probes"]["turning_r35"]["resp"]
    U = snap["ship"]["speed"]
    # MAN-1：D 与 r 的互锁偏差
    D_exp = 2.0 * U / math.radians(pr["turning_rate"])
    assert abs(by_id["MAN-1"].rel_err - abs(pr["steady_diameter"] - D_exp) / D_exp) < 1e-9
    assert by_id["MAN-1"].rel_err > 0.5, "D 与 r 至少应背离 50%"
    # MAN-2/4：舵角完全不敏感
    assert by_id["MAN-2"].measured == 0.0
    assert by_id["MAN-4"].measured == 1.0
    # MAN-5：三个回转要素全部落在 0.5L 栅格上（硬编码指纹）
    L = snap["ship"]["L"]
    grid = [pr["advance"] / L, pr["transfer"] / L, pr["tactical_diameter"] / L]
    assert all(abs(x * 2 - round(x * 2)) < 1e-9 for x in grid), grid
    assert by_id["MAN-5"].verdict == "FAIL"
    # SEA-3：纵摇与垂荡数组逐点恒等
    rao = snap["probes"]["rao_gm1p2"]["resp"]
    assert all(abs(a - b) < 1e-12 for a, b in zip(rao["heave_rao"], rao["pitch_rao"]))


def test_summary_counts(audit):
    _, verdicts, _ = audit
    s = V.summarize(verdicts)
    assert s["n"] == 19
    assert s["total"]["PASS"] == 3
    assert s["total"]["FAIL"] == 16
    assert s["total"]["ERROR"] == 0
    assert s["by_domain"]["manoeuvring"]["FAIL"] == 9


def test_markdown_and_json_have_no_placeholders(audit):
    """报告产物必须自洽：md 行数与判据数一致，JSON 可序列化。"""
    snap, verdicts, _ = audit
    md = V.to_markdown(verdicts, snap["ship"], {"base_url": "x", "mode": "replay"})
    assert md.count("### ") == 19
    payload = V.to_json(verdicts, snap["ship"], {"base_url": "x", "mode": "replay"})
    json.dumps(payload)
    assert payload["summary"]["total"]["FAIL"] == 16
