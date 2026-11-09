"""IN99-N10(d) 证据台账：自动登记 + S 级评价触发 + 查询 + 哈希链审计追溯（判据 C4）。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from ops.taskplane.governance import evidence_ledger as led


def _record(mission_id=5001, cap="gov.demo", *, accepted=True, criteria=(True, True, True),
            baseline=None, declares=False, artifacts=()):
    return {
        "mission_id": mission_id, "capability_id": cap, "category": "new_capability",
        "accepted": accepted,
        "acceptance": [{"id": f"C{i+1}", "passed": p, "value": 1.0 if p else 0.0,
                        "threshold": 1.0} for i, p in enumerate(criteria)],
        "baseline": baseline,
        "change": {"declares_breakthrough": declares},
        "artifacts": list(artifacts),
        "evidence": {"report_id": 9000 + mission_id},
    }


BASE = {"metric": "recall", "reference": 0.80, "measured": 0.92}


@pytest.fixture()
def ledger(tmp_path):
    return tmp_path / "capability_ledger.sqlite"


# ─────────────────────────── S 级评价 ───────────────────────────

def test_grade_s3_requires_breakthrough_and_artifacts():
    v = led.evaluate_grade(_record(baseline=BASE, declares=True, artifacts=["a.py"]))
    assert v.grade == "S3" and v.requires_independent_review
    assert "8%" in v.grade_reason or "0.12" in v.grade_reason      # 理由带数字
    assert v.baseline["delta"] == pytest.approx(0.12)


def test_grade_s2_without_breakthrough_claim():
    assert led.evaluate_grade(_record(baseline=BASE)).grade == "S2"
    assert led.evaluate_grade(_record(baseline=BASE, artifacts=["a.py"])).grade == "S2"


def test_grade_s1_without_baseline():
    v = led.evaluate_grade(_record())
    assert v.grade == "S1" and not v.requires_independent_review
    assert "无基线" in v.grade_reason


def test_no_grade_when_failed_or_too_few_criteria():
    assert led.evaluate_grade(_record(accepted=False)).grade is None
    assert led.evaluate_grade(_record(criteria=(True, False, True), baseline=BASE)).grade is None
    few = led.evaluate_grade(_record(criteria=(True, True), baseline=BASE))
    assert few.grade is None and "下限" in few.grade_reason


def test_baseline_must_be_numeric_to_count():
    bogus = {"metric": "recall", "reference": "n/a", "measured": 0.9}
    assert led.evaluate_grade(_record(baseline=bogus)).grade == "S1"


# ─────────────────────────── C4：登记 / 查询 / 追溯 ───────────────────────────

def test_register_then_query_and_index(ledger):
    r3 = led.register(ledger, _record(5001, "gov.dedup", baseline=BASE, declares=True,
                                      artifacts=["ops/taskplane/governance/proposal_dedup.py"]))
    r2 = led.register(ledger, _record(5002, "gov.dag", baseline=BASE))
    r1 = led.register(ledger, _record(5003, "gov.gate"))
    assert [r3.grade_verdict.grade, r2.grade_verdict.grade, r1.grade_verdict.grade] == \
        ["S3", "S2", "S1"]
    assert all(r.code == "REGISTERED" for r in (r1, r2, r3))

    assert [e["capability_id"] for e in led.query(ledger)] == ["gov.dedup", "gov.dag", "gov.gate"]
    assert len(led.query(ledger, capability_id="gov.dag")) == 1
    assert [e["capability_id"] for e in led.query(ledger, grade="S3")] == ["gov.dedup"]
    assert len(led.query(ledger, mission_id=5003)) == 1
    index = led.capability_index(ledger)
    assert index["gov.dag"]["latest_grade"] == "S2" and index["gov.dedup"]["highest_grade"] == "S3"
    events = led.s_events(ledger)
    assert [e["grade"] for e in events] == ["S3", "S2", "S1"]
    assert [e["requires_independent_review"] for e in events] == [True, True, False]


def test_chain_links_and_verify(ledger):
    a = led.register(ledger, _record(5001, "gov.a"))
    b = led.register(ledger, _record(5002, "gov.b"))
    chain = led.verify_chain(ledger)
    assert chain["ok"] and chain["n_entries"] == 2
    assert a.entry["prev_hash"] == led.GENESIS
    assert b.entry["prev_hash"] == a.entry["entry_hash"]
    trace = led.trace(ledger, b.entry["entry_id"])
    assert trace["ok"] and trace["seq"] == 2 and trace["chain"]["ok"]


def test_tampering_breaks_chain(ledger, tmp_path):
    led.register(ledger, _record(5001, "gov.a"))
    led.register(ledger, _record(5002, "gov.b", baseline=BASE))
    dirty = tmp_path / "dirty.sqlite"
    dirty.write_bytes(ledger.read_bytes())
    conn = sqlite3.connect(str(dirty))
    conn.execute("UPDATE ledger_entries SET reason='好' WHERE capability_id='gov.b'")
    conn.commit()
    conn.close()
    result = led.verify_chain(dirty)
    assert not result["ok"] and result["code"] == "CONTENT_TAMPERED"
    assert result["bad_entry"].startswith("5002:")


def test_trace_unknown_entry(ledger):
    led.register(ledger, _record(5001, "gov.a"))
    assert led.trace(ledger, "nope")["code"] == "NOT_FOUND"


# ─────────────────────────── 拒绝路径留痕（honest FAIL） ───────────────────────────

def test_rejections_logged_not_scored(ledger):
    r = led.register(ledger, _record(5004, "gov.x", accepted=False, criteria=(True, False, True)))
    assert r.code == "NOT_ACCEPTED" and r.entry is None
    r2 = led.register(ledger, _record(5005, "gov.x", criteria=(True, False, True)))
    assert r2.code == "CRITERIA_FAILED"
    assert led.query(ledger) == []                                   # 未入定额
    attempts = led.attempts(ledger)
    assert [a["code"] for a in attempts] == ["NOT_ACCEPTED", "CRITERIA_FAILED"]
    # accepted 列记"任务是否声称验收通过"：CRITERIA_FAILED 是声称通过但判据不全
    assert [a["accepted"] for a in attempts] == [0, 1]


def test_missing_fields_rejected(ledger):
    assert led.register(ledger, {"mission_id": 1, "category": "c", "accepted": True}).code == \
        "MISSING_CAPABILITY_ID"
    assert led.register(ledger, {"mission_id": 1, "capability_id": "c",
                                 "accepted": True}).code == "MISSING_CATEGORY"


def test_idempotent_registration_does_not_double_count(ledger):
    rec = _record(5006, "gov.idem", baseline=BASE, declares=True, artifacts=["a.py"])
    first = led.register(ledger, rec)
    second = led.register(ledger, rec)
    assert first.code == "REGISTERED" and second.code == "IDEMPOTENT"
    assert len(led.query(ledger, capability_id="gov.idem")) == 1
    assert led.verify_chain(ledger)["n_entries"] == 1


def test_ledger_survives_reopen(ledger):
    led.register(ledger, _record(5007, "gov.persist", baseline=BASE))
    reopened = led.capability_index(ledger)
    assert reopened["gov.persist"]["latest_grade"] == "S2"
    assert json.dumps(reopened, ensure_ascii=False)