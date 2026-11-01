"""船舶基准参考数据完整性闸门测试（闭合校验 + 解析锚 + 迁移后回归）。

判据全部来自可手算复核的解析恒等式，不依赖任何外部服务（8001/8002 不可用时也能跑）:
  H1 ITTC-57 平板摩擦线（Re=1e6 → 0.075/16 = 4.6875e-3）
  H2 矩形浮箱静水力学（KB=T/2, BM=B²/12T）
  H3 总阻力定义 Rt = Ct·½ρV²S
  H4 排水量闭合 Δ = Cb·L·B·T·ρ
  H5 Fr = V/√(gL)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.benchmark_integrity import (  # noqa: E402
    FAIL, PASS, WARN, WITHHELD,
    audit_case, audit_dataset, audit_proxy_table, check_displacement_closure,
    check_form_bands, check_friction_floor, check_froude_closure,
    check_resistance_closure, dataset_path, implied_wetted_surface,
    ittc57_cf, pontoon_hydrostatics,
)

DATA = dataset_path()
LEGACY = BACKEND_DIR / "data" / "benchmark_reference_v1_legacy.json"


# ---------------------------------------------------------------------------
# 解析锚
# ---------------------------------------------------------------------------

def test_ittc57_anchor_re_1e6():
    """H1: Re=1e6 → 0.075/(6-2)² = 4.6875e-3（ITTC-57 表值 0.00469）。"""
    assert abs(ittc57_cf(1.0e6) - 0.0046875) < 1e-12


def test_ittc57_monotonic_and_physical():
    """Re 增大 Cf 单调下降，且量级落在 1e-3 附近。"""
    res = [ittc57_cf(r) for r in (1e5, 1e6, 1e7, 1e8, 1e9)]
    assert res == sorted(res, reverse=True)
    assert 0.0012 < res[-1] < 0.0020


def test_pontoon_hydrostatics_anchor():
    """H2: L=80,B=20,T=4 的矩形浮箱 KM = 2 + 400/48 = 10.3333 m（精确解）。"""
    h = pontoon_hydrostatics(80.0, 20.0, 4.0)
    assert abs(h["kb"] - 2.0) < 1e-12
    assert abs(h["bm"] - 400.0 / 48.0) < 1e-12
    assert abs(h["km"] - (2.0 + 400.0 / 48.0)) < 1e-12


def test_resistance_identity_roundtrip():
    """H3: S_imp = Rt/(Ct·½ρV²) 与正向 Rt = Ct·½ρV²S 互为逆。"""
    s = 5.42
    rt = 0.0038 * 0.5 * 1025.0 * 3.0 ** 2 * s
    assert abs(implied_wetted_surface(0.0038, 3.0, rt) - s) < 1e-9


# ---------------------------------------------------------------------------
# 检查器行为：能抓到"错"、能放过"对"
# ---------------------------------------------------------------------------

def test_froude_closure_accepts_consistent_row():
    # SUBOFF: V=3.0, L=4.356, Fr=0.459
    c = check_froude_closure(3.0, 4.356, 0.459)
    assert c["status"] == PASS


def test_froude_closure_rejects_mixed_scale_row():
    # v1 事故行: DTMB-5415 记 30 kn 全尺度航速 15.433 m/s，却配 5.72 m 船模 L
    c = check_froude_closure(15.433, 5.72, 0.366)
    assert c["status"] == FAIL
    assert c["computed"] > 2.0          # Fr≈2.06 —— 排水型船模不可能
    assert abs(c["implied_length_m"] - 181.2) < 1.0   # 反解隐含船长


def test_displacement_closure_rejects_v1_kvlcc2():
    c = check_displacement_closure(320.0, 58.0, 20.8, 0.8098, 307000.0)
    assert c["status"] == FAIL
    assert abs(c["derived_t"] - 320437.2) < 1.0


def test_displacement_closure_rejects_v1_barge():
    # v1 ship_proxy 的 generic_barge: Δ=10000 t 与 Cb=0.85/LBT 矛盾 44%
    c = check_displacement_closure(80.0, 20.0, 4.0, 0.85, 10000.0)
    assert c["status"] == FAIL
    assert abs(c["derived_t"] - 5576.0) < 1.0


def test_resistance_closure_rejects_v1_suboff_rt():
    # v1: Ct=0.0038, V=3.0, S=5.42 应得 95 N，记录 12.5 N
    c = check_resistance_closure(0.0038, 3.0, 12.5, 4.356, 0.508, wetted_surface_m2=5.42,
                                 form_type="body_of_revolution", diameter=0.508)
    assert c["status"] == FAIL
    assert abs(c["derived_rt_n"] - 95.0) < 0.5


def test_resistance_closure_implied_area_band():
    # KVLCC2 v1: Ct=0.0028, V=7.202, Rt=550 kN → S_imp/(L·B)≈0.40，低于物理下限
    c = check_resistance_closure(0.0028, 7.202, 550000.0, 320.0, 58.0)
    assert c["status"] == FAIL
    assert c["dimensionless_ratio"] < 1.0


def test_friction_floor():
    assert check_friction_floor(0.0038, 3.0, 4.356)["status"] == PASS
    # 低于平板摩擦的总阻力系数在物理上不存在
    assert check_friction_floor(0.0010, 7.2, 320.0)["status"] == FAIL


def test_form_bands_flags_absurd_ratio():
    assert check_form_bands("displacement_hull", 320.0, 58.0, 20.8, 0.8098)["status"] == PASS
    bad = check_form_bands("displacement_hull", 320.0, 58.0, 2.0, 0.8098)  # B/T=29
    assert bad["status"] == FAIL


# ---------------------------------------------------------------------------
# 数据回归：v1（事故快照）必须被判 FAIL；v2（迁移后）不得有 FAIL 行
# ---------------------------------------------------------------------------

def test_legacy_v1_snapshot_is_rejected():
    if not LEGACY.exists():
        pytest.skip("v1 快照缺失")
    rep = audit_dataset(LEGACY)
    assert rep["total"] == 12
    assert rep["verdict_counts"][FAIL] == 12
    assert rep["binding"] is False
    failing_checks = {c["check"] for row in rep["rows"] for c in row["checks"]
                      if c["status"] == FAIL}
    assert {"froude_closure", "displacement_closure", "resistance_closure"} <= failing_checks


def test_current_dataset_has_no_fail_row():
    rep = audit_dataset(DATA)
    assert rep["total"] == 12
    assert rep["verdict_counts"][FAIL] == 0, rep["failing_cases"]
    assert rep["binding"] is True
    assert rep["dataset_version"] == "3.0"   # m1561：v3.0 正典（六分支裁决后）


def test_current_dataset_froude_rows_closed():
    data = json.loads(DATA.read_text())
    for case in data["cases"]:
        geo = case["geometry"]
        fr = case["speed_ms"] / math.sqrt(9.81 * geo["length_m"])
        assert abs(fr - case["froude_number"]) < 0.02 * case["froude_number"], case["case_id"]


def test_current_dataset_displacement_rows_closed():
    """排水量必须按行内流体密度闭合（m1561：写死 1025 正是 SUBOFF 95.0 N 的根因）。"""
    data = json.loads(DATA.read_text())
    for case in data["cases"]:
        if case.get("form_type") == "body_of_revolution":
            continue
        geo = case["geometry"]
        rho = case["reference_density_kg_m3"]
        disp_t = geo.get("displacement_t", (geo.get("displacement_kg") or 0) / 1000.0)
        derived = geo["cb"] * geo["length_m"] * geo["beam_m"] * geo["draft_m"] * rho / 1000.0
        assert abs(derived - disp_t) <= 0.02 * disp_t, (case["case_id"], derived, disp_t, rho)


def test_legacy_values_preserved_for_audit():
    """迁移不得丢原值：v1 值必须仍在 legacy 字段/快照里。"""
    data = json.loads(DATA.read_text())
    by_id = {c["case_id"]: c for c in data["cases"]}
    assert by_id["KVLCC2-01"]["geometry"]["legacy_displacement_t"] == 307000.0
    # v3.0：被 v2 藏在顶层的原始记录值统一收进 legacy_values（不再散落两套口径）
    assert by_id["DTMB5415-03"]["legacy_values"]["filed_speed_ms"] == 10.289
    assert by_id["SUBOFF-01"]["legacy_values"]["v1_experimental_rt_n"] == 12.5
    assert by_id["SUBOFF-01"]["legacy_values"]["superseded_experimental_rt_n"] == 95.0
    # 裁决值 = 淡水密度复算值（不是 v2 的 95.0，也不是 v1 的 12.5）
    assert abs(by_id["SUBOFF-01"]["experimental_rt_n"] - 92.52) < 0.01


def test_withheld_marks_where_evidence_missing():
    """缺湿面积的船型不得凭空补 Rt：必须标 WITHHELD 而不是编数字。"""
    data = json.loads(DATA.read_text())
    for case in data["cases"]:
        if case["ship_type"] in ("KVLCC2", "DTMB-5415"):
            assert case["experimental_rt_n"] is None
            assert case["rt_status"] == WITHHELD


# ---------------------------------------------------------------------------
# 代理表（ship_proxy.BENCHMARK_REFERENCE）同样要过闸门
# ---------------------------------------------------------------------------

def test_proxy_table_passes_after_fix():
    from services.ship_proxy import BENCHMARK_REFERENCE

    rep = audit_proxy_table(BENCHMARK_REFERENCE)
    assert rep["verdict_counts"][FAIL] == 0, rep["failing_cases"]
    # 仍保留 honest-FAIL 语义与宽容差
    sub = BENCHMARK_REFERENCE["resistance"]["suboff_model"]
    assert sub["tolerance_pct"] >= 300 and "honest-FAIL" in sub["note"]


def test_proxy_barge_gm_consistency_warned_not_failed():
    from services.ship_proxy import BENCHMARK_REFERENCE

    rep = audit_proxy_table(BENCHMARK_REFERENCE)
    row = next(r for r in rep["rows"] if r["case_id"] == "stability/generic_barge")
    statuses = {c["check"]: c["status"] for c in row["checks"]}
    assert statuses["gm_cap"] == PASS
    assert statuses["gm_consistency"] == WARN