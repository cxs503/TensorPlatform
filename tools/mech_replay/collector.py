"""Golden fixture collector — generates synthetic, domain-neutral input snapshots.

Each fixture is a JSON file with:
  - mechanism: str (mechanism name)
  - fixture_id: str (unique id)
  - input: dict (mechanism-specific input)
  - expected_output: dict (golden output from current mechanism)
  - expected_sha256: str (sha256 of expected_output)
  - source: "synthetic" (no real domain data)

Fixtures are generated deterministically from seeds.  No DB, no network.
Domain words are replaced with synthetic tokens (C5 desensitization).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, List

from .mechanisms import ALL_MECHANISMS, get_mechanism


# ── Synthetic input generators ──────────────────────────────────────────

def _gen_maturity_gate_inputs(rng: random.Random, n: int) -> List[Dict[str, Any]]:
    """Generate n diverse maturity gate inputs covering PASS/FAIL/WITHHELD."""
    fixtures = []
    for i in range(n):
        n_nodes = rng.randint(3, 6)  # At least 3 nodes for M3 reversal
        summaries = []
        for j in range(n_nodes):
            scenario = rng.choice(["all_pass", "some_fail", "some_absent", "mixed"])
            if scenario == "all_pass":
                summaries.append({
                    "node_key": f"node_{j}",
                    "evaluable_ratio": 1.0,
                    "pass_ratio": rng.uniform(0.8, 1.0),
                    "claim_mismatches": [],
                })
            elif scenario == "some_fail":
                summaries.append({
                    "node_key": f"node_{j}",
                    "evaluable_ratio": 1.0,
                    "pass_ratio": rng.uniform(0.3, 0.79),
                    "claim_mismatches": [],
                })
            elif scenario == "some_absent":
                summaries.append({
                    "node_key": f"node_{j}",
                    "evaluable_ratio": 0.0,
                    "pass_ratio": 0.0,
                    "claim_mismatches": [],
                })
            else:  # mixed
                summaries.append({
                    "node_key": f"node_{j}",
                    "evaluable_ratio": rng.choice([0.0, 1.0]),
                    "pass_ratio": rng.uniform(0.5, 1.0),
                    "claim_mismatches": [{"criterion_id": f"c{k}"} for k in range(rng.randint(0, 2))],
                })
        # Ensure first and last nodes are different (non-palindromic)
        if len(summaries) >= 2:
            summaries[0]["pass_ratio"] = 0.95
            summaries[-1]["pass_ratio"] = 0.45
        threshold = rng.choice([0.7, 0.8, 0.9])
        mismatch = rng.randint(0, 3)
        fixtures.append({
            "summaries": summaries,
            "threshold": threshold,
            "claim_mismatch_total": mismatch,
        })
    return fixtures


def _gen_evidence_comparator_inputs(rng: random.Random, n: int) -> List[Dict[str, Any]]:
    """Generate n diverse comparator inputs covering all comparators."""
    fixtures = []
    comparators = ["le", "lt", "ge", "gt", "eq", "between", "nonempty"]
    for i in range(n):
        n_checks = rng.randint(4, 8)  # At least 4 for M3 reversal
        checks = []
        for _ in range(n_checks):
            comp = rng.choice(comparators)
            if comp == "between":
                lo = rng.uniform(0, 50)
                hi = lo + rng.uniform(1, 50)
                val = rng.uniform(lo - 5, hi + 5)
                checks.append({"value": val, "comparator": comp,
                               "threshold": [lo, hi]})
            elif comp == "nonempty":
                val = rng.choice(["", "x", [], [1], {}, {"a": 1}, None])
                checks.append({"value": val, "comparator": comp})
            elif comp == "eq":
                thr = rng.uniform(0, 100)
                val = thr + rng.uniform(-1, 1)
                tol = rng.uniform(0, 0.1)
                checks.append({"value": val, "comparator": comp,
                               "threshold": thr, "tolerance": tol})
            else:
                thr = rng.uniform(0, 100)
                val = thr + rng.uniform(-10, 10)
                checks.append({"value": val, "comparator": comp,
                               "threshold": thr})
        # Ensure first and last checks are different (non-palindromic)
        if len(checks) >= 2:
            checks[0] = {"value": 10.0, "comparator": "lt", "threshold": 50.0}
            checks[-1] = {"value": 90.0, "comparator": "gt", "threshold": 50.0}
        fixtures.append({"checks": checks})
    return fixtures


def _gen_evidence_grader_inputs(rng: random.Random, n: int) -> List[Dict[str, Any]]:
    """Generate n diverse evidence grading inputs covering all grade levels."""
    fixtures = []
    grade_targets = ["REAL", "MOCK", "STUB", "WITHHELD", "UNKNOWN"] * (n // 5 + 1)
    for i in range(n):
        target = grade_targets[i % len(grade_targets)]
        if target == "UNKNOWN":
            inp = {"has_schema": False, "has_numeric": True, "has_repro_cmd": True,
                   "has_anchor": True, "anchor_recomputable": True,
                   "has_residual": True, "has_independent_path": True,
                   "path_recomputable": True, "residual_recomputable": True,
                   "tolerance_declared": True, "tolerance_met": True,
                   "paths_independent": True, "neg_control_fails": True,
                   "provenance_matches": True}
        elif target == "STUB":
            inp = {"has_schema": True, "has_numeric": False, "has_repro_cmd": True,
                   "has_anchor": True, "anchor_recomputable": True,
                   "has_residual": True, "has_independent_path": True,
                   "path_recomputable": True, "residual_recomputable": True,
                   "tolerance_declared": True, "tolerance_met": True,
                   "paths_independent": True, "neg_control_fails": True,
                   "provenance_matches": True}
        elif target == "MOCK":
            inp = {"has_schema": True, "has_numeric": True,
                   "has_repro_cmd": rng.choice([True, False]),
                   "has_anchor": rng.choice([True, False]),
                   "anchor_recomputable": rng.choice([True, False]),
                   "has_residual": True, "has_independent_path": True,
                   "path_recomputable": True, "residual_recomputable": True,
                   "tolerance_declared": True, "tolerance_met": True,
                   "paths_independent": True, "neg_control_fails": True,
                   "provenance_matches": True}
        elif target == "WITHHELD":
            inp = {"has_schema": True, "has_numeric": True, "has_repro_cmd": True,
                   "has_anchor": True, "anchor_recomputable": True,
                   "has_residual": rng.choice([True, False]),
                   "has_independent_path": rng.choice([True, False]),
                   "path_recomputable": rng.choice([True, False]),
                   "residual_recomputable": rng.choice([True, False]),
                   "tolerance_declared": rng.choice([True, False]),
                   "tolerance_met": rng.choice([True, False]),
                   "paths_independent": rng.choice([True, False]),
                   "neg_control_fails": rng.choice([True, False]),
                   "provenance_matches": rng.choice([True, False])}
        else:  # REAL
            inp = {"has_schema": True, "has_numeric": True, "has_repro_cmd": True,
                   "has_anchor": True, "anchor_recomputable": True,
                   "has_residual": True, "has_independent_path": True,
                   "path_recomputable": True, "residual_recomputable": True,
                   "tolerance_declared": True, "tolerance_met": True,
                   "paths_independent": True, "neg_control_fails": True,
                   "provenance_matches": True}
        fixtures.append(inp)
    return fixtures


def _gen_drift_classifier_inputs(rng: random.Random, n: int) -> List[Dict[str, Any]]:
    """Generate n diverse drift classifier inputs covering all drift classes."""
    fixtures = []
    for i in range(n):
        n_pairs = rng.randint(3, 6)  # At least 3 for M3 reversal
        pairs = []
        for _ in range(n_pairs):
            scenario = rng.choice(["bit_exact", "ulp_level", "threshold_level",
                                   "structural", "timeout_blocked"])
            if scenario == "bit_exact":
                val = {"x": rng.uniform(0, 100), "y": rng.randint(0, 1000)}
                pairs.append({"baseline": val, "candidate": val,
                              "declared_tol": 0.01})
            elif scenario == "ulp_level":
                base = rng.uniform(1, 100)
                cand = base + base * rng.uniform(1e-16, 5e-16)
                pairs.append({"baseline": base, "candidate": cand,
                              "declared_tol": 0.01})
            elif scenario == "threshold_level":
                base = rng.uniform(1, 100)
                cand = base * (1 + rng.uniform(0.001, 0.009))
                pairs.append({"baseline": base, "candidate": cand,
                              "declared_tol": 0.01})
            elif scenario == "structural":
                bl = {"a": 1, "b": [1, 2, 3]}
                cand = {"a": 1, "c": [1, 2, 3]}
                pairs.append({"baseline": bl, "candidate": cand,
                              "declared_tol": 0.01})
            else:  # timeout_blocked
                pairs.append({"baseline": {"status": "timeout"},
                              "candidate": {"value": 42.0},
                              "declared_tol": 0.01})
        # Ensure first and last pairs are different (non-palindromic)
        if len(pairs) >= 2:
            pairs[0] = {"baseline": 10.0, "candidate": 10.0, "declared_tol": 0.01}
            pairs[-1] = {"baseline": {"a": 1}, "candidate": {"b": 2}, "declared_tol": 0.01}
        fixtures.append({"pairs": pairs})
    return fixtures


def _gen_perf_gate_inputs(rng: random.Random, n: int) -> List[Dict[str, Any]]:
    """Generate n diverse perf gate inputs covering PASS/FAIL scenarios.

    Uses domain-neutral field names (metric_a, metric_b, etc.).
    """
    fixtures = []
    for i in range(n):
        n_cases = rng.randint(3, 5)  # At least 3 for M3 reversal
        cases = []
        for _ in range(n_cases):
            scenario = rng.choice(["pass_clean", "fail_geo", "fail_error",
                                   "fail_mass", "fail_missing"])
            if scenario == "pass_clean":
                cases.append({
                    "geometry": {"metric_a": rng.uniform(0.4, 0.8),
                                 "metric_b": rng.uniform(50, 300),
                                 "metric_c": rng.uniform(10, 50)},
                    "results": {"error_pct_a": rng.uniform(1, 10),
                                "conservation_err": rng.uniform(0.01, 0.5)},
                    "expected_verdict": "PASS",
                })
            elif scenario == "fail_geo":
                cases.append({
                    "geometry": {"metric_a": rng.choice([-0.1, 1.5, 0]),
                                 "metric_b": rng.uniform(50, 300),
                                 "metric_c": rng.uniform(10, 50)},
                    "results": {"error_pct_a": rng.uniform(1, 10),
                                "conservation_err": rng.uniform(0.01, 0.5)},
                    "expected_verdict": "FAIL",
                })
            elif scenario == "fail_error":
                cases.append({
                    "geometry": {"metric_a": rng.uniform(0.4, 0.8),
                                 "metric_b": rng.uniform(50, 300),
                                 "metric_c": rng.uniform(10, 50)},
                    "results": {"error_pct_a": rng.uniform(16, 40),
                                "conservation_err": rng.uniform(0.01, 0.5)},
                    "expected_verdict": "FAIL",
                })
            elif scenario == "fail_mass":
                cases.append({
                    "geometry": {"metric_a": rng.uniform(0.4, 0.8),
                                 "metric_b": rng.uniform(50, 300),
                                 "metric_c": rng.uniform(10, 50)},
                    "results": {"error_pct_a": rng.uniform(1, 10),
                                "conservation_err": rng.uniform(1.5, 10)},
                    "expected_verdict": "FAIL",
                })
            else:  # fail_missing
                cases.append({
                    "geometry": None,
                    "results": None,
                    "expected_verdict": "FAIL",
                })
        # Ensure first and last cases are different (non-palindromic)
        if len(cases) >= 2:
            cases[0] = {
                "geometry": {"metric_a": 0.5, "metric_b": 100, "metric_c": 20},
                "results": {"error_pct_a": 5.0, "conservation_err": 0.2},
                "expected_verdict": "PASS",
            }
            cases[-1] = {
                "geometry": None,
                "results": None,
                "expected_verdict": "FAIL",
            }
        fixtures.append({"cases": cases})
    return fixtures


# ── Collector ────────────────────────────────────────────────────────────

GENERATORS = {
    "maturity_gate_verdict": _gen_maturity_gate_inputs,
    "evidence_comparator": _gen_evidence_comparator_inputs,
    "evidence_grader": _gen_evidence_grader_inputs,
    "drift_classifier": _gen_drift_classifier_inputs,
    "perf_gate_case": _gen_perf_gate_inputs,
}


def collect_fixtures(mechanism_name: str, n: int = 10,
                     seed: int = 20261110) -> List[Dict[str, Any]]:
    """Generate n golden fixtures for a mechanism with expected outputs."""
    rng = random.Random(seed)
    mech = get_mechanism(mechanism_name)
    gen = GENERATORS[mechanism_name]
    inputs = gen(rng, n)
    fixtures = []
    for i, inp in enumerate(inputs):
        output = mech["invoke"](inp)
        sha = mech["output_sha256"](output)
        fixtures.append({
            "mechanism": mechanism_name,
            "fixture_id": f"{mechanism_name}_{i:04d}",
            "input": inp,
            "expected_output": output,
            "expected_sha256": sha,
            "source": "synthetic",
            "seed": seed,
        })
    return fixtures


def collect_all(n_per_mechanism: int = 10, seed: int = 20261110) -> Dict[str, List[Dict[str, Any]]]:
    """Collect fixtures for all mechanisms."""
    result = {}
    for name in sorted(GENERATORS.keys()):
        result[name] = collect_fixtures(name, n=n_per_mechanism, seed=seed)
    return result


def save_fixtures(fixtures: List[Dict[str, Any]], out_dir: Path,
                  mechanism_name: str) -> Dict[str, str]:
    """Save fixtures to JSON files + sha256 manifest. Returns {filename: sha256}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for fix in fixtures:
        fname = f"{fix['fixture_id']}.json"
        fpath = out_dir / fname
        content = json.dumps(fix, sort_keys=True, indent=2, ensure_ascii=False)
        fpath.write_text(content, encoding="utf-8")
        file_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        manifest[fname] = file_sha
    manifest_path = out_dir / f"{mechanism_name}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    return manifest


def check_desensitization(fixtures: List[Dict[str, Any]],
                          forbidden_words: List[str]) -> List[str]:
    """C5: Check that no forbidden domain words appear in fixtures.
    Returns list of violations (empty = clean)."""
    violations = []
    blob = json.dumps(fixtures, ensure_ascii=False)
    for word in forbidden_words:
        if word.lower() in blob.lower():
            violations.append(word)
    return violations
