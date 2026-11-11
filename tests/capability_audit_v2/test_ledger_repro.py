"""台账工件测试：规范化 JSON、证据索引、两次运行可复现。"""
from pathlib import Path

from tools.capability_audit_v2 import cli
from tools.capability_audit_v2.ledger_v2 import canonical_json, digest_of, evidence_index

ROOT = Path(__file__).resolve().parents[2]
SPEC = "tests/capability_audit_v2/fixtures/spec_exec.json"
LABELS = "tests/capability_audit_v2/fixtures/labels.json"
PATHS = "tests/capability_audit_v2/fixtures/upgrade_paths.json"


def test_canonical_json_is_key_ordered():
    a = {"b": 1, "a": [2, 3]}
    b = {"a": [2, 3], "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert digest_of(a) == digest_of(b)
    assert canonical_json(a) == '{"a":[2,3],"b":1}'


def test_evidence_index_is_sorted_and_shas():
    idx = evidence_index(["tests/capability_audit_v2/fixtures/impl_core.py",
                          "tests/capability_audit_v2/fixtures/bench.json"], ROOT)
    assert list(idx) == sorted(idx)
    assert all(len(v) == 64 for v in idx.values())


def test_second_pipeline_run_has_identical_digest(pipeline_result):
    second = cli._run_once(SPEC, ROOT, LABELS, PATHS, None)
    assert pipeline_result["digest"] == second["digest"]
    assert pipeline_result["ledger"] == second["ledger"]
    assert pipeline_result["evidence_sha"] == second["evidence_sha"]


def test_ledger_has_no_volatile_fields(pipeline_result):
    text = canonical_json(pipeline_result["ledger"])
    for volatile in ("generated_at", "runtime_sec", "timestamp", "duration"):
        assert volatile not in text


def test_synthetic_verdicts_and_agreement(pipeline_result):
    levels = {c["id"]: c["level"] for c in pipeline_result["ledger"]["capabilities"]}
    assert levels == {"cap.alpha": "REAL", "cap.beta": "MOCK", "cap.gamma": "STUB",
                      "cap.delta": "NOT_SUPPORTED", "cap.epsilon": "WITHHELD"}
    assert pipeline_result["ledger"]["summary"]["exec_bindings"]["pytest"] == 3
    agree = pipeline_result["agreement"]
    assert agree["n"] == 5
    assert agree["agreement"] == 1.0
    assert agree["majority_baseline"] == 0.2


def test_evidence_index_covers_probe_and_test_sources(pipeline_result):
    idx = pipeline_result["evidence_sha"]
    assert "tests/capability_audit_v2/fixtures/impl_core.py" in idx
    assert "tests/capability_audit_v2/fixtures/case_pass.py" in idx
    assert "tests/capability_audit_v2/fixtures/spec_base.json" in idx