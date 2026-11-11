"""可执行证据采集器测试：pytest 执行器 + 不变量探针执行器。"""
from pathlib import Path

from tools.capability_audit_v2.exec_evidence import run_probe, run_pytest
from tools.capability_audit_v2.spec_v2 import load_spec_v2

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec_v2("tests/capability_audit_v2/fixtures/spec_exec.json", ROOT)
FIX = "tests/capability_audit_v2/fixtures"
MARKERS = SPEC["execution"]["env_markers"]


def _pytest(selectors):
    return run_pytest("cap.x", {"selectors": selectors, "timeout_s": 120}, ROOT, 120, MARKERS)


def test_passing_selector_collected():
    ev = _pytest([f"{FIX}/case_pass.py"])
    assert ev["status"] == "PASSED"
    assert ev["tests"] == 2 and ev["passed"] == 2
    assert ev["numeric_assertions"] >= 2
    assert len(ev["digest"]) == 64


def test_assertion_failure_classified():
    ev = _pytest([f"{FIX}/case_fail.py"])
    assert ev["status"] == "TEST_FAILED"
    assert ev["failed"] == 1
    assert ev["failing_tests"] and "expected_assertion_failure" in ev["failing_tests"][0]


def test_environment_failure_not_treated_as_capability_failure():
    ev = _pytest([f"{FIX}/case_env.py"])
    assert ev["status"] == "ENV_ERROR"
    assert ev["env_marker_hit"] is True


def test_missing_selector_file_is_collection_error():
    ev = _pytest([f"{FIX}/absent_case.py"])
    assert ev["status"] == "COLLECTION_ERROR"
    assert ev["missing_selector_files"] == [f"{FIX}/absent_case.py"]


def test_pytest_evidence_is_deterministic():
    a = _pytest([f"{FIX}/case_pass.py"])
    b = _pytest([f"{FIX}/case_pass.py"])
    assert a == b


def _probe(cap_id):
    return run_probe(cap_id, SPEC["exec"][cap_id]["probe"], ROOT, 1e-12, MARKERS)


def test_probe_passes_on_admissible_kernel():
    ev = _probe("cap.alpha")
    assert ev["status"] == "OK"
    assert ev["contradictions"] == []
    assert {c["id"] for c in ev["checks"]} == {"I_eta_identity", "I_second_parameter", "I_determinism"}


def test_probe_refutes_collapsed_kernel():
    ev = _probe("cap.beta")
    assert ev["status"] == "OK"
    assert ev["contradictions"] == ["I_second_parameter"]
    check = [c for c in ev["checks"] if c["id"] == "I_second_parameter"][0]
    assert check["value"] == 0.0 and check["pass"] is False


def test_control_probe_clean_while_subject_fails():
    control = run_probe("cap.beta", SPEC["exec"]["cap.beta"]["control_probe"], ROOT, 1e-12, MARKERS)
    assert control["status"] == "OK" and control["contradictions"] == []


def test_probe_sensitivity_controls_other_parameters():
    """敏感度必须在"其余参数固定"的组内测，不能被其它变量的变化放大。"""
    pts = SPEC["exec"]["cap.beta"]["probe"]["points"]
    assert len({(p["a"], p["b"]) for p in pts}) == len(pts)


def test_probe_unknown_target_reports_error_without_raising():
    entry = dict(SPEC["exec"]["cap.alpha"]["probe"])
    entry["target"] = "impl_core:no_such_function"
    ev = run_probe("cap.alpha", entry, ROOT, 1e-12, MARKERS)
    assert ev["status"] == "ERROR"
    assert "no_such_function" in ev["detail"]
    assert ev["contradictions"] == []