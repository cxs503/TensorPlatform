"""Assemble the P93W3-N10 machine acceptance verdicts from frozen artifacts.

Inputs (all produced by other tools in this directory — this file copies no value):
  runs/p93w3-n10/metrics.json      tools/p93w3_n10/evaluate.py
  runs/p93w3-n10/figures.json      tools/p93w3_n10/figures.py
  runs/p93w3-n10/pytest_junit.xml  the acceptance test run

Output: runs/p93w3-n10/ACCEPTANCE.json

Usage: python3 tools/p93w3_n10/acceptance.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p93w3-n10"


def load(name: str) -> dict:
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def pytest_summary() -> dict:
    path = RUN / "pytest_junit.xml"
    if not path.exists():
        return {"status": "not_run"}
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    attrs = suite.attrib if suite is not None else {}
    return {
        "status": "run",
        "tests": int(attrs.get("tests", 0)),
        "failures": int(attrs.get("failures", 0)),
        "errors": int(attrs.get("errors", 0)),
        "skipped": int(attrs.get("skipped", 0)),
        "junit": "runs/p93w3-n10/pytest_junit.xml",
    }


def main() -> None:
    metrics = load("metrics.json")
    figures = load("figures.json")
    tests = pytest_summary()

    acc = metrics["criteria"]["a_classification_accuracy"]
    lat = metrics["criteria"]["b_mvd_trigger_latency"]
    mig = metrics["criteria"]["c_backward_compat_migration"]
    lab = metrics["criteria"]["d_label_schema_validation"]

    criteria = {
        "a_terminal_state_accuracy": {
            "requirement": "four-state classification accuracy 1.00 over 20 scenarios",
            "measured_accuracy": acc["accuracy"],
            "n_scenarios": acc["n_scenarios"],
            "n_correct": acc["n_correct"],
            "passed": acc["accuracy"] == 1.0 and acc["n_scenarios"] == 20,
            "artifact": "runs/p93w3-n10/metrics.json#criteria.a_classification_accuracy",
        },
        "b_mvd_checkpoint_latency": {
            "requirement": "70% MVD checkpoint trigger latency <= 1 s",
            "measured_p100_ms": lat["p100_ms"],
            "measured_p50_ms": lat["p50_ms"],
            "n_calls": lat["n_calls"],
            "threshold_pinned_to_contract": lat["threshold_pinned_to_contract"],
            "passed": lat["p100_ms"] <= 1000.0 and lat["criterion_met"]
            and lat["threshold_pinned_to_contract"],
            "artifact": "runs/p93w3-n10/metrics.json#criteria.b_mvd_trigger_latency",
        },
        "c_backward_compat_zero_loss": {
            "requirement": "legacy succeeded migration loses no data",
            "records_in": mig["records_in"],
            "records_out": mig["records_out"],
            "rows_covered": mig["rows_covered"],
            "records_unmappable": mig["records_unmappable"],
            "violations": len(mig["violations"]),
            "passed": bool(mig["zero_data_loss"])
            and mig["records_in"] == mig["records_out"]
            and not mig["violations"]
            and sum(mig["state_counts"].values()) == mig["records_in"],
            "artifact": "runs/p93w3-n10/metrics.json#criteria.c_backward_compat_migration",
        },
        "d_label_schema_validation": {
            "requirement": "delivery-label schema validation pass rate 1.00",
            "n_labels": lab["n_labels"],
            "n_valid": lab["n_valid"],
            "pass_rate": lab["pass_rate"],
            "schema": lab["schema_path"],
            "passed": lab["pass_rate"] == 1.0 and lab["n_labels"] == metrics["corpus"]["records"],
            "artifact": "runs/p93w3-n10/metrics.json#criteria.d_label_schema_validation",
        },
    }
    support = {
        "pytest": tests,
        "pytest_clean": tests.get("status") == "run"
        and tests.get("failures") == 0 and tests.get("errors") == 0 and tests.get("tests", 0) > 0,
        "figures": {"n": len(figures["figures"]),
                    "digests": [f["sha256"] for f in figures["figures"]],
                    "metrics_digest_matches": figures["metrics_sha256"] == hashlib.sha256(
                        (RUN / "metrics.json").read_bytes()).hexdigest()},
        "corpus": {"sha256": metrics["corpus"]["sha256"], "records": metrics["corpus"]["records"]},
        "metrics_sha256": hashlib.sha256((RUN / "metrics.json").read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(
            (ROOT / "backend/contracts/terminal_state_vocab.v1.json").read_bytes()).hexdigest(),
    }

    all_passed = (all(c["passed"] for c in criteria.values())
                  and support["pytest_clean"]
                  and support["figures"]["metrics_digest_matches"])
    verdict = {
        "node": "P93W3-N10",
        "mission_id": 9177,
        "repo": "TensorPlatform",
        "criteria": criteria,
        "support": support,
        "all_criteria_met": all_passed,
        "honest_notes": [
            "The semantic states are derived from stored facts; the storage enum and the "
            "cross-repo status contract are unchanged (a storage change would need a migration "
            "plus a coordinated consumer bump).",
            "Wall-clock arithmetic is deliberately NOT accepted as exhaustion evidence "
            "(measured precision on this corpus is in metrics.json -> budget_evidence).",
            "Four frozen-artifact tests of the previous node fail in this worktree because "
            "runs/p92w3-n10/* is not tracked here; they are unrelated to this change and are "
            "not counted as this node's tests.",
        ],
    }
    (RUN / "ACCEPTANCE.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "all_criteria_met": all_passed,
        "criteria": {k: v["passed"] for k, v in criteria.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()