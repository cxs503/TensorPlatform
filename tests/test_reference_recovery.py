"""m1641 真值可供给性重判的回归测试。

判据不是「账本里写了什么」，而是**独立重算**：每个断言都从归档数字重算一遍，
所以正典数据被改坏、带宽被放宽、或判定被悄悄提升时测试会红。

运行（沙箱缺 cryptography，无法加载 backend/tests/conftest.py）::

    PYTHONPATH=backend python3 -m pytest tests/test_reference_recovery.py --noconftest -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from services import benchmark_canon as bc
from services import reference_recovery as rr

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "backend" / "data" / "benchmark_reference.json"


@pytest.fixture(scope="module")
def doc():
    return json.loads(DATA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases(doc):
    return {c["case_id"]: c for c in doc["cases"]}


@pytest.fixture(scope="module")
def led():
    return rr.ledger()


# ---------------------------------------------------------------------------
# 1. R1 反解本身是精确的（往返闭合）
# ---------------------------------------------------------------------------

def test_implied_surface_round_trip():
    """由已知 S 正向算 Rt，再反解必须回到同一 S（相对误差 < 1e-12）。"""
    for ct, v, s, rho in ((0.0038, 3.0, 5.42, 998.2),
                          (0.0028, 7.202, 26717.34, 1025.0),
                          (0.0045, 0.4569, 4.5123, 998.2)):
        rt = rr.resistance_from_ct(ct, v, s, rho)
        assert rr.implied_surface(ct, v, rt, rho) == pytest.approx(s, rel=1e-12)


def test_implied_surface_rejects_zero_denominator():
    with pytest.raises(ValueError):
        rr.implied_surface(0.0, 3.0, 10.0, 998.2)


# ---------------------------------------------------------------------------
# 2. 船型湿面积估计：溯源必须是「回归/实测」，不能是硬编码文献数字
# ---------------------------------------------------------------------------

def test_hull_surface_basis_is_declared(cases):
    for cid, c in cases.items():
        est = rr.hull_surface_estimate(c)
        assert est["basis"] in {"holtrop_mennen_1982_regression",
                                "archived_measured_surface",
                                "axisymmetric_estimate"}, cid
        assert est["s_m2"] > 0.0, cid
        assert est["derivation"], cid


def test_surface_estimates_are_within_engineering_band(cases):
    """湿面积回归的合理性闸门（宽 3 倍带，只拦量级错误）。"""
    for cid, c in cases.items():
        s = rr.hull_surface_estimate(c)["s_m2"]
        geo = c["geometry"]
        length = geo["length_m"]
        if c["form_type"] == "body_of_revolution":
            ref = math.pi * geo["diameter_m"] * length
        else:
            ref = length * (2.0 * geo["draft_m"] + geo["beam_m"])
        assert 0.3 * ref < s < 3.0 * ref, cid


def test_suboff_surface_is_the_archived_measurement(cases):
    for cid in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"):
        est = rr.hull_surface_estimate(cases[cid])
        assert est["basis"] == "archived_measured_surface"
        assert est["s_m2"] == pytest.approx(5.42, rel=1e-12)


# ---------------------------------------------------------------------------
# 3. 判定与正典一致性：允许「补 S 救回」，但绝不允许把 WITHHELD 悄悄提升
# ---------------------------------------------------------------------------

def test_no_row_is_promoted_by_this_ledger(led, doc):
    usable = set(led["usable_cases"])
    canonical = set(rr.usable_rows(doc))
    assert usable == canonical
    assert led["usable_count"] == len(canonical)
    for row in led["rows"]:
        if row["canon_status"] == bc.WITHHELD:
            assert row["verdict"] != rr.USABLE, row["case_id"]


def test_usable_verdict_requires_identity_closure(led):
    for row in led["rows"]:
        if row["verdict"] == rr.USABLE:
            assert row["s_implied_m2"] is not None
            lo, hi = row["s_band_m2"]
            assert lo <= row["s_implied_m2"] <= hi, row["case_id"]


def test_suboff_rows_are_usable_and_close_identically(led):
    rows = {r["case_id"]: r for r in led["rows"]}
    for cid in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"):
        row = rows[cid]
        assert row["verdict"] == rr.USABLE
        assert row["s_ratio"] == pytest.approx(1.0, abs=5e-3)
        assert row["datum_request"] is None


# ---------------------------------------------------------------------------
# 4. 不可救回的行：证据必须量化（比值远超带宽），且不能被补 S 救
# ---------------------------------------------------------------------------

def test_irrecoverable_rows_are_far_outside_band(led):
    irr = [r for r in led["rows"] if r["verdict"] == rr.IRRECOVERABLE_FILED_RT]
    assert {r["case_id"] for r in irr} == {
        "DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04",
        "KVLCC2-01", "KVLCC2-02", "KVLCC2-03", "KVLCC2-04",
    }
    for r in irr:
        lo, hi = r["s_band_m2"]
        assert not (lo <= r["s_implied_m2"] <= hi), r["case_id"]
        # 至少偏离带宽边界 50%，才配得上「不可救」这个词
        assert (r["s_ratio"] < 1 - rr.SURFACE_BAND - 0.5
                or r["s_ratio"] > 1 + rr.SURFACE_BAND + 0.5), r["case_id"]
        assert r["datum_request"]["missing_datum"] == "experimental_rt_n"


def test_irrecoverable_verdict_is_robust_to_speed_reading(cases):
    """DTMB-5415 的航速有两种读法；两种读法下都必须判不可救回。"""
    for cid in ("DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04"):
        row = rr.adjudicate_case(cases[cid])
        assert row.speed_robust is True, cid
        lo, hi = row.s_band_m2
        assert not (lo <= row.s_implied_m2 <= hi)


def test_filed_rt_would_have_to_change_by_the_stated_factor(cases):
    """把「需要多大的 Rt 才自洽」写成数字：区间与归档值之比必须与 s_ratio 互为倒数。"""
    for cid in ("KVLCC2-01", "DTMB5415-01"):
        row = rr.adjudicate_case(cases[cid])
        lo, hi = row.rt_required_n
        assert not (lo <= row.filed_rt_n <= hi), cid  # 需要改的倍数必须 >1
        # rt_factor_center 在产物里按 4 位小数落盘，故用 1e-3 相对容差
        assert row.filed_rt_n / ((lo + hi) / 2) == pytest.approx(row.rt_factor_center, rel=1e-3)


# ---------------------------------------------------------------------------
# 5. 恒定因子检验：区分「系统性标度误用」与「Ct/Rt 混档」
# ---------------------------------------------------------------------------

def test_factor_consistency_separates_systematic_from_mixed(led):
    fc = led["factor_consistency"]
    assert fc["SUBOFF"]["unit_factor"] is True
    assert fc["KVLCC2"]["single_factor_explains"] is True
    assert fc["KVLCC2"]["unit_factor"] is False
    assert fc["DTMB-5415"]["single_factor_explains"] is False


def test_factor_consistency_is_recomputed_from_rows(led):
    for ship, block in led["factor_consistency"].items():
        rows = [r for r in led["rows"] if r["ship_type"] == ship]
        fs = [r["rt_factor_center"] for r in rows]
        assert block["rows"] == len(fs)
        assert block["min"] == pytest.approx(min(fs), rel=1e-6)
        assert block["max"] == pytest.approx(max(fs), rel=1e-6)
        assert block["spread_ratio"] == pytest.approx(max(fs) / min(fs), rel=1e-4)


# ---------------------------------------------------------------------------
# 6. 假设扫描：不得把「Rt 对了但航速是记录外的第三个值」当成接受
# ---------------------------------------------------------------------------

def test_hypotheses_flag_undocumented_speed_as_rt_only(cases):
    row = rr.adjudicate_case(cases["DTMB5415-01"])
    h1 = next(h for h in row.hypotheses if h["hypothesis"] == "H1_speed_in_knots")
    assert h1["implied_speed_documented"] is False
    assert h1["status"] == "RT_ONLY"


def test_kvlcc2_hypotheses_all_rejected(cases):
    row = rr.adjudicate_case(cases["KVLCC2-01"])
    assert row.hypotheses, "假设扫描缺失"
    assert all(h["status"] == "REJECTED" for h in row.hypotheses)


def test_no_hypothesis_is_accepted_for_irrecoverable_rows(led):
    for row in led["rows"]:
        if row["verdict"] == rr.IRRECOVERABLE_FILED_RT:
            assert not any(h["status"] == "ACCEPTED" for h in row["hypotheses"]), row["case_id"]


# ---------------------------------------------------------------------------
# 7. KPI 赤字是算出来的，不是手写的
# ---------------------------------------------------------------------------

def test_kpi_gap_is_derived(led):
    assert led["kpi_gap"] == max(led["kpi_target"] - led["usable_count"], 0)
    assert led["kpi_met"] == (led["usable_count"] >= led["kpi_target"])
    assert led["kpi_target"] == 10
    assert led["usable_count"] == 4
    assert led["kpi_gap"] == 6


def test_truth_budget_is_a_third_of_the_gate(led):
    assert led["truth_uncertainty_budget_pct"] == pytest.approx(rr.ACCURACY_GATE_PCT / 3.0)
    for row in led["rows"]:
        if row["datum_request"]:
            assert row["datum_request"]["required_uncertainty_pct"] == pytest.approx(
                led["truth_uncertainty_budget_pct"])
            assert row["datum_request"]["closes_row_because"]


def test_band_widening_would_promote_rows_that_must_stay_withheld(monkeypatch):
    """反向守卫：把带宽放宽到 4 倍就会把 KVLCC2 判成可救——
    所以带宽必须是声明过的常数，不能随调用者浮动。"""
    led_strict = rr.ledger()
    assert not led_strict["factor_consistency"]["KVLCC2"]["unit_factor"]
    monkeypatch.setattr(rr, "SURFACE_BAND", 0.80)
    led_loose = rr.ledger()
    promoted = [r["case_id"] for r in led_loose["rows"]
                if r["verdict"] == rr.RECOVERABLE_BY_SURFACE]
    assert promoted, "带宽放宽后应出现「可补 S 救回」的行（否则带宽没生效）"
    assert rr.SURFACE_BAND == 0.80  # monkeypatch 生效的证据