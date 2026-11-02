"""mission 1758 —— /advanced/bem 审计判决逻辑的单元测试（离线，不需要平台在线）。

两类断言
--------
1. **判据逻辑正确性**：喂合成的「真求解器」响应（RAO 随 ω 变、K33 按 Cw·LB 变），
   判据必须给 PASS；喂「硬编码响应」（恒定表、A_wp=L·B），必须给 FAIL。
   这防的是「审计脚本自己坏掉，把什么都判 FAIL」。
2. **实测记录复算**：读 artifacts/m1758/bem_surface/audit.json（在线探针原始响应），
   断言每条判据的实测值可复算、整体证据等级为 MOCK。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "audit"))

import audit_m1758_bem_surface as A  # noqa: E402

AUD = os.path.join(ROOT, "artifacts", "m1758", "bem_surface", "audit.json")


def _rec(pid, group, payload, *, heave, pitch, k33, k44, k55, damping, panels,
         f3=1.0e6, am=None):
    return dict(id=pid, _group=group, _payload=payload, _ok=True, panels=panels,
                added_mass=am or dict(surge=k33 / 10.0, heave=0.9 * A.RHO * payload["cb"]
                                      * payload["length"] * payload["beam"] * payload["draft"],
                                      roll=k44 / 100.0, yaw=k55 / 100.0, pitch=k55 / 100.0),
                damping=damping,
                restoring=dict(heave=k33, roll=k44, pitch=k55),
                rao=dict(frequencies=[0.3, 0.5, 0.7, 0.9, 1.1, 1.3],
                         heave=heave, pitch=pitch),
                wave_forces=dict(surge=f3 / 10, sway=f3 / 5, heave=f3),
                wave_moments=dict(roll=f3, pitch=f3 * 50, yaw=f3 * 10))


def _honest_records():
    """合成一个「像真求解器」的响应：RAO 依赖 ω、K33 依赖 Cw·LB、阻尼非零。"""
    recs = []
    for i, name in enumerate(["KCS", "KVLCC2", "DTMB5415"]):
        h = dict(A.HULLS[name]); h.update(speed=0.0, wave_frequency=0.6, wave_direction=180.0)
        L, B, T, cb = h["length"], h["beam"], h["draft"], h["cb"]
        cw = (1 + 2 * cb) / 3.0
        k33 = A.RHO * A.G * cw * L * B
        nabla = cb * L * B * T
        k44 = A.RHO * A.G * nabla * (0.6 + 1.2 * cb)
        k55 = A.RHO * A.G * nabla * (0.05 * L)
        recs.append(_rec(f"hull_{name}", "hull", h,
                         heave=[0.9, 1.1, 1.4, 1.2, 0.9, 0.7],
                         pitch=[0.4, 0.7, 1.0, 0.8, 0.6, 0.4],
                         k33=k33, k44=k44, k55=k55,
                         damping=dict(surge=1e4, heave=2e5, pitch=3e5,
                                      sway=4e5, roll=5e5, yaw=6e5),
                         panels=int(0.9 * L * B), f3=1.5e5 * cb * L))
    # 频率扫描：RAO 真的随 ω 变
    for w in (0.3, 0.5, 0.7, 0.9, 1.1):
        h = dict(A.HULLS["KCS"]); h.update(speed=0.0, wave_frequency=w, wave_direction=180.0)
        recs.append(_rec(f"freq_{w}", "freq", h,
                         heave=[1 / (1 + (w - 0.7) ** 2), 1, 1, 1, 1, 1],
                         pitch=[0.5 * w, 1, 1, 1, 1, 1],
                         k33=1.0, k44=1.0, k55=1.0,
                         damping=dict(surge=1, heave=1, pitch=1, sway=1, roll=1, yaw=1),
                         panels=100, f3=1e5))
    # Cb 扫描：K33 随 Cb 变
    for cb in (0.45, 0.6, 0.75, 0.85):
        h = dict(A.HULLS["KCS"]); h.update(cb=cb, speed=0.0, wave_frequency=0.6, wave_direction=180.0)
        k33 = A.RHO * A.G * (1 + 2 * cb) / 3.0 * h["length"] * h["beam"]
        recs.append(_rec(f"cb_{cb}", "cb", h, heave=[1, 1, 1, 1, 1, 1],
                         pitch=[1, 1, 1, 1, 1, 1], k33=k33, k44=1e9, k55=1e10,
                         damping=dict(surge=1, heave=1, pitch=1, sway=1, roll=1, yaw=1),
                         panels=100, f3=1e5))
    # 航速 / 浪向扫描：同一输入族下结果必须互不相同
    DAMP = dict(surge=1, heave=1, pitch=1, sway=1, roll=1, yaw=1)
    for u in (0.0, 5.0, 10.0):
        h = dict(A.HULLS["KCS"])
        h.update(speed=u, wave_frequency=0.6, wave_direction=180.0)
        f3 = 1.0e5 * (1.0 + u / 50.0)
        recs.append(_rec(f"speed_{u}", "speed", h, heave=[1] * 6, pitch=[1] * 6,
                         k33=A.RHO * A.G * 7000.0 * (1.0 + u / 100.0),
                         k44=1.0e9, k55=1.0e10, damping=DAMP, panels=100, f3=f3))
    for d in (0.0, 90.0, 180.0):
        h = dict(A.HULLS["KCS"])
        h.update(speed=0.0, wave_frequency=0.6, wave_direction=d)
        f3 = 1.0e5 * (1.0 + math.cos(math.radians(d)))
        recs.append(_rec(f"dir_{d}", "dir", h, heave=[1] * 6, pitch=[1] * 6,
                         k33=A.RHO * A.G * 7000.0, k44=1.0e9, k55=1.0e10,
                         damping=DAMP, panels=100, f3=f3))
    return recs


def _verdict(judges, jid):
    return next(j["verdict"] for j in judges if j["id"] == jid)


def test_judges_pass_on_honest_solver():
    judges = A.judge(_honest_records(), cross=None)
    for jid in ("J1", "J5", "J6", "J7", "J9D", "J9S"):
        assert _verdict(judges, jid) == "PASS", (jid, judges)


def test_judges_fail_on_constant_table():
    recs = _honest_records()
    # 把频率扫描压成恒定表（模拟硬编码 RAO）
    for r in recs:
        if r["_group"] == "freq":
            r["rao"]["heave"] = [1.0] * 6
            r["rao"]["pitch"] = [1.0] * 6
    judges = A.judge(recs, cross=None)
    assert _verdict(judges, "J1") == "FAIL"


def test_identity_judge_detects_rectangle_waterplane():
    recs = []
    for name in ("KCS", "KVLCC2", "DTMB5415"):
        h = dict(A.HULLS[name]); h.update(speed=0.0, wave_frequency=0.6, wave_direction=180.0)
        k33 = A.RHO * A.G * h["length"] * h["beam"]          # 矩形水线面（错误）
        recs.append(_rec(f"hull_{name}", "hull", h, heave=[1] * 6, pitch=[1] * 6,
                         k33=k33, k44=1e9, k55=1e10,
                         damping=dict(surge=1, heave=1, pitch=1, sway=1, roll=1, yaw=1),
                         panels=100, f3=1e5))
    judges = A.judge(recs, cross=None)
    j4 = next(j for j in judges if j["id"] == "J4")
    assert j4["verdict"] == "FAIL"
    assert "L·B" in j4["measured"]


@pytest.mark.skipif(not os.path.exists(AUD), reason="需要先跑在线审计生成 audit.json")
def test_recorded_audit_verdict():
    with open(AUD, encoding="utf-8") as f:
        a = json.load(f)
    assert a["n_probes_ok"] == a["n_probes"] >= 30
    assert a["evidence_grade"] == "MOCK"
    assert a["n_fail"] >= 8
    judges = {j["id"]: j for j in a["judgements"]}
    # 频率无关与 A_wp=L·B 是两条最硬的判据，必须 FAIL
    assert judges["J1"]["verdict"] == "FAIL"
    assert judges["J2"]["verdict"] == "FAIL"
    assert judges["J4"]["verdict"] == "FAIL"
    # 反解 A_wp 与 L·B 的相对偏差应 < 1e-6（精确硬编码）
    for d in a["derived"]:
        assert d["rel_Awp_vs_LB"] < 1e-6
        assert abs(d["implied_Awp"] - d["LB"]) / d["LB"] < 1e-6
    # 跨求解器偏差 > 15% 闸门
    assert any(r["rel_heave"] > 0.15 for r in a["cross_solver"])