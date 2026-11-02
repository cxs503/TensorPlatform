"""破舱稳性 A 指数 p 因子审计的机器可裁回归测试（离线，只读工件 + 只读被测件）。

设计意图：本测试锁的是**缺陷指纹**（A 可由调用方制造 / A 与分区数成线性 / p 与几何解耦），
不是「缺陷已修」的期望。一旦 ship-performance-platform 落地 p 因子溯源（补丁草案见
artifacts/verif/damage_stability_a/damage_stability_a_index.patch），这些用例会转红，
从而强制重新出判决 —— 这是闸门，不是可放宽的软测。

运行：python3 -m pytest tests/verif/test_damage_stability_a_index.py -q
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
EV = os.path.join(ROOT, "artifacts", "verif", "damage_stability_a", "evidence.json")
PATCH = os.path.join(ROOT, "artifacts", "verif", "damage_stability_a",
                     "damage_stability_a_index.patch")
SUT_REL = "backend/api/damage_stability.py"
sys.path.insert(0, os.path.join(ROOT, "tools", "verif", "damage_stability_a"))


@pytest.fixture(scope="module")
def ev():
    assert os.path.exists(EV), "先跑 python3 tools/verif/damage_stability_a/audit_a_index.py"
    with open(EV) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def crit(ev):
    return dict((c["id"], c) for c in ev["criteria"])


@pytest.fixture(scope="module")
def sut():
    import audit_a_index as A
    return A.load_sut()


# --------------------------------------------------------------------------- #
# L1 工件结构（每判据必须带实测/参照/出处/判定）
# --------------------------------------------------------------------------- #
def test_evidence_structure(ev):
    ids = [c["id"] for c in ev["criteria"]]
    assert ids == ["A1", "A2", "A3", "A4", "A5", "A6", "G1", "G2"]
    for c in ev["criteria"]:
        assert isinstance(c["pass"], bool), c["id"]
        assert "platform" in c and "reference" in c, c["id"]
        assert c["reference"].get("src"), c["id"]  # 每条判据必须可溯源
    assert ev["meta"]["n_pass"] == sum(1 for c in ev["criteria"] if c["pass"])
    assert ev["meta"]["verdict"]["probabilistic.A_index"] == "WITHHELD"


def test_verdict_matches_criteria(ev):
    """判决与判据自洽：A 族 4 条全败 ⇒ A_index 必须 WITHHELD。"""
    a_fail = [c for c in ev["criteria"] if c["id"].startswith("A")
              and c["id"] in ("A1", "A2", "A3", "A4") and not c["pass"]]
    assert len(a_fail) == 4
    assert ev["meta"]["verdict"]["damage.s_factor"] == "REAL"


# --------------------------------------------------------------------------- #
# L2 缺陷指纹（从 evidence.json 复算，独立于被测件）
# --------------------------------------------------------------------------- #
def test_A1_attained_index_can_exceed_one(crit):
    """Σp=1.8 时平台返回 A>1 且仍判 compliant —— 概率加权和越界。"""
    p = crit["A1"]["platform"]
    assert p["A_value"] > 1.0
    assert p["compliant"] is True
    assert crit["A1"]["pass"] is False


def test_A2_A_scales_with_zone_count(crit):
    """A 随分区数线性增长：A = 0.1·N（缺省 p=0.1/区）。"""
    rows = crit["A2"]["platform"]["rows"]
    for r in rows:
        assert r["A_value"] == pytest.approx(0.1 * r["n_zones"], abs=1e-9)
    assert crit["A2"]["platform"]["p_sum_values"] == [0.2, 0.4, 0.8]


def test_A3_A_linear_in_caller_p(crit):
    """A 对调用方 p 标度严格线性 ⇒ p 是输入回声。"""
    p = crit["A3"]["platform"]
    assert p["max_linear_resid"] < 1e-9
    assert p["A_linear_slope"] > 0
    assert p["s_invariant_under_k"] is True
    assert p["first_compliant_k"] is not None
    # 首个合规点必须与 R/斜率 一致（自洽校验）
    assert p["first_compliant_k"] == pytest.approx(p["flip_k_analytic"], rel=0.5)


def test_A4_p_decoupled_from_damage_geometry(crit):
    """损伤纵向范围 10 m→45 m，A 完全不变。"""
    p = crit["A4"]["platform"]
    assert p["delta_A"] <= 1e-6
    assert p["A_length10"] == p["A_length45"]


# --------------------------------------------------------------------------- #
# L3 解析锚点（独立复算，不读被测件内部量）
# --------------------------------------------------------------------------- #
def test_G1_wallsided_gz_closed_form(crit):
    """完整 GZ 必须复现壁式闭式解 GZ = sinφ (GM + ½ BM tan²φ)。"""
    g = crit["G1"]
    phi = np.linspace(g["platform"]["phi_grid_deg"][0], g["platform"]["phi_grid_deg"][1],
                      len(g["platform"]["gz_platform"]))
    phi_rad = np.radians(phi)
    ref = np.sin(phi_rad) * (g["platform"]["GM"] + 0.5 * g["platform"]["BM"] * np.tan(phi_rad) ** 2)
    plat = np.array(g["platform"]["gz_platform"])
    assert np.max(np.abs(plat - ref)) < 1e-3          # m
    assert g["platform"]["max_rel_dev"] < 1e-3
    # GM 自洽：GM = KB + BM - KG - GG0
    assert g["platform"]["GM"] == pytest.approx(
        g["platform"]["KB"] + g["platform"]["BM"] - 8.0 - g["platform"]["GG0"], rel=1e-9)


def test_A6_s_branch_properties(sut, crit):
    """s 三分支式的性质面（独立网格复算）+ 中间支遮蔽门槛。"""
    gz = np.arange(0.0, 0.3001, 0.005)
    rg = np.arange(0.0, 30.01, 0.5)
    S = np.array([[sut.solas2009_s_index(g, r) for r in rg] for g in gz])
    assert np.all(np.diff(S, axis=0) >= -1e-12)      # 对 GZmax 单调不减
    assert np.all(np.diff(S, axis=1) >= -1e-12)      # 对 Range 单调不减
    assert S.min() >= 0.0 and S.max() <= 1.0
    assert sut.solas2009_s_index(0.25, 25.0) == 1.0
    assert sut.solas2009_s_index(0.09, 25.0) == 0.0
    # 中间支遮蔽的解析门槛：Range >= 16*0.12/0.10 = 19.2 deg
    assert crit["A6"]["platform"]["intermediate_hidden_above_range_deg"] == pytest.approx(19.2)
    share = float(np.mean((S > 1e-9) & (S < 1.0 - 1e-9)))
    assert share == pytest.approx(crit["A6"]["platform"]["intermediate_share_of_grid"], abs=1e-6)


def test_A5_s_responds_to_kg_monotonically(crit):
    rows = crit["A5"]["platform"]["rows"]
    s = [r["s_first_zone"] for r in rows]
    assert all(s[i] >= s[i + 1] - 1e-12 for i in range(len(s) - 1))
    assert len(set(s)) >= 2
    assert 0.5 not in set(s)


# --------------------------------------------------------------------------- #
# L4 补丁草案可落地（不修改被测件）
# --------------------------------------------------------------------------- #
def test_patch_is_present_and_applies_cleanly():
    assert os.path.exists(PATCH), "补丁草案缺失"
    txt = open(PATCH).read()
    assert "--- a/backend/api/damage_stability.py" in txt
    assert "p_source" in txt and "p_sum" in txt
    r = subprocess.run(["git", "apply", "--check", PATCH], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr