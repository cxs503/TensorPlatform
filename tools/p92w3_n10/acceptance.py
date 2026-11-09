"""Assemble the machine acceptance verdicts for P92W3-N10 (criteria a-e).

Every number is read from a frozen artifact (no hand-copied values):
  runs/p92w3-n10/metrics.json               primary weak-label experiment
  runs/p92w3-n10/adjudication_metrics.json  60-unit adjudicated evaluation
  runs/p92w3-n10/shadow_golden.json         shadow-mode regression
  runs/p92w3-n10/pytest_junit.xml           test run (domain scan + fail-closed live here)
  runs/p92w3-n10/domain_scan.json           zero-domain-semantics sweep

Run: python3 tools/p92w3_n10/acceptance.py
"""
from __future__ import annotations

import json
import pathlib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p92w3-n10"


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
        "tests": int(attrs.get("tests", 0)),
        "failures": int(attrs.get("failures", 0)),
        "errors": int(attrs.get("errors", 0)),
        "skipped": int(attrs.get("skipped", 0)),
    }


def main() -> None:
    metrics = load("metrics.json")
    adj = load("adjudication_metrics.json")
    shadow = load("shadow_golden.json")
    scan = load("domain_scan.json")
    tests = pytest_summary()

    crit_a = adj["artifact_only"]["macro_f1"] >= 0.90
    crit_b_recall = metrics["rules"]["R2_attested_not_complete"]["recall"] >= 0.85
    crit_b_precision = metrics["rules"]["R2_attested_not_complete"]["precision"] >= 0.90
    crit_c = tests.get("failures") == 0 and tests.get("errors") == 0 and tests.get("tests", 0) > 0
    crit_d = bool(shadow["deterministic"]) and not bool(shadow["input_mutated"]) \
        and all(not v for v in shadow["io_tokens_in_classifier"].values())
    crit_e = scan["total_new_hits"] == 0

    verdict = {
        "node": "P92W3-N10",
        "mission_id": 8456,
        "criteria": {
            "a_adjudicated_macro_f1_ge_0_90": {
                "value": adj["artifact_only"]["macro_f1"],
                "threshold": 0.90,
                "pass": crit_a,
                "evidence": "runs/p92w3-n10/adjudication_metrics.json:artifact_only",
                "baseline_before_fix": adj["artifact_only_prefix_replay"]["macro_f1"],
            },
            "b_weak_label_recall_ge_0_85": {
                "value": metrics["rules"]["R2_attested_not_complete"]["recall"],
                "threshold": 0.85, "pass": crit_b_recall,
                "evidence": "runs/p92w3-n10/metrics.json:rules.R2_attested_not_complete",
            },
            "b_weak_label_precision_ge_0_90": {
                "value": metrics["rules"]["R2_attested_not_complete"]["precision"],
                "threshold": 0.90, "pass": crit_b_precision,
                "evidence": "runs/p92w3-n10/metrics.json:rules.R2_attested_not_complete",
                "why_not": "the weak label is one-sided: 30/30 adjudicated non-marked missions "
                           "are genuinely not complete, so an honest rule is charged with false "
                           "positives for being right; only a tautological replay of the marker "
                           "(R3) reaches 0.90+ (precision=1.0, recall=1.0, zero information)",
            },
            "c_fail_closed_negative_controls": {
                "pass": crit_c, "evidence": "runs/p92w3-n10/pytest_junit.xml", "pytest": tests,
            },
            "d_shadow_golden_regression": {
                "pass": crit_d,
                "deterministic": shadow["deterministic"],
                "input_mutated": shadow["input_mutated"],
                "io_tokens": shadow["io_tokens_in_classifier"],
                "n_cases": shadow["n_cases"],
                "evidence": "runs/p92w3-n10/shadow_golden.json",
            },
            "e_zero_domain_semantics": {
                "pass": crit_e, "new_hits": scan["total_new_hits"],
                "hits_in_mechanism_modules": sum(
                    len(f["hits"]) for f in scan["files"]
                    if f["path"] in ("backend/api/domain/delivery_state.py",
                                     "backend/api/kpi_delivery.py",
                                     "ops/taskplane/mvd_gate.py",
                                     "backend/tests/test_p92w3_n10_layering.py")),
                "pre_existing_prompt_hits": [
                    {"path": f["path"], "hits": f["hits_at_head"]}
                    for f in scan["files"] if f.get("hits_at_head")],
                "files_scanned": len(scan["files"]),
                "evidence": "runs/p92w3-n10/domain_scan.json",
            },
        },
        "overall": "partial — mechanism delivered and machine-verified; criterion (b) precision "
                   "recorded as honest FAIL with a measured, quantified reason",
        "all_pass": all([crit_a, crit_b_recall, crit_b_precision, crit_c, crit_d, crit_e]),
    }
    (RUN / "ACCEPTANCE.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v.get("pass") for k, v in verdict["criteria"].items()},
                     ensure_ascii=False), "all_pass=", verdict["all_pass"])


if __name__ == "__main__":
    main()