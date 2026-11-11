"""判定级联规则测试（合成静态 verdict + 合成执行证据，不依赖真实台账）。"""
from tools.capability_audit_v2.cascade_v2 import adjudicate, agreement, tally

THRESH = {"probe_min_contradictions": 1, "exec_numeric_assertions_min": 1}


def _static(cap_id="cap.x", level="WITHHELD", reasons=None, warnings=None):
    return {"capability": cap_id, "level": level, "reasons": list(reasons or []),
            "warnings": list(warnings or [])}


def _exec(pytest_status="NOT_BOUND", contradictions=None, numeric=0, bound=True, probe_status=None):
    probe = {"bound": bound, "status": probe_status or ("OK" if contradictions is not None else "NOT_BOUND"),
             "contradictions": list(contradictions or []), "checks": []}
    return {"pytest": {"bound": bound, "status": pytest_status, "tests": 3, "passed": 3,
                       "failed": 0, "numeric_assertions": numeric, "failing_tests": []},
            "probe": probe}


def test_probe_contradiction_wins_over_static_withheld():
    v = adjudicate(_static(level="WITHHELD", reasons=["NO_BENCHMARK"]),
                   _exec(pytest_status="PASSED", contradictions=["I_x"], numeric=3), THRESH)
    assert v["level"] == "MOCK"
    assert v["reason_code"] == "EXEC_INVARIANT_VIOLATED"
    assert v["rule"] == 3


def test_stub_is_terminal():
    v = adjudicate(_static(level="STUB", reasons=["STUB_DOMINATED"]), _exec(), THRESH)
    assert v["level"] == "STUB" and v["rule"] == 2


def test_missing_code_is_terminal():
    v = adjudicate(_static(level="NOT_SUPPORTED", reasons=["BINDING_TARGET_MISSING"]), _exec(), THRESH)
    assert v["level"] == "NOT_SUPPORTED" and v["rule"] == 1


def test_static_mock_kept():
    v = adjudicate(_static(level="MOCK", reasons=["HARDCODE_MARKERS"]), _exec(), THRESH)
    assert v["level"] == "MOCK" and v["reason_code"] == "HARDCODE_MARKERS"


def test_test_failure_withholds_but_records_failing_tests():
    v = adjudicate(_static(level="WITHHELD", reasons=["NO_BENCHMARK"]),
                   _exec(pytest_status="TEST_FAILED", numeric=2), THRESH)
    assert v["level"] == "WITHHELD"
    assert v["reason_code"] == "EXEC_TEST_FAILED"


def test_environment_error_never_downgrades_capability():
    v = adjudicate(_static(level="WITHHELD", reasons=["NO_BENCHMARK"]),
                   _exec(pytest_status="ENV_ERROR", numeric=2), THRESH)
    assert v["level"] == "WITHHELD"
    assert v["reason_code"] == "EXEC_ENV_ERROR"


def test_real_requires_passing_exec_evidence():
    ok = adjudicate(_static(level="REAL", reasons=["NUMERIC_VERIFICATION_PASS"]),
                    _exec(pytest_status="PASSED", numeric=4), THRESH)
    assert ok["level"] == "REAL"
    failed = adjudicate(_static(level="REAL", reasons=["NUMERIC_VERIFICATION_PASS"]),
                        _exec(pytest_status="TEST_FAILED", numeric=4), THRESH)
    assert failed["level"] == "WITHHELD" and failed["reason_code"] == "EXEC_TEST_FAILED"
    skipped = adjudicate(_static(level="REAL", reasons=["NUMERIC_VERIFICATION_PASS"]),
                         _exec(pytest_status="SKIPPED", numeric=4), THRESH)
    assert skipped["level"] == "WITHHELD" and skipped["reason_code"] == "EXEC_EVIDENCE_MISSING"


def test_real_downgraded_when_exec_numeric_assertions_missing():
    v = adjudicate(_static(level="REAL", reasons=["NUMERIC_VERIFICATION_PASS"]),
                   _exec(pytest_status="PASSED", numeric=0), THRESH)
    assert v["level"] == "WITHHELD" and v["reason_code"] == "EXEC_NON_NUMERIC"


def test_static_withheld_reason_preserved():
    v = adjudicate(_static(level="WITHHELD", reasons=["NO_EXEC_BINDING"]), _exec(), THRESH)
    assert v["level"] == "WITHHELD" and v["reason_code"] == "NO_EXEC_BINDING"


def test_tally_and_agreement_with_majority_baseline():
    verdicts = [
        {"capability": "a", "level": "REAL"},
        {"capability": "b", "level": "MOCK"},
        {"capability": "c", "level": "WITHHELD"},
        {"capability": "d", "level": "WITHHELD"},
    ]
    labels = {"a": "REAL", "b": "MOCK", "c": "WITHHELD", "d": "REAL"}
    assert tally(verdicts) == {"REAL": 1, "MOCK": 1, "STUB": 0, "WITHHELD": 2, "NOT_SUPPORTED": 0}
    agg = agreement(verdicts, labels)
    assert agg["n"] == 4 and agg["matched"] == 3
    assert agg["agreement"] == 0.75
    assert agg["majority_baseline"] == 0.5
    assert agg["mismatches"] == [{"capability": "d", "audited": "WITHHELD", "label": "REAL"}]