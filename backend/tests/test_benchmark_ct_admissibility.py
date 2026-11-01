"""Ct 级判据门（m1704）——参考行"无量纲 Ct 列能否作精度标尺"的回归测试。

背景
----
正典参考数据集 ``data/benchmark_reference.json`` v3.0 的 12 行里只有 4 行 SUBOFF
是 ``CONSISTENT``；DTMB-5415 / KVLCC2 共 8 行被记为 ``PARTIAL``，唯一原因是
``resistance_closure``（C3：Rt == Ct·½ρV²S）**缺湿面积 S** 而 WITHHELD。

因此这 8 行在 m1704 之前对任何精度口径都不可用 —— 平台对全船型阻力的精度状况
实际上处于"无判据"状态。可是 C3 判的是**牛顿值** Rt；精度对标用的是无量纲 Ct，
而 ``Ct = Rt/(½ρV²S)`` 里 S 在模型/参考两侧同时约去。缺 S 不该封锁 Ct 级结论。

本组测试锁死三件事：
1. 闸门确实打开：8 行 PARTIAL 行拿到 ``ct_level="CT"``，4 行 SUBOFF 拿到 ``CT+RT``；
2. 闸门不空转：Fr / 摩擦下限 / 形体带任一项非 PASS 时 Ct 列一并封锁（合成反例）；
3. 不粉饰、不造数：Ct 级结论**不得**把 Rt 级 ``withheld`` 翻案，也**不得**在数据集里
   凭空补一个湿面积把 PARTIAL 洗成 CONSISTENT。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.ship_benchmark import (  # noqa: E402
    BenchmarkValidateRequest,
    _load_benchmark_data,
    benchmark_validate,
)
from services.benchmark_dataset_audit import (  # noqa: E402
    CONSISTENT,
    case_status_map,
    load_and_assess,
)
from services.benchmark_integrity import (  # noqa: E402
    CT_LEVEL_BOTH,
    CT_LEVEL_CT,
    CT_LEVEL_NONE,
    PASS,
    WARN,
    WITHHELD,
    ct_admissibility,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _canon_rows() -> dict:
    return case_status_map(load_and_assess(_load_benchmark_data()))


def _audited(checks: dict) -> dict:
    """构造 audit_case 形状的最小字典：checks 为 {name: status}。"""
    return {"checks": [{"check": k, "status": v} for k, v in checks.items()]}


# ---------------------------------------------------------------------------
# 1. 闸门打开：缺 S 的行拿到 CT 级判据
# ---------------------------------------------------------------------------

def test_partial_rows_become_ct_admissible():
    rows = _canon_rows()
    for case_id in ("DTMB5415-01", "KVLCC2-01"):
        row = rows[case_id]
        assert row["status"] != CONSISTENT, "缺 S 的行不应被判为 CONSISTENT"
        assert row["withheld"] is True
        assert "resistance_closure" in row["checks_not_run"]
        assert row["ct_admissible"] is True
        assert row["ct_level"] == CT_LEVEL_CT
        assert row["rt_closure_status"] == WITHHELD
        assert row["ct_gate_blockers"] == []


def test_suboff_keeps_both_evidence_levels():
    rows = _canon_rows()
    for case_id in ("SUBOFF-01", "SUBOFF-04"):
        assert rows[case_id]["status"] == CONSISTENT
        assert rows[case_id]["ct_level"] == CT_LEVEL_BOTH
        assert rows[case_id]["ct_admissible"] is True


def test_dataset_level_counts():
    """12 行全部可做 Ct 级对标；只有 4 行可做 Rt 级声明。"""
    rows = _canon_rows()
    assert len(rows) == 12
    assert sum(1 for r in rows.values() if r["ct_admissible"]) == 12
    assert sum(1 for r in rows.values() if r["ct_level"] == CT_LEVEL_BOTH) == 4
    assert sum(1 for r in rows.values() if r["ct_level"] == CT_LEVEL_CT) == 8


# ---------------------------------------------------------------------------
# 2. 闸门不空转：合成反例必须关门
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("broken", ["froude_closure", "friction_floor", "form_bands"])
def test_gate_closes_when_any_pillar_not_pass(broken):
    checks = {
        "froude_closure": PASS,
        "friction_floor": PASS,
        "form_bands": PASS,
        "resistance_closure": WITHHELD,
    }
    checks[broken] = WARN
    out = ct_admissibility(_audited(checks))
    assert out["ct_admissible"] is False
    assert out["ct_level"] == CT_LEVEL_NONE
    assert any(b.startswith(broken) for b in out["ct_gate_blockers"])


def test_gate_closes_on_fail_even_when_rt_closure_passes():
    """Rt 闭合 PASS 也不能救回一个形体带 FAIL 的行（Ct 归属本身可疑）。"""
    out = ct_admissibility(_audited({
        "froude_closure": PASS,
        "friction_floor": PASS,
        "form_bands": "FAIL",
        "resistance_closure": PASS,
    }))
    assert out["ct_admissible"] is False
    assert out["ct_level"] == CT_LEVEL_NONE


def test_gate_closes_when_pillar_withheld():
    out = ct_admissibility(_audited({
        "froude_closure": WITHHELD,
        "friction_floor": PASS,
        "form_bands": PASS,
        "resistance_closure": WITHHELD,
    }))
    assert out["ct_level"] == CT_LEVEL_NONE
    assert out["ct_gate_blockers"] == ["froude_closure:WITHHELD"]


def test_absent_check_is_not_a_blocker():
    """回转体没有 displacement_closure 这类检查：缺项 ≠ 未通过。"""
    out = ct_admissibility(_audited({
        "froude_closure": PASS,
        "friction_floor": PASS,
        "resistance_closure": WITHHELD,
    }))
    assert out["ct_admissible"] is True
    assert out["ct_level"] == CT_LEVEL_CT


# ---------------------------------------------------------------------------
# 3. 不粉饰：Ct 级不得翻案 Rt 级，也不得靠造湿面积洗数据
# ---------------------------------------------------------------------------

def test_ct_level_never_upgrades_rt_claim():
    rows = _canon_rows()
    for case_id, row in rows.items():
        if row["ct_level"] == CT_LEVEL_CT:
            assert row["status"] != CONSISTENT, case_id
            assert row["withheld"] is True, case_id
        if row["ct_level"] == CT_LEVEL_BOTH:
            assert row["status"] == CONSISTENT, case_id


def test_no_wetted_surface_invented_in_dataset():
    """闸门只做判定，不得往正典数据里回填湿面积把 PARTIAL 洗成 CONSISTENT。"""
    data = _load_benchmark_data()
    for case in data["cases"]:
        if case["ship_type"] in ("DTMB-5415", "KVLCC2"):
            s = (case.get("geometry") or {}).get("wetted_surface_m2")
            assert s is None, f"{case['case_id']} 被回填了未溯源的湿面积 {s}"


# ---------------------------------------------------------------------------
# 4. API 层：Ct 级口径进入 benchmark_validate
# ---------------------------------------------------------------------------

def test_endpoint_ct_scope_dtmb():
    resp = benchmark_validate(BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
    d = resp.model_dump(by_alias=True)
    s = d["summary"]
    assert s["ct_admissible_cases"] == 4
    assert s["ct_admissible_passed"] == 3
    assert s["pass_rate_ct"] == pytest.approx(75.0)
    # Rt 级声明照旧全部撤下（缺 S）
    assert s["verified_cases"] == 0
    assert s["rt_claimable_cases"] == 0
    assert all(r["withheld"] for r in d["results"])
    assert all(r["ct_evidence_level"] == CT_LEVEL_CT for r in d["results"])
    assert all(r["ct_level_pass"] == (r["error_pct"] <= 15.0) for r in d["results"])


def test_endpoint_kvlcc2_honest_fail():
    resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
    s = resp.model_dump(by_alias=True)["summary"]
    assert s["ct_admissible_cases"] == 4
    assert s["ct_admissible_passed"] == 0
    assert s["pass_rate_ct"] == 0.0
    assert "ct_level_recommendation" in s  # 全超判据 → 显式建议换方法
    assert all(r["error_pct"] > 15.0 for r in resp.model_dump()["results"])


def test_endpoint_suboff_unchanged():
    """SUBOFF 是闸门改动前唯一可用口径：m1704 不得改变它的结论。"""
    d = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")).model_dump(by_alias=True)
    s = d["summary"]
    assert s["verified_cases"] == 4 and s["verified_passed"] == 3
    assert s["pass_rate_verified"] == pytest.approx(75.0)
    assert s["ct_admissible_cases"] == 4 and s["ct_admissible_passed"] == 3
    assert s["rt_claimable_cases"] == 4
    assert [r["case_id"] for r in d["results"]] == [f"SUBOFF-0{i}" for i in range(1, 5)]


def test_ct_scope_is_strictly_wider_but_not_looser():
    """Ct 口径覆盖 12 行 ≥ Rt 口径 4 行；而每一行仍按同一个 15% 判据裁。"""
    totals = {"ct": 0, "verified": 0}
    for st in ("SUBOFF", "DTMB-5415", "KVLCC2"):
        s = benchmark_validate(BenchmarkValidateRequest(ship_type=st, benchmark_id="all")).model_dump()["summary"]
        totals["ct"] += s["ct_admissible_cases"]
        totals["verified"] += s["verified_cases"]
    assert totals == {"ct": 12, "verified": 4}