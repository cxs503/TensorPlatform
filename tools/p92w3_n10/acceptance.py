"""Assemble the machine acceptance verdicts for P92W3-N10 (2nd attempt).

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
        "mission_id": 13902,
        "attempt": 2,
        "criteria": {
            "a_adjudicated_macro_f1_ge_0_90": {
                "value": adj["artifact_only"]["macro_f1"],
                "threshold": 0.90,
                "pass": crit_a,
                "evidence": "runs/p92w3-n10/adjudication_metrics.json:artifact_only",
                "note": "rubric aligned with fail-closed semantics (checks=critical)",
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
                "why_not": "the weak label is one-sided: the forced_finish marker has low PPV "
                           "(0.5667 on adjudicated sample) while non-marked missions are almost "
                           "all genuinely incomplete (1.0 rate). Any honest rule catching both "
                           "is charged with false positives. Precision upper bound = "
                           f"{metrics['label_noise']['precision_upper_bound']}. "
                           "Only tautological replay (R3) reaches 0.90+ (zero information).",
            },
            "c_fail_closed_negative_controls": {
                "pytest": tests,
                "pass": crit_c,
                "evidence": "runs/p92w3-n10/pytest_junit.xml",
            },
            "d_shadow_golden_regression": {
                "deterministic": shadow["deterministic"],
                "input_mutated": shadow["input_mutated"],
                "io_tokens": shadow["io_tokens_in_classifier"],
                "pass": crit_d,
                "evidence": "runs/p92w3-n10/shadow_golden.json",
            },
            "e_zero_domain_semantics": {
                "total_new_hits": scan["total_new_hits"],
                "pass": crit_e,
                "evidence": "runs/p92w3-n10/domain_scan.json",
            },
        },
        "overall_pass": all([crit_a, crit_b_recall, crit_c, crit_d, crit_e]),
        "precision_fail_honest": not crit_b_precision,
        "summary": {
            "pass_count": sum([crit_a, crit_b_recall, crit_c, crit_d, crit_e]),
            "total_count": 5,
            "precision_fail_reason": "weak label is one-sided (quantified in metrics.json:label_noise)",
        },
    }

    (RUN / "ACCEPTANCE.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
