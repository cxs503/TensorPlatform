#!/usr/bin/env python3
"""tests/test_benchmark_settlement.py — 对标决算复算（m2054）的判据测试。

原则：先造判据再动手。每个断言都能杀一个具体缺陷：
  * 误差一律从原始数字复算 → 手改 calc/ref/error 任一都会被检出；
  * 正典 policy（WITHHELD 不出判决）→ 越权判决必须计数；
  * 三档判据边界闭区间 → 3.0001%/5.0001%/15.0001% 必须掉档；
  * 分母为 0 → None（未实测），不许返回 0.0 冒充达标率。
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import benchmark_settlement as bs  # noqa: E402


def _case(cid="C-01", ct=0.004, rt=92.52, status="DERIVED", rho=998.2, v=3.0, s=5.42):
    return {"case_id": cid, "ship_type": "SUBOFF", "speed_ms": v,
            "experimental_ct": ct, "experimental_rt_n": rt,
            "reference_status": status, "rt_status": status,
            "reference_density_kg_m3": rho,
            "geometry": {"wetted_surface_m2": s}, "legacy_values": {}}


def _result(cid="C-01", calc=0.004, ref=0.004, typed=0.0, passed=True):
    return {"case_id": cid, "ship_type": "SUBOFF", "speed_ms": 3.0,
            "calc_ct": calc, "ref_ct": ref, "error_pct": typed, "pass": passed,
            "method": "unit-test"}


# ── 误差复算 ────────────────────────────────────────────────────────────
def test_rel_err_sign_and_zero_guard():
    assert bs.rel_err_pct(0.0041, 0.004) == pytest.approx(2.5)
    assert bs.rel_err_pct(0.0039, 0.004) == pytest.approx(-2.5)  # 负误差必须保留符号
    with pytest.raises(ZeroDivisionError):
        bs.rel_err_pct(1.0, 0.0)


@pytest.mark.parametrize("err,expected", [
    (0.0, "PASS_3PCT"), (3.0, "PASS_3PCT"), (3.0001, "PASS_5PCT"),
    (5.0, "PASS_5PCT"), (5.0001, "FAIL_5PCT"), (15.0, "FAIL_5PCT"),
    (15.0001, "FAIL_OVER_15"), (-68.9, "FAIL_OVER_15"),
])
def test_verdict_boundaries_closed(err, expected):
    assert bs.verdict_of(err) == expected


# ── 单例决算 ────────────────────────────────────────────────────────────
def test_settle_row_recomputes_and_flags_sign_loss():
    case = _case()
    row = bs.settle_row(_result(calc=0.0041, ref=0.004, typed=2.5, passed=True), case)
    assert row["err_pct"] == pytest.approx(2.5)
    assert row["verdict"] == "PASS_3PCT"
    assert row["admissible"] is True and row["policy_violation"] is False
    # 手写字段丢了符号（-2.5 写成 2.5）→ 必须标出
    row2 = bs.settle_row(_result(calc=0.0039, ref=0.004, typed=2.5, passed=True), case)
    assert row2["err_pct"] == pytest.approx(-2.5) and row2["sign_lost"] is True
    assert row2["typed_field_trusted"] is False


def test_withheld_rows_must_not_be_adjudicated():
    case = _case(cid="W-01", rt=None, status="WITHHELD")
    row = bs.settle_row(_result(cid="W-01", calc=0.0029, ref=0.0028, typed=3.06, passed=True), case)
    assert row["verdict"] == "WITHHELD"
    assert row["admissible"] is False
    assert row["policy_violation"] is True          # 源工件照样出了判决 → 越权
    assert row["status"] == "INADMISSIBLE"


def test_stale_reference_detected():
    case = _case(ct=0.0038)
    row = bs.settle_row(_result(calc=0.0064, ref=0.0039, typed=64.0, passed=False), case)
    assert row["ref_stale"] is True and row["ref_stale_pct"] == pytest.approx(2.6316, abs=1e-3)
    fresh = bs.settle_row(_result(calc=0.0064, ref=0.0038, typed=68.89, passed=False), case)
    assert fresh["ref_stale"] is False


def test_missing_canon_case_is_not_silently_dropped():
    row = bs.settle_row(_result(cid="GHOST-99"), None)
    assert row["status"] == "NO_CANON_REFERENCE" and row["verdict"] == "WITHHELD"


def test_canon_case_without_settlement_counted_not_settled():
    canon = {"cases": {"A-01": _case(cid="A-01")}, "meta": {}, "policy": ""}
    rows = bs.settle(canon, {"results": []})
    assert rows[0]["status"] == "NOT_SETTLED" and rows[0]["admissible"] is True


# ── 正典 R1 恒等式自检（正典自己也是被验证对象） ─────────────────────────
def test_r1_identity_ok_and_detects_injected_error():
    case = _case(ct=0.0038, rt=92.52)  # SUBOFF-01 真值组合
    chk = bs.r1_identity(case)
    assert chk["ok"] is True and abs(chk["rel_err_pct"]) < 0.5
    bad = copy.deepcopy(case)
    bad["experimental_rt_n"] = 95.0                 # v3 作废的旧值 → R1 必须报警
    assert bs.r1_identity(bad)["ok"] is False


# ── 汇总折算 ────────────────────────────────────────────────────────────
def test_summarize_rates_are_none_when_no_admissible_samples():
    withheld = _case(cid="W", rt=None, status="WITHHELD")
    rows = bs.settle({"cases": {"W": withheld}, "meta": {}, "policy": ""},
                     {"results": [_result(cid="W", calc=0.003, ref=0.0028, typed=7.1, passed=True)]})
    s = bs.summarize(rows)
    assert s["admissible"] == 0 and s["settled_admissible"] == 0
    assert s["within5_rate"] is None and s["qualified_rate"] is None
    assert s["policy_violations"] == 1
    payload = {"summary": s}
    kpi = bs.kpi_metrics(payload)
    assert kpi["resistance_accuracy"] is None        # 未实测，不是 0.0
    assert kpi["benchmark_cases"] == 0.0


def test_qualified_requires_admissible_and_within_hard_tol():
    ok = _case(cid="OK")
    far = _case(cid="FAR")
    canon = {"cases": {"OK": ok, "FAR": far}, "meta": {}, "policy": ""}
    rows = bs.settle(canon, {"results": [
        _result(cid="OK", calc=0.0041, ref=0.004, typed=2.5, passed=True),    # +2.5% → 合格
        _result(cid="FAR", calc=0.0064, ref=0.004, typed=60.0, passed=False),  # +60% → 不合格
    ]})
    s = bs.summarize(rows)
    assert s["admissible"] == 2 and s["settled_admissible"] == 2
    assert s["within5"] == 1 and s["qualified_cases"] == 1
    assert s["within5_rate"] == pytest.approx(0.5)
    assert s["source_verdict_mismatch"] == 0


# ── 真实工件回归（现场数据，机器数字，防口径漂移） ────────────────────────
@pytest.mark.skipif(not (ROOT / "backend/data/benchmark_reference.json").exists()
                    or not (ROOT / "artifacts/benchmark_validation.json").exists(),
                    reason="正典/决算工件不在本树")
def test_live_audit_invariants():
    payload = bs.audit(bs.load_canon(), bs.load_settlement())
    s = payload["summary"]
    assert s["n_rows"] == 12
    assert s["r1_failed"] == 0, "正典 R1 恒等式不得失败"
    # 正典 policy：8 例 WITHHELD / 4 例 SUBOFF DERIVED
    assert (s["admissible"], s["inadmissible"]) == (4, 8)
    # 源工件 M398 在 WITHHELD 行上出了判决 → 越权判决数必须被抓住
    assert s["policy_violations"] == 8
    # KPI#9/#10 的机器口径：可判决行全部超 15%，达标率 0（honest-FAIL）
    assert s["qualified_cases"] == 0
    assert s["within5_rate"] == 0.0 and s["max_abs_err_pct"] > 15.0
    # Rt 层决算：计算侧从未产出 Rt → 0（这是尚未清偿的欠账，不许假装已决算）
    assert s["rt_level_settled"] == 0


@pytest.mark.skipif(not (ROOT / "backend/data/benchmark_reference.json").exists(),
                    reason="正典不在本树")
def test_live_render_and_artifact_roundtrip(tmp_path):
    payload = bs.audit(bs.load_canon(), bs.load_settlement())
    md = bs.render_md(payload)
    assert "policy 越权判决" in md and "SUBOFF-01" in md
    out = bs.write_artifacts(payload, tmp_path)
    assert pathlib.Path(out["json"]).exists()
    reg = bs.load_registry(pathlib.Path(out["json"]))
    assert reg["kpi"]["benchmark_cases"] == float(payload["summary"]["qualified_cases"])


# ── 与 KPI 台账接线 ─────────────────────────────────────────────────────
def test_registry_feeds_guide_kpi_ledger():
    spec = importlib.util.spec_from_file_location(
        "guide_kpi_ledger", ROOT / "tools" / "guide_kpi_ledger.py")
    kpi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kpi)
    ledger = kpi.load_ledger()
    reg = {"kpi": {"benchmark_cases": 0.0, "resistance_accuracy": 0.0},
           "summary": {"qualified_cases": 0, "settled_admissible": 4, "policy_violations": 8}}
    recs = {r["key"]: r for r in kpi.build_records({}, ledger, registry=reg)}
    assert recs["benchmark_cases"]["kind"] == "registry"
    assert recs["benchmark_cases"]["value"] == 0.0
    assert recs["resistance_accuracy"]["value"] == 0.0
    assert "registry" in recs["benchmark_cases"]["provenance"]
    assert "8" in recs["resistance_accuracy"]["status_text"]
    # 工件缺失 → 未实测（None + stale），绝不回落手写字面量
    recs2 = {r["key"]: r for r in kpi.build_records({}, ledger, registry=None)}
    assert recs2["benchmark_cases"]["value"] is None
    assert recs2["benchmark_cases"]["stale"] is True