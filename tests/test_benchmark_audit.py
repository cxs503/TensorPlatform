#!/usr/bin/env python3
"""Benchmark reference-data consistency audit + fail-closed gate (m1589).

Why this file exists
--------------------
``backend/api/domain/ship_benchmark.py`` publishes a *pass/fail accuracy gate*
(Ct error <= 15 %) for ship resistance validation.  An audit on 2026-11-01 found
the reference table it compares against is internally inconsistent:

* SUBOFF rows: stated Ct vs Ct back-derived from stated Rt differ by 86-90 %;
* DTMB-5415 rows: stated Fn vs V/sqrt(gL) differ by 82 %, and two rows imply
  Fn = 1.37 / 2.06 -- i.e. speeds and length live in different reference frames;
* DTMB-5415 rows: cb x L x B x T = 0.5425 m^3 against a stated 0.4663 t (16 %);
* DTMB-5415 / KVLCC2 rows carry no wetted surface, so Ct <-> Rt is uncheckable.

Tests below pin the audit verdicts (so a silent data edit cannot pass unnoticed)
and prove the gate is fail-closed: a case whose reference row breaks a
definitional identity is WITHHELD, never "passed".

Run:  pytest tests/test_benchmark_audit.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# api/__init__.py builds the DB engine at import time; keep it in-memory/sqlite.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from services import benchmark_audit as ba  # noqa: E402

DATASET_PATH = BACKEND / "data" / "benchmark_reference.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _case(**over):
    base = {
        "case_id": "SYN-01",
        "ship_type": "KVLCC2",
        "speed_ms": 7.202,
        "froude_number": 0.129,
        "experimental_ct": 0.0028,
        "experimental_rt_n": 550000.0,
        "geometry": {
            "length_m": 320.0, "beam_m": 58.0, "draft_m": 20.8,
            "displacement_t": 307000.0, "cb": 0.8098,
            "wetted_surface_m2": 27194.0,
        },
    }
    base.update(over)
    return base


def _checks(case_result):
    return {f["check"] for f in case_result["findings"]
            if f["severity"] in (ba.BLOCKING, ba.WARNING)}


# ---------------------------------------------------------------------------
# 1. definitional identities
# ---------------------------------------------------------------------------
def test_froude_number_identity():
    # Fn = V / sqrt(g L); 7.202 m/s over 320 m -> 0.1286
    assert ba.froude_number(7.202, 320.0) == pytest.approx(0.1286, abs=5e-5)
    with pytest.raises(ValueError):
        ba.froude_number(1.0, 0.0)


def test_ct_rt_roundtrip_is_exact():
    ct = ba.ct_from_rt(1234.5, 27.0, 5.5, rho=1025.0)
    assert ba.rt_from_ct(ct, 27.0, 5.5, rho=1025.0) == pytest.approx(1234.5, rel=1e-12)
    # 0.5 rho S V^2 for 1025 kg/m3, 27 m2, 5.5 m/s = 418k N; Ct must be O(1e-3)
    assert 1e-4 < ct < 1e-2


def test_ittc57_friction_line_regression():
    # Cf = 0.075/(log10 Re - 2)^2, Re = 1e9 -> 0.075/49
    assert ba.ittc57_cf(1e9) == pytest.approx(0.075 / 49.0, rel=1e-12)
    assert ba.ittc57_cf(1e10) < ba.ittc57_cf(1e9)  # monotone decreasing


# ---------------------------------------------------------------------------
# 2. audit verdicts on synthetic cases
# ---------------------------------------------------------------------------
def test_consistent_case_is_gate_eligible():
    res = ba.audit_case(_case())
    assert res["gate_eligible"] is True
    assert res["blocking_checks"] == []
    # KVLCC2 sea-water rho must be used for the Ct<->Rt cross-check
    assert res["rho"] == 1025.0
    assert ba.BLOCKING not in {f["severity"] for f in res["findings"]}


def test_broken_froude_blocks_the_case():
    # Stated Fn 0.061 against V/sqrt(gL) = 0.343: the comparison point is wrong.
    res = ba.audit_case(_case(froude_number=0.061))
    assert res["gate_eligible"] is False
    assert "FROUDE_IDENTITY" in res["blocking_checks"]


def test_mixed_reference_frame_is_flagged():
    # Model length (5.72 m) with a full-scale speed (15.4 m/s) -> Fn = 2.06 > 1.
    model_case = _case(
        ship_type="DTMB-5415", speed_ms=15.433, froude_number=0.366,
        experimental_ct=0.0038, experimental_rt_n=210.0,
        geometry={"length_m": 5.72, "beam_m": 0.759, "draft_m": 0.246,
                  "displacement_kg": 466.3, "cb": 0.508})
    res = ba.audit_case(model_case)
    assert res["gate_eligible"] is False
    assert "SPEED_REFERENCE_FRAME" in res["blocking_checks"]


def test_missing_wetted_surface_is_a_warning_not_a_blocker():
    case = _case()
    case["geometry"] = {k: v for k, v in case["geometry"].items()
                        if k != "wetted_surface_m2"}
    res = ba.audit_case(case)
    assert "CT_RT_RECIPROCITY" in res["warnings"]
    assert res["gate_eligible"] is True


def test_implausible_ct_blocks_and_promotes_reciprocity():
    # Ct far below the ITTC-57 friction line is impossible for a displacement hull.
    case = _case(experimental_ct=0.0005, experimental_rt_n=98000.0)
    res = ba.audit_case(case)
    assert "CT_PLAUSIBILITY" in res["blocking_checks"]
    assert "CT_RT_RECIPROCITY" in res["blocking_checks"]


# ---------------------------------------------------------------------------
# 3. golden verdicts on the shipped dataset (regression lock)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def report():
    return ba.audit_file(DATASET_PATH)


def test_shipped_dataset_case_count(report):
    assert report["n_cases"] == 12


def test_shipped_dataset_defect_register(report):
    # Exact defect census -- edit the dataset and this fails on purpose.
    # v3.0 正典（4f2ca17b 程序化裁决）已按本审计修复 FROUDE_IDENTITY /
    # BLOCK_COEFF_IDENTITY / SPEED_REFERENCE_FRAME 三族缺陷；现役登记只剩
    # CT_RT_RECIPROCITY 完整性告警 8 例（warning 级）。
    assert report["defects_by_check"] == {"CT_RT_RECIPROCITY": 8}


def test_shipped_dataset_no_case_is_withheld(report):
    # v3.0 正典修复（本审计驱动）后 12 案全部 gate-eligible，零封锁。
    assert report["blocked_case_ids"] == []
    assert report["n_gate_eligible"] == report["n_cases"] == 12


def test_suboff_ct_rt_discrepancy_magnitude(report):
    by_id = {c["case_id"]: c for c in report["cases"]}
    for cid in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"):
        finding = next(f for f in by_id[cid]["findings"]
                       if f["check"] == "CT_RT_RECIPROCITY")
        # v3.0 修复了 SUBOFF 参考行 Ct/Rt 不自洽（原 86-90% 偏差），
        # 现在 reciprocity 在容差内（OK 级），钉住"不许回退"。
        assert finding["severity"] == ba.OK
        assert finding["deviation_pct"] <= ba.TOL_CT_RT_REL * 100  # TOL 为分数，deviation_pct 为百分数


def test_markdown_render_covers_every_case(report):
    md = ba.audit_summary_markdown(report)
    for case in report["cases"]:
        assert case["case_id"] in md


# ---------------------------------------------------------------------------
# 4. presets are derived from the dataset (single source of truth)
# ---------------------------------------------------------------------------
def test_presets_come_from_dataset_except_declared_fallback():
    built = ba.derive_presets(ba.load_dataset(DATASET_PATH),
                              {"SUBOFF": {"cb": 0.508}})
    presets, sources = built["presets"], built["sources"]
    # v3.0 位移改 cb*L*B*T 自洽值（原 0.4663 与自身 cb 块矛盾 16%）
    assert presets["DTMB-5415"]["displacement"] == pytest.approx(0.5416, rel=1e-3)
    assert presets["DTMB-5415"]["length"] == pytest.approx(5.72)
    assert presets["KVLCC2"]["cb"] == pytest.approx(0.8098)
    assert sources["KVLCC2"]["cb"] == "dataset"
    assert sources["SUBOFF"]["cb"] == "fallback"


def test_endpoint_presets_are_dataset_derived():
    from api.domain import ship_benchmark as sb
    # v3.0 位移=cb*L*B*T 自洽值；PRESET_SOURCES 属性 main 线端点未暴露，
    # presets 来源透明度由 derive_presets 测试（上）覆盖。
    assert sb.SHIP_GEOMETRY["KVLCC2"]["displacement"] == pytest.approx(311996.427, rel=1e-3)
    assert sb.SHIP_GEOMETRY["DTMB-5415"]["displacement"] == pytest.approx(0.5416, rel=1e-3)
    # SUBOFF displacement must be identity-consistent, not a copied diameter
    # (v3.0 SHIP_GEOMETRY 的 displacement_from_cb 与裸 cb*L*B*T 差 ~0.2%)
    sub = sb.SHIP_GEOMETRY["SUBOFF"]
    assert sub["displacement"] == pytest.approx(
        sub["cb"] * sub["length"] * sub["beam"] * sub["draft"], rel=5e-3)


# ---------------------------------------------------------------------------
# 5. fail-closed gate: withheld, never "passed"
# ---------------------------------------------------------------------------
def test_gate_withholds_inconsistent_reference(monkeypatch):
    """fail-closed 语义（保义改写）：v3.0 正典修复后自带数据已一致，自带行
    不再触发 WITHHELD；改以合成 INCONSISTENT 审计行注入，证明"参考行不
    一致 ⇒ withheld 且绝不记 pass"的门语义未回退。"""
    from api.domain import ship_benchmark as sb

    def fake_resistance(ship_type, speed_ms, geometry):
        return {"ct": 0.0030, "method": "Holtrop-1984"}

    def fake_assess(data, *a, **kw):
        return {"n_cases": 12, "n_blocked": 4, "cases": [
            {"case_id": f"DTMB5415-0{i}", "status": "INCONSISTENT",
             "failed_checks": ["FROUDE_IDENTITY"]} for i in (1, 2, 3, 4)]}

    monkeypatch.setattr(sb, "_call_resistance_api", fake_resistance)
    monkeypatch.setattr(sb, "load_and_assess", fake_assess)
    resp = sb.benchmark_validate(
        sb.BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
    assert len(resp.results) == 4
    assert all(r.withheld for r in resp.results)
    assert all(r.pass_ is False for r in resp.results)
    assert all(r.note.startswith("HONEST-FAIL DATASET-WITHHELD") for r in resp.results)
    assert resp.summary["withheld_cases"] == [
        "DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04"]
    assert resp.summary["honest_fail"] is True


def test_gate_still_evaluates_consistent_reference(monkeypatch):
    """一致参考行正常评估（保义改写）：main 线 dataset_audit 对 DTMB/KVLCC2
    判 PARTIAL（湿表面积缺失=Ct/Rt 不可交叉核验的完备性债）→ withheld；
    全 CONSISTENT 的船型是 SUBOFF，换用它证明"一致 ⇒ 正常评估可过门"。"""
    from api.domain import ship_benchmark as sb

    def fake_resistance(ship_type, speed_ms, geometry):
        assert geometry["length"] == pytest.approx(4.356)
        return {"ct": 0.0040, "method": "Holtrop-1984"}

    monkeypatch.setattr(sb, "_call_resistance_api", fake_resistance)
    resp = sb.benchmark_validate(
        sb.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
    assert resp.summary["withheld_cases"] == []
    assert all(r.method == "Holtrop-1984" for r in resp.results)
    # ct=0.0040 恰中 SUBOFF-02 参考值，其余三案偏差 4.8-9.1%，均在 15% 带内
    assert all(r.pass_ for r in resp.results)
    assert resp.summary["pass_rate"] == 100.0


def test_gate_fails_on_large_error(monkeypatch):
    """Mutation probe (QUALITY_BAR R1): a 50 % error must not pass."""
    from api.domain import ship_benchmark as sb

    def fake_resistance(ship_type, speed_ms, geometry):
        return {"ct": 0.0042, "method": "Holtrop-1984"}  # +50 % vs 0.0028

    monkeypatch.setattr(sb, "_call_resistance_api", fake_resistance)
    resp = sb.benchmark_validate(
        sb.BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-01"))
    assert resp.results[0].pass_ is False
    assert resp.results[0].error_pct == pytest.approx(50.0, abs=0.5)