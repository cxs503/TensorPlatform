"""Run the governance-suite maturation measurement and emit its ledgers.

    python3 -m tools.governance_maturity.run_maturity --out artifacts/governance_maturity

Every number quoted by the maturation report is produced here: contract-corpus
accuracy, mutation kill rates with 95% intervals, gate power, write-back gate
detection, idempotency, the domain-semantics scan, the pytest census, and the
figures.  Metric names, units and labels are synthetic; domain data is injected
through JSON documents (``data/governance_maturity``, ``data/criteria``).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from . import adapters, domain_scan, gate_power, mutation_scan, writeback_gate
from .artifact_registry import ArtifactRegistry, canonical_payload_digest, load_registry
from .report_writer import make_figures, write_report, write_summary

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "governance_maturity"
DEFAULT_OUT = REPO / "artifacts" / "governance_maturity"
REFERENCE_DATE = "2026-11-10"

SAMPLE_METRIC_ROWS = [
    {"metric": "repro_rate", "value": 0.95, "unit": "ratio", "window": "2026-W45", "evidence_count": 20},
    {"metric": "uplift", "value": 1.5, "unit": "ratio", "window": "2026-W45", "evidence_count": 12},
    {"metric": "coverage_ratio", "value": 0.83, "unit": "ratio", "window": "2026-W44", "evidence_count": 6},
    {"metric": "queue_depth", "value": 17, "unit": "count", "window": "2026-W45", "evidence_count": 1},
]

DOMAIN_SCAN_PATHS = [
    "tools/governance_maturity/__init__.py",
    "tools/governance_maturity/artifact_registry.py",
    "tools/governance_maturity/adapters.py",
    "tools/governance_maturity/mutation_scan.py",
    "tools/governance_maturity/gate_power.py",
    "tools/governance_maturity/writeback_gate.py",
    "tools/governance_maturity/domain_scan.py",
    "tools/governance_maturity/run_maturity.py",
    "tools/governance_maturity/report_writer.py",
    "tools/governance_maturity/tests",
    "data/governance_maturity",
]


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_contract_corpus(registry: ArtifactRegistry, out_dir: Path) -> dict[str, Any]:
    engine_profile = adapters.profile_from_registry_doc(DATA / "engine_profiles.json")["anchor_ledger"]
    snapshot = REPO / "artifacts" / "governance_maturity" / "targets" / "TensorFEM" / "validation_report.json"

    valid: list[tuple[str, dict[str, Any], str | None]] = []
    producers: Counter[str] = Counter()

    for row in SAMPLE_METRIC_ROWS:
        payload = adapters.adapt_metric_row(row)
        digest = "sha256:" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        valid.append((f"metric_row/{row['metric']}",
                      adapters.to_envelope(registry, "metric_row", payload,
                                           "TensorPlatform::tools/metric_gate", digest),
                      "metric_gate"))
        producers["TensorPlatform::tools/metric_gate"] += 1

    criteria = gate_power.load_registry_criteria(REPO / "data" / "criteria" / "threshold_registry.json")
    for criterion in criteria[:4]:
        payload = adapters.adapt_criterion(criterion)
        digest = "sha256:" + hashlib.sha256(json.dumps(criterion, sort_keys=True).encode()).hexdigest()
        valid.append((f"criterion/{payload['criterion_id'][:24]}",
                      adapters.to_envelope(registry, "criterion", payload,
                                           "TensorPlatform::tools/criteria_governance", digest),
                      "report_pipeline"))
        producers["TensorPlatform::tools/criteria_governance"] += 1

    # synthetic report payload: the repository's report.json carries no verdict
    # field, so the adapter's contract is exercised on a synthetic document
    report_doc = {
        "mission_id": 0,
        "title": "synthetic mission report",
        "verdict": "PASS",
        "sections": [{}, {}, {}],
        "criteria": ["c1", "c2"],
        "checks": {"passed": 2},
    }
    mission_payload = adapters.adapt_mission_report(report_doc)
    valid.append(("mission_report/synthetic",
                  adapters.to_envelope(registry, "mission_report", mission_payload,
                                       "TensorPlatform::tools/report_pipeline",
                                       "sha256:" + hashlib.sha256(
                                           json.dumps(report_doc, sort_keys=True).encode()).hexdigest()),
                  "report_pipeline"))
    producers["TensorPlatform::tools/report_pipeline"] += 1

    if snapshot.exists():
        engine_payload = adapters.adapt_engine_report(_read_json(snapshot), engine_profile)
        valid.append(("verification_result/validation_report.json",
                      adapters.to_envelope(registry, "verification_result", engine_payload,
                                           "TensorFEM::benchmarks", adapters.digest_of(snapshot)),
                      "report_pipeline"))
        producers["TensorFEM::benchmarks"] += 1

    def first_numeric(payload: Mapping[str, Any]) -> str | None:
        for name, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return name
        return None

    def drop_required(env: dict[str, Any]) -> str | None:
        for name in list(env["payload"]):
            if name != "probe":
                env["payload"].pop(name)
                env["checksum"] = canonical_payload_digest(env["payload"])
                return f"dropped:{name}"
        return None

    def wrong_type(env: dict[str, Any]) -> str | None:
        name = first_numeric(env["payload"])
        if name is None:
            return None
        env["payload"][name] = "not-a-number"
        env["checksum"] = canonical_payload_digest(env["payload"])
        return f"retyped:{name}"

    def out_of_range(env: dict[str, Any]) -> str | None:
        for name in ("evidence_count", "anchor_count", "failure_count", "section_count", "criteria_count"):
            if name in env["payload"]:
                env["payload"][name] = -5
                env["checksum"] = canonical_payload_digest(env["payload"])
                return f"negative:{name}"
        return None

    def enum_violation(env: dict[str, Any]) -> str | None:
        for name, bad in (("comparator", "≈"), ("verdict", "MAYBE"), ("unit", "furlongs"),
                          ("source_type", "vibes")):
            if name in env["payload"]:
                env["payload"][name] = bad
                env["checksum"] = canonical_payload_digest(env["payload"])
                return f"{name}:{bad}"
        return None

    def payload_tamper(env: dict[str, Any]) -> str | None:
        name = first_numeric(env["payload"])
        if name is None:
            return None
        env["payload"][name] = float(env["payload"][name]) * 1.01 + 1.0
        return f"tampered:{name}"  # checksum deliberately stale

    def unknown_version(env: dict[str, Any]) -> str:
        env["schema_version"] = "9.9.9"
        return "schema_version:9.9.9"

    def unregistered_producer(env: dict[str, Any]) -> str:
        env["provenance"]["producer"] = "RepoX::impostor"
        return "producer:RepoX::impostor"

    def malformed_digest(env: dict[str, Any]) -> str:
        env["provenance"]["source_digest"] = "not-a-digest"
        return "source_digest:malformed"

    def extra_field(env: dict[str, Any]) -> str:
        env["payload"]["surprise"] = 1
        env["checksum"] = canonical_payload_digest(env["payload"])
        return "extra:surprise"

    def array_item_type(env: dict[str, Any]) -> str | None:
        if "failing_anchors" in env["payload"]:
            env["payload"]["failing_anchors"] = [1, 2]
            env["checksum"] = canonical_payload_digest(env["payload"])
            return "failing_anchors:numbers"
        return None

    corruptions: tuple[tuple[str, Callable[[dict[str, Any]], str | None]], ...] = (
        ("drop_required", drop_required),
        ("wrong_type", wrong_type),
        ("out_of_range", out_of_range),
        ("enum_violation", enum_violation),
        ("payload_tamper", payload_tamper),
        ("unknown_version", unknown_version),
        ("unregistered_producer", unregistered_producer),
        ("malformed_digest", malformed_digest),
        ("extra_field", extra_field),
        ("array_item_type", array_item_type),
    )

    cases: list[dict[str, Any]] = []
    for label, env, consumer in valid:
        decision = registry.validate(env, consumer=consumer)
        cases.append({"label": label, "corruption": None, "expected": "accept",
                      "observed": "accept" if decision.ok else "reject",
                      "errors": [e.code for e in decision.errors], "consumer": consumer})
        for name, mutate in corruptions:
            bad = copy.deepcopy(env)
            detail = mutate(bad)
            if detail is None:
                continue
            decision = registry.validate(bad, consumer=consumer)
            cases.append({"label": label, "corruption": name, "detail": detail,
                          "expected": "reject",
                          "observed": "reject" if not decision.ok else "accept",
                          "errors": [e.code for e in decision.errors], "consumer": consumer})

    correct = sum(1 for c in cases if c["observed"] == c["expected"])
    confusion = Counter((c["expected"], c["observed"]) for c in cases)
    per_class = Counter(c["corruption"] for c in cases if c["corruption"])
    ledger = {
        "schema": "artifact-contract-ledger/1.0",
        "registry": "data/governance_maturity/artifact_schemas.json",
        "coverage": registry.coverage(),
        "producers_exercised": dict(sorted(producers.items())),
        "valid_artifacts": len(valid),
        "cases": len(cases),
        "cases_per_corruption_class": dict(sorted(per_class.items())),
        "correct": correct,
        "accuracy": correct / len(cases) if cases else None,
        "false_accepts": [c for c in cases if c["expected"] == "reject" and c["observed"] == "accept"],
        "false_rejects": [c for c in cases if c["expected"] == "accept" and c["observed"] == "reject"],
        "confusion": {f"{k[0]}->{k[1]}": v for k, v in sorted(confusion.items())},
        "corruption_classes": sorted(per_class),
        "cases_detail": cases,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "artifact_contract_ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger


def aggregate_mutation(out_dir: Path, targets_path: Path) -> dict[str, Any]:
    specs = {t.name: t for t in mutation_scan.load_targets(targets_path)}
    ledgers = [_read_json(p) for p in sorted((out_dir / "shards").glob("*.json"))]
    if not ledgers:
        raise SystemExit("no shard ledgers found -- run the mutation shards first")
    ledger = mutation_scan.aggregate(ledgers, expected_shards={name: 8 for name in specs})
    for name, entry in ledger["targets"].items():
        catalogue_path = out_dir / "mutants" / f"{name}.json"
        if catalogue_path.exists():
            catalogue = _read_json(catalogue_path)
            entry["catalogue"] = {
                "path": f"artifacts/governance_maturity/mutants/{name}.json",
                "module_sha256": catalogue["module_sha256"],
                "semantic_listed": sum(1 for m in catalogue["mutants"] if m["kind"] == "semantic"),
                "control_listed": sum(1 for m in catalogue["mutants"] if m["kind"] == "control"),
            }
    ledger["shard_files"] = [f"artifacts/governance_maturity/shards/{p.name}"
                             for p in sorted((out_dir / "shards").glob("*.json"))]
    (out_dir / "mutation_ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger


def measure_gate_power(out_dir: Path) -> dict[str, Any]:
    criteria = gate_power.load_registry_criteria(REPO / "data" / "criteria" / "threshold_registry.json")
    records = gate_power.build_records(criteria, REPO, reference_date=REFERENCE_DATE)
    ledger = gate_power.measure_gate_power(records, gate_power.CriterionGate())
    ledger["corpus"] = {"path": "data/criteria/threshold_registry.json", "criteria": len(records)}
    (out_dir / "gate_power_ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger


def _write(metric: str, value: float, inputs: Mapping[str, float], evidence: int,
           expression: str, unit: str, stamp: str, **overrides: Any) -> writeback_gate.MetricWrite:
    doc = {
        "metric": metric, "window": "synthetic-window", "value": value, "unit": unit,
        "expression": expression, "inputs": dict(inputs),
        "source_digest": "sha256:" + hashlib.sha256(f"{metric}{value}{evidence}".encode()).hexdigest(),
        "evidence_count": evidence, "written_at": stamp,
    }
    doc.update(overrides)
    return writeback_gate.MetricWrite.from_dict(doc)


def run_writeback_experiment(out_dir: Path) -> dict[str, Any]:
    config = writeback_gate.GateConfig.from_dict(_read_json(DATA / "writeback_registry.json")["config"])
    store = out_dir / "writeback_ledger.jsonl"
    if store.exists():
        store.unlink()
    gate = writeback_gate.MetricWritebackGate(config, store)
    now = "2026-11-10T12:00:00Z"
    stamps = [f"2026-11-10T{h:02d}:00:00Z" for h in range(1, 13)]
    outcomes: Counter[str] = Counter()


    def ratio_write(value: float, evidence: int, stamp: str, declared: float | None = None, **kw):
        return _write("ratio_metric", value,
                      {"numerator": value, "denominator": 1.0,
                       "declared_share": value if declared is None else declared},
                      evidence, "numerator / denominator", "ratio", stamp, **kw)

    def count_write(value: float, evidence: int, stamp: str):
        return _write("count_metric", value,
                      {"left": value / 2.0, "right": value / 2.0, "declared_total": value},
                      evidence, "left + right", "count", stamp)

    for k in range(12):
        value = 0.80 + 0.01 * k
        outcomes[gate.write(ratio_write(value, 10 + k, stamps[k]), now=now)["status"]] += 1
        value2 = 100.0 + k
        outcomes[gate.write(count_write(value2, 10 + k, stamps[k]), now=now)["status"]] += 1

    history_ratio = [r["value"] for r in gate.history("ratio_metric")]
    history_count = [r["value"] for r in gate.history("count_metric")]
    attempts: dict[str, dict[str, Any]] = {}

    def attempt(cls: str, write: writeback_gate.MetricWrite, expected: str) -> None:
        slot = attempts.setdefault(cls, {"attempted": 0, "detected": 0, "expected_code": expected, "codes": {}})
        slot["attempted"] += 1
        try:
            receipt = gate.write(write, now=now)
        except writeback_gate.WritebackRejected as exc:
            slot["codes"][exc.code] = slot["codes"].get(exc.code, 0) + 1
            if exc.code == expected:
                slot["detected"] += 1
        else:
            slot["codes"][receipt["status"]] = slot["codes"].get(receipt["status"], 0) + 1

    for k in range(6):
        target = history_ratio[k]
        attempt("rollback", ratio_write(target, 100 + k, stamps[k]), "rollback_to_historical_value")
        target_c = history_count[k]
        attempt("rollback", count_write(target_c, 100 + k, stamps[k]), "rollback_to_historical_value")

    for k in range(6):
        value = history_ratio[-1] + 0.02 + 0.001 * k
        attempt("regression", ratio_write(value, 21, stamps[k]), "regression_without_new_evidence")

    for k in range(5):
        stamp = f"2026-11-0{1 + k}T00:00:00Z"
        value = history_ratio[-1] + 0.001 * (k + 1)
        attempt("stale", ratio_write(value, 40 + k, stamp), "stale_write")

    for k in range(5):
        attempt("recompute_mismatch",
                _write("ratio_metric", history_ratio[-1] + 0.0001 * (k + 1),
                       {"numerator": 1.0, "denominator": 3.0}, 50 + k,
                       "numerator / denominator", "ratio", stamps[k]), "recompute_mismatch")

    for k in range(5):
        attempt("identity_violation",
                ratio_write(0.5, 60 + k, stamps[k], declared=0.4), "identity_violation")

    for k in range(5):
        value = history_ratio[-1] + 0.0001 * (k + 1)
        attempt("forged_provenance", ratio_write(value, 70 + k, stamps[k], source_digest="deadbeef"),
                "provenance_malformed")

    ledger_before = len(gate.records)
    duplicates = 0
    duplicate_ok = True
    first_receipt = None
    for k in range(6):
        value = history_ratio[k]
        receipt = gate.write(ratio_write(value, 10 + k, stamps[k]), now=now)
        if first_receipt is None:
            first_receipt = receipt
        duplicates += 1
        duplicate_ok &= receipt["status"] == "duplicate"
    ledger_after = len(gate.records)
    reloaded = writeback_gate.MetricWritebackGate(config, store)
    chain = reloaded.verify_chain()
    replay = reloaded.write(ratio_write(history_ratio[0], 10, stamps[0]), now=now)

    legit_after = 0
    for k in range(5):
        value = history_ratio[-1] + 0.01 * (k + 1)
        receipt = gate.write(ratio_write(value, 200 + k, stamps[k]), now=now)
        legit_after += 1 if receipt["status"] == "accepted" else 0

    detection = {}
    for cls, slot in attempts.items():
        detection[cls] = {
            "attempted": slot["attempted"],
            "detected": slot["detected"],
            "rate": slot["detected"] / slot["attempted"] if slot["attempted"] else None,
            "expected_code": slot["expected_code"],
            "codes": slot["codes"],
        }

    ledger = {
        "schema": "writeback-ledger/1.0",
        "freshness_slo_s": config.freshness_slo_s,
        "clean_history_writes": dict(outcomes),
        "detection": detection,
        "rollback_detection_rate": detection.get("rollback", {}).get("rate"),
        "regression_detection_rate": detection.get("regression", {}).get("rate"),
        "idempotency": {
            "duplicates_attempted": duplicates,
            "duplicates_returned_duplicate": duplicates if duplicate_ok else None,
            "ledger_before": ledger_before,
            "ledger_after": ledger_after,
            "ledger_unchanged": ledger_before == ledger_after,
            "reload_chain_ok": chain["ok"],
            "reloaded_length": chain.get("length"),
            "replay_status": replay["status"],
            "replay_chain_matches_first": replay["chain"] == first_receipt["chain"] if first_receipt else None,
        },
        "legitimate_after_campaign": {"attempted": 5, "accepted": legit_after},
        "false_positive_rate": (5 - legit_after) / 5,
        "final_chain": gate.verify_chain(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "writeback_ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger


def run_domain_scan(out_dir: Path) -> dict[str, Any]:
    ledger = domain_scan.scan_paths(DOMAIN_SCAN_PATHS, REPO)
    (out_dir / "domain_scan.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger


def pytest_census() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tools/governance_maturity/tests", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if "::" in ln]
    return {"collected": len(lines), "returncode": proc.returncode,
            "tail": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--report", default=str(REPO / "docs" / "P315W1-N10_governance_suite_maturity.md"))
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "shards").mkdir(exist_ok=True)
    (out / "mutants").mkdir(exist_ok=True)

    registry = load_registry(DATA / "artifact_schemas.json")
    contract = build_contract_corpus(registry, out)
    mutation = aggregate_mutation(out, DATA / "mutation_targets.json")
    gate = measure_gate_power(out)
    writeback = run_writeback_experiment(out)
    scan = run_domain_scan(out)
    census = pytest_census()
    figures = make_figures(contract, mutation, gate, writeback, out)
    summary = write_summary(out, contract, mutation, gate, writeback, scan, census)
    v0_path = out / "mutation_ledger_v0.json"
    mutation_v0 = _read_json(v0_path) if v0_path.exists() else None
    write_report(summary, contract, mutation, gate, writeback, scan, figures, census,
                 Path(args.report), mutation_v0=mutation_v0)
    print(json.dumps({"summary": str(out / "summary.json"),
                      "report": args.report,
                      "checks": {k: v["pass"] for k, v in summary["checks"].items()},
                      "figures": [f["name"] for f in figures]}, indent=2))
    return 0 if summary["all_checks_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())