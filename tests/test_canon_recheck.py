"""m1767 验证欠账：正典阻力数据集 v3.0 独立复核的回归测试。

判据（全部解析，零平台依赖）：
  J1 R3 Froude 闭合 Fr=V/√(gL)，容差 2%
  J2 R1 阻力恒等式 Rt=Ct·½ρV²S，容差 1%（仅当 S 与 Rt 俱在）
  J3 摩擦下限 Ct ≥ Cf(ITTC-57)
  J5 文献湿面积锚：SUBOFF S=5.42 m² (Roddy 1990)，容差 2%
  J6 KVLCC2 v3.0 缺口钉住：rt_status=WITHHELD 且无湿面积（m1751 v3.1 合并前保持）
"""
import json
import math
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANON = os.path.join(REPO, "backend", "data", "benchmark_reference.json")
LEDGER = os.path.join(REPO, "docs", "reports", "m1767", "canon_recheck_ledger.json")
G = 9.81


@pytest.fixture(scope="module")
def canon():
    return json.load(open(CANON))


@pytest.fixture(scope="module")
def rows(canon):
    return canon["cases"]


def ittc57_cf(re):
    return 0.075 / (math.log10(re) - 2.0) ** 2


def test_canon_version_is_v30(canon):
    assert canon["version"] == "3.0"
    assert len(canon["cases"]) == 12


def test_j1_froude_closure_all_rows(rows):
    for c in rows:
        g = c["geometry"]
        fr_calc = c["speed_ms"] / math.sqrt(G * g["length_m"])
        err = abs(fr_calc - c["froude_number"]) / c["froude_number"]
        assert err <= 0.02, f"{c['case_id']}: Fr closure {err:.4%} > 2%"


def test_j2_r1_identity_suboff(rows):
    n = 0
    for c in rows:
        S = c["geometry"].get("wetted_surface_m2")
        rt = c.get("experimental_rt_n")
        if S and rt:
            rt_calc = c["experimental_ct"] * 0.5 * c["reference_density_kg_m3"] * c["speed_ms"] ** 2 * S
            err = abs(rt_calc - rt) / rt
            assert err <= 0.01, f"{c['case_id']}: R1 {err:.4%} > 1%"
            n += 1
    assert n == 4  # SUBOFF 四行


def test_j3_friction_floor_all_rows(rows):
    for c in rows:
        re_ = c["speed_ms"] * c["geometry"]["length_m"] / c["reference_kinematic_viscosity_m2_s"]
        cf = ittc57_cf(re_)
        assert c["experimental_ct"] >= cf, f"{c['case_id']}: Ct below ITTC-57 floor"


def test_j5_suboff_wetted_surface_rod1990(rows):
    for c in rows:
        if c["ship_type"] == "SUBOFF":
            assert abs(c["geometry"]["wetted_surface_m2"] - 5.42) / 5.42 <= 0.02


def test_kvlcc2_v30_gap_pinned(rows):
    """v3.0 已知缺口：KVLCC2 缺湿面积 → Rt 恒等式不可运行 → WITHHELD。
    m1751（v3.1，SIMMAN 2008 S≈2.7e4 m²）合并后本测试须更新。"""
    kvl = [c for c in rows if c["ship_type"] == "KVLCC2"]
    assert len(kvl) == 4
    for c in kvl:
        assert c["rt_status"] == "WITHHELD"
        assert c["geometry"].get("wetted_surface_m2") is None
        assert c["experimental_rt_n"] is None


def test_ledger_tally_consistent(canon, rows):
    led = json.load(open(LEDGER))
    assert led["canon_version"] == "3.0"
    assert led["tally"]["FAIL"] == 0
    # 独立复算 PASS 数：J1 全行 + J2 SUBOFF 4 + J3 全行 + J5 SUBOFF 4
    n = len(rows) + 4 + len(rows) + 4
    assert led["tally"]["PASS"] == n
