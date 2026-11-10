"""AC4: the readiness mechanism must stay free of domain vocabulary and must
degrade to an identity gate when its configuration is missing.

The forbidden vocabulary lives in configuration (never in code); this test reads
it at runtime and scans the package + its tests. When the configuration file is
absent the audit is a no-op (identity), which is asserted explicitly.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.dispatch_readiness import DispatchGate, load_contracts  # noqa: E402
from tools.dispatch_readiness.audit_zero_domain import audit, load_terms  # noqa: E402
from tools.dispatch_readiness.backtest import run_backtest  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = os.path.join(REPO_ROOT, "backend", "tools", "dispatch_readiness")
TESTS = os.path.join(REPO_ROOT, "backend", "tests", "test_dispatch_readiness.py")


def test_terms_config_exists_and_has_entries():
    terms = load_terms()
    assert terms is not None, "config/dispatch_readiness_domain_terms.json missing"
    assert terms.get("substring_terms") or terms.get("word_terms")


def test_package_and_tests_are_domain_free():
    result = audit([PKG, TESTS])
    assert result["status"] == "clean", result["violations"][:5]
    assert result["files_scanned"] >= 5


def test_missing_terms_config_degrades_to_identity():
    result = audit([PKG], terms_path=os.path.join(REPO_ROOT, "config", "does-not-exist.json"))
    assert result["status"] == "degraded_identity"
    assert result["violations"] == []


def test_audit_does_catch_planted_vocabulary(tmp_path):
    terms = load_terms()
    planted = (terms.get("substring_terms") or terms.get("word_terms"))[0]
    target = tmp_path / "sample.py"
    target.write_text(f'VALUE = "{planted}"\n', encoding="utf-8")
    result = audit([str(target)])
    assert result["status"] == "violations"
    assert result["violations"][0]["token"] == planted


def test_missing_contract_config_keeps_dispatch_working(tmp_path):
    gate = DispatchGate(
        nodes=[{"key": "1"}, {"key": "2", "node_type": "task", "dependencies": ["1"]}],
        contracts_path=str(tmp_path / "nope.json"),
    )
    assert load_contracts(str(tmp_path / "nope.json")) == {}
    verdict = gate.preflight("2", {})
    # no declared configuration -> structural inference still applies (declared mode)
    assert verdict["decision"] == "defer"
    assert verdict["status"] == "NOT_READY"


def test_missing_label_config_keeps_backtest_alive(tmp_path):
    snapshot = {"nodes": [{"id": 1, "key": "1", "status": "done", "node_type": "task",
                           "dependencies": [], "mission_id": None, "created_at": "2026-01-01T00:00:00+00:00"}],
                "missions": []}
    body = run_backtest(snapshot, label_rules={}, permutation_iterations=5, seed=1)
    assert body["targets"]["delivery_gap_consult"]["n_labeled"] == 0
    assert body["report_sha256"]


def test_report_json_declares_artefact_paths(tmp_path):
    """The backtest report must be serialisable and carry its provenance/limits."""
    snapshot = {"provenance": {"sql_sha256": "x", "snapshot_sha256": "y"}, "counts": {}, "nodes": [], "missions": []}
    body = run_backtest(snapshot, label_rules={"upstream_cause_needles": ["upstream"]}, permutation_iterations=5, seed=2)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "dispatch-readiness-backtest/1.0"
    assert "label_definitions" in loaded and "frozen_thresholds" in loaded