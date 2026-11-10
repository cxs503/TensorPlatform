"""Acceptance run of the capability-governance triple (P187-W1-N09).

One command produces every number the node's criteria ask for, from artifacts on
disk — nothing is typed by hand into the report:

C1  passing endpoint test -> evidence chain -> ledger entry created as REAL
C2  failing endpoint test -> entry NOT REAL (WITHHELD), tampered chain rejected
C3  frozen-set backtest accuracy >= 0.90 and false-positive rate < 0.05
C4  cross-solver deviation attribution chain, all four dimensions recovered
C5  structured sample rows >= 10 000, >= 2 000 per family, >= 1 000 per class
C6  provenance completeness >= 0.95, dedupe unique rate >= 0.99
C7  zero domain vocabulary in the added code and tests (scanner asserts 0 hits)

The run is *gated* on the package's own pytest suite: the junit report is
produced first, and its sha256 is the ``test_report`` field of every evidence
chain.  If the suite fails, no endpoint can be graded REAL — the loop cannot
certify itself with red tests.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import attribution, backtest as backtest_mod, fixtures, samples
from .grading import build_chain, digest_of, grade_probe, sha256_file, validate_chain
from .ledger import CapabilityLedger
from .probes import run_endpoint

SCHEMA = "capability-governance-acceptance/1.0"
MISSION_ID = 9811
NODE = "P187-W1-N09"
REPO = Path(__file__).resolve().parents[3]
PKG = Path(__file__).resolve().parent
CONFIG = REPO / "config" / "capability_governance"
TESTS = REPO / "tests" / "capability_governance"

#: files the C7 scanner must find zero domain vocabulary in
SCAN_TARGETS = [
    "backend/services/capability_governance",
    "tests/capability_governance",
    "config/capability_governance/probe_set.json",
    "config/capability_governance/sample_spec.json",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# test-report gate
# ---------------------------------------------------------------------------


def run_test_gate(out_dir: Path, *, timeout: int = 1800, python: Optional[str] = None) -> Dict[str, Any]:
    """Run the package's pytest suite and hash its junit report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = out_dir / "pytest_junit.xml"
    cmd = [python or sys.executable, "-m", "pytest", str(TESTS), "-q",
           f"--junitxml={junit}"]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
    passed = proc.returncode == 0 and junit.exists()
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "passed": passed,
        "report_path": str(junit),
        "report_sha256": sha256_file(junit) if junit.exists() else None,
        "stdout_tail": proc.stdout.strip().splitlines()[-5:],
        "stderr_tail": proc.stderr.strip().splitlines()[-5:],
    }


def _fallback_report(out_dir: Path, reason: str) -> Path:
    """Synthetic red-ish report when pytest cannot run: keeps chains honest."""
    path = out_dir / "test_report_fallback.json"
    path.write_text(json.dumps({"passed": False, "reason": reason, "generated_at": _now()},
                               indent=1), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# C1 / C2 — auto ledger upgrade + negative controls
# ---------------------------------------------------------------------------


def run_auto_ledger(specs: Sequence[Mapping[str, Any]], *, test_report: Path,
                    out_dir: Path) -> Dict[str, Any]:
    ledger = CapabilityLedger(out_dir / "ledger.json", out_dir / "ledger_events.jsonl")
    result = backtest_mod.run_frozen_set(specs, test_report_path=test_report,
                                        mission_id=MISSION_ID, ledger=ledger)
    (out_dir / "backtest.json").write_text(json.dumps(
        {k: v for k, v in result.items() if k != "chains"}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    chains_dir = out_dir / "evidence_chains"
    chains_dir.mkdir(parents=True, exist_ok=True)
    for chain in result["chains"]:
        (chains_dir / f"{chain['subject_id']}.json").write_text(
            json.dumps(chain, indent=1, ensure_ascii=False), encoding="utf-8")

    real_rows = [r for r in result["rows"] if r["ledger_level"] == "REAL"]
    non_real_rows = [r for r in result["rows"] if r["ledger_level"] != "REAL"]
    chain_valid = sum(1 for c in result["chains"] if validate_chain(c)["valid"])
    return {
        "backtest": result,
        "ledger": ledger,
        "summary": ledger.summary(),
        "n_real": len(real_rows),
        "n_non_real": len(non_real_rows),
        "chains_total": len(result["chains"]),
        "chains_valid": chain_valid,
        "ledger_path": str(out_dir / "ledger.json"),
        "events_path": str(out_dir / "ledger_events.jsonl"),
        "evidence_chain_dir": str(chains_dir),
        "evidence_chain_manifest_sha256": digest_of(
            [{"subject": c["subject_id"], "digest": c["chain_digest"]} for c in result["chains"]]),
    }


def run_negative_controls(specs: Sequence[Mapping[str, Any]], *, test_report: Path,
                          out_dir: Path) -> Dict[str, Any]:
    """C2: a failing endpoint must never reach REAL, and a tampered chain must die."""
    nc_dir = out_dir / "negative_controls"
    nc_dir.mkdir(parents=True, exist_ok=True)
    ledger = CapabilityLedger(nc_dir / "ledger_negative.json",
                              nc_dir / "ledger_negative_events.jsonl")
    routine_sha = sha256_file(PKG / "probes.py")
    controls: List[Dict[str, Any]] = []

    # (a) failing endpoints: every labelled negative of the frozen set
    negatives = [s for s in specs if s.get("expected_level") != "REAL"]
    levels: List[str] = []
    for spec in negatives:
        verdict = grade_probe(run_endpoint(spec).to_dict(), spec, routine_sha, digest_of(dict(spec)))
        chain = build_chain(verdict, test_report_path=test_report, mission_id=MISSION_ID,
                            routine_source_sha256=routine_sha, spec_digest=digest_of(dict(spec)))
        decision = ledger.apply(chain, MISSION_ID)
        levels.append(decision["level"])
    controls.append({
        "control": "failing_endpoints_never_real",
        "n_cases": len(negatives),
        "levels": sorted(set(levels)),
        "real_count": sum(1 for lv in levels if lv == "REAL"),
        "passed": all(lv != "REAL" for lv in levels) and bool(negatives),
    })

    # (b) tampered chain: flip a measured value after the digest was computed
    spec = specs[0]
    verdict = grade_probe(run_endpoint(spec).to_dict(), spec, routine_sha, digest_of(dict(spec)))
    chain = build_chain(verdict, test_report_path=test_report, mission_id=MISSION_ID,
                        routine_source_sha256=routine_sha, spec_digest=digest_of(dict(spec)))
    tampered = json.loads(json.dumps(chain))
    tampered["measured_values"]["measured"][-1] *= 1.000001
    tamper_decision = ledger.apply(tampered, MISSION_ID)
    controls.append({
        "control": "tampered_chain_rejected",
        "action": tamper_decision["action"],
        "problems": tamper_decision.get("problems", []),
        "passed": tamper_decision["action"] == "rejected" and tamper_decision["level"] != "REAL",
    })

    # (c) forged pass: claim PASS while a criterion failed
    forged = json.loads(json.dumps(chain))
    forged["verdict"] = "PASS"
    forged["acceptance_criteria"][1]["passed"] = False
    forged["chain_digest"] = digest_of({k: v for k, v in forged.items() if k != "chain_digest"})
    forged_decision = ledger.apply(forged, MISSION_ID)
    controls.append({
        "control": "forged_pass_rejected",
        "action": forged_decision["action"],
        "problems": forged_decision.get("problems", []),
        "passed": forged_decision["action"] == "rejected",
    })

    # (d) red test report: same verdict table, but the gate report is missing/mismatched
    no_report_chain = json.loads(json.dumps(chain))
    no_report_chain["test_report"]["sha256"] = "0" * 64
    no_report_chain["chain_digest"] = digest_of({k: v for k, v in no_report_chain.items()
                                                 if k != "chain_digest"})
    no_report_decision = ledger.apply(no_report_chain, MISSION_ID)
    controls.append({
        "control": "chain_without_matching_report_rejected",
        "action": no_report_decision["action"],
        "problems": no_report_decision.get("problems", []),
        "passed": no_report_decision["action"] == "rejected",
    })

    document = {
        "schema": "capability-governance-negative-controls/1.0",
        "controls": controls,
        "all_passed": all(c["passed"] for c in controls),
    }
    (nc_dir / "negative_controls.json").write_text(
        json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    return document


# ---------------------------------------------------------------------------
# C4 — attribution chain
# ---------------------------------------------------------------------------


def run_attribution(out_dir: Path) -> Dict[str, Any]:
    cases = fixtures.attribution_cases()
    result = attribution.attribute_cases(cases)
    expected = {c["case_id"]: c for c in cases}
    checks: List[Dict[str, Any]] = []
    for case in result["cases"]:
        arm = case["arms"][0]
        exp = expected[case["case_id"]]
        dominant_expected = exp.get("expect_dominant")
        attributed_expected = exp.get("expect_attributed", True)
        dominant_ok = (arm["dominant_dimension"] == dominant_expected) if dominant_expected else True
        attributed_ok = (arm["attributed"] == attributed_expected)
        telescoping = abs(sum(arm["contributions"].values()) + arm["residual_share"] - 1.0) < 1e-9
        checks.append({
            "case_id": case["case_id"],
            "expected_dominant": dominant_expected,
            "observed_dominant": arm["dominant_dimension"],
            "expected_attributed": attributed_expected,
            "observed_attributed": arm["attributed"],
            "residual_share": arm["residual_share"],
            "contributions": arm["contributions"],
            "telescoping_identity": telescoping,
            "passed": bool(dominant_ok and attributed_ok and telescoping),
        })
    document = {
        "schema": "capability-governance-attribution-acceptance/1.0",
        "dimensions": result["dimension_coverage"],
        "n_cases": result["n_cases"],
        "attribution_completeness": result["attribution_completeness"],
        "mean_dimension_share": result["mean_dimension_share"],
        "checks": checks,
        "n_passed": sum(1 for c in checks if c["passed"]),
        "all_passed": all(c["passed"] for c in checks),
        "cases": result["cases"],
    }
    (out_dir / "attribution.json").write_text(
        json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    return document


# ---------------------------------------------------------------------------
# C5 / C6 — structured sample corpus
# ---------------------------------------------------------------------------


def run_samples(out_dir: Path, *, db_url: str, limit: Optional[int] = None) -> Dict[str, Any]:
    from sqlalchemy import create_engine

    spec_path = CONFIG / "sample_spec.json"
    spec = samples.load_spec(spec_path)
    corpus_dir = out_dir / "samples"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    labels_path = corpus_dir / "axis_labels.json"
    axis_labels = json.loads(labels_path.read_text(encoding="utf-8")) \
        if labels_path.exists() else None

    rows = list(samples.iter_rows(spec, limit=limit))
    jsonl = corpus_dir / "corpus.jsonl"
    n_written = samples.write_jsonl(rows, jsonl)

    engine = create_engine(db_url)
    load_stats = samples.load_rows(engine, rows)
    acceptance = spec.get("acceptance") or {}
    audit = samples.audit(engine, spec=spec, axis_labels=axis_labels,
                          min_rows=int(acceptance.get("min_rows", 10000)),
                          min_per_family=int(acceptance.get("min_per_family", 2000)),
                          min_per_class=int(acceptance.get("min_per_class", 1000)),
                          min_provenance=float(acceptance.get("min_provenance_completeness", 0.95)),
                          min_unique=float(acceptance.get("min_dedupe_unique_rate", 0.99)))
    audit["corpus_jsonl"] = str(jsonl)
    audit["corpus_jsonl_sha256"] = sha256_file(jsonl)
    audit["rows_written"] = n_written
    audit["spec_path"] = str(spec_path)
    audit["spec_sha256"] = sha256_file(spec_path)
    audit["db_url"] = db_url
    audit["load_stats"] = load_stats
    audit["idempotent_reload"] = samples.load_rows(engine, rows)["inserted"]

    # commit-friendly evidence: the full corpus as gzip + its head, and the hash of
    # the database the audit numbers were read from.  The .db / raw .jsonl are
    # regenerated by `python -m services.capability_governance.cli run`.
    gz_path = corpus_dir / "corpus.jsonl.gz"
    with open(jsonl, "rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    head_path = corpus_dir / "corpus_head.jsonl"
    with open(head_path, "w", encoding="utf-8") as fh:
        for row in rows[:100]:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    audit["corpus_gz"] = str(gz_path)
    audit["corpus_gz_sha256"] = sha256_file(gz_path)
    audit["corpus_head"] = str(head_path)
    audit["gz_rows_verifiable"] = _count_gz_rows(gz_path)
    db_file = _sqlite_path(db_url)
    if db_file and db_file.is_file():
        audit["db_sha256"] = sha256_file(db_file)
        audit["db_bytes"] = db_file.stat().st_size
        audit["db_retained"] = False
        engine.dispose()
        db_file.unlink()
    jsonl.unlink()
    (corpus_dir / "sample_audit.json").write_text(
        json.dumps(audit, indent=1, ensure_ascii=False), encoding="utf-8")
    return audit


def _count_gz_rows(path: Path) -> int:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _sqlite_path(db_url: str) -> Optional[Path]:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    raw = db_url[len(prefix):]
    return Path(raw) if raw and raw != ":memory:" else None


# ---------------------------------------------------------------------------
# C7 — domain-vocabulary scan
# ---------------------------------------------------------------------------


def run_domain_scan() -> Dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from tools.evidence_domain_scan import load_lexicon, scan_paths  # noqa: WPS433

    lexicon = load_lexicon()
    result = scan_paths(SCAN_TARGETS, lexicon=lexicon, root=REPO)
    return {
        "targets": SCAN_TARGETS,
        "scanned_files": result["scanned_files"],
        "terms": result["terms"],
        "hits": result["hits"],
        "n_hits": len(result["hits"]),
        "passed": len(result["hits"]) == 0,
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run(out_dir: Optional[Path] = None, *, db_url: Optional[str] = None,
        python: Optional[str] = None, skip_test_gate: bool = False,
        sample_limit: Optional[int] = None) -> Dict[str, Any]:
    out_dir = Path(out_dir or (REPO / "artifacts" / "capability_governance"))
    out_dir.mkdir(parents=True, exist_ok=True)
    # start from a clean ledger / evidence state so the run is reproducible from
    # scratch (the sample database is kept: loading it again proves idempotence)
    for rel in ("ledger.json", "ledger_events.jsonl", "backtest.json", "criteria.json",
                "pytest_junit.xml", "test_report_fallback.json"):
        target = out_dir / rel
        if target.exists():
            target.unlink()
    for rel in ("evidence_chains", "negative_controls"):
        target = out_dir / rel
        if target.is_dir():
            shutil.rmtree(target)
    db_url = db_url or f"sqlite:///{out_dir / 'ship_samples.db'}"
    started = _now()

    # 1) gate on the package's own test suite
    if skip_test_gate:
        test_gate = {"passed": True, "skipped": True, "report_path": str(
            _fallback_report(out_dir, "test gate skipped by caller"))}
        test_report = Path(test_gate["report_path"])
    else:
        test_gate = run_test_gate(out_dir, python=python)
        test_report = Path(test_gate["report_path"]) if test_gate["report_sha256"] else \
            _fallback_report(out_dir, f"pytest returncode={test_gate['returncode']}")

    # 2) C1/C2
    specs = json.loads((CONFIG / "probe_set.json").read_text(encoding="utf-8"))["endpoints"]
    ledger_run = run_auto_ledger(specs, test_report=test_report, out_dir=out_dir)
    controls = run_negative_controls(specs, test_report=test_report, out_dir=out_dir)

    # 3) C4
    attribution_doc = run_attribution(out_dir)

    # 4) C5/C6
    sample_audit = run_samples(out_dir, db_url=db_url, limit=sample_limit)

    # 5) C7
    domain_scan = run_domain_scan()

    bt = ledger_run["backtest"]
    metrics = bt["metrics"]
    ablation = bt["single_gate_ablation"]
    expectations = {
        "frozen_set_endpoints": len(specs),
        "labelled_negatives": metrics["n_negatives"],
        "labelled_positives": metrics["n_positives"],
    }

    criteria = {
        "C1_pass_creates_real_entry": {
            "n_real_entries": ledger_run["n_real"],
            "n_chains": ledger_run["chains_total"],
            "n_chains_valid": ledger_run["chains_valid"],
            "ledger_counters": ledger_run["summary"]["counters"],
            "passed": ledger_run["n_real"] > 0
                      and ledger_run["chains_valid"] == ledger_run["chains_total"]
                      and ledger_run["n_real"] == expectations["labelled_positives"],
            "evidence": [ledger_run["ledger_path"], ledger_run["events_path"],
                         ledger_run["evidence_chain_dir"]],
        },
        "C2_fail_never_upgrades": {
            "negative_controls": [{k: c[k] for k in ("control", "passed")} for c in controls["controls"]],
            "n_non_real_entries": ledger_run["n_non_real"],
            "failing_endpoints_real_count": controls["controls"][0]["real_count"],
            "passed": controls["all_passed"] and ledger_run["n_non_real"] == expectations["labelled_negatives"],
            "evidence": [str(out_dir / "negative_controls" / "negative_controls.json")],
        },
        "C3_backtest_accuracy": {
            "accuracy": metrics["accuracy"],
            "false_positive_rate": metrics["false_positive_rate"],
            "false_negative_rate": metrics["false_negative_rate"],
            "sublevel_agreement": metrics["sublevel_agreement"],
            "single_gate_ablation": {
                "accuracy": ablation["accuracy"],
                "false_positive_rate": ablation["false_positive_rate"],
                "n_false_positives": ablation["n_false_positives"],
            },
            "frozen_set_digest": bt["frozen_set_digest"],
            "n_cases": metrics["n_cases"],
            "passed": metrics["accuracy"] >= 0.90 and metrics["false_positive_rate"] < 0.05,
            "evidence": [str(out_dir / "backtest.json")],
        },
        "C4_attribution_chain": {
            "dimensions": attribution_doc["dimensions"],
            "n_cases": attribution_doc["n_cases"],
            "checks_passed": attribution_doc["n_passed"],
            "attribution_completeness": attribution_doc["attribution_completeness"],
            "mean_dimension_share": attribution_doc["mean_dimension_share"],
            "passed": (attribution_doc["all_passed"]
                       and set(attribution_doc["dimensions"]) == set(attribution.DIMENSIONS)),
            "evidence": [str(out_dir / "attribution.json")],
        },
        "C5_sample_rows": {
            "n_rows": sample_audit["n_rows"],
            "families": sample_audit["family_labels"],
            "case_classes": sample_audit["class_labels"],
            "min_per_family": sample_audit["min_per_family"],
            "min_per_class": sample_audit["min_per_class"],
            "gz_rows_verifiable": sample_audit["gz_rows_verifiable"],
            "db_sha256": sample_audit.get("db_sha256"),
            "db_bytes": sample_audit.get("db_bytes"),
            "passed": (sample_audit["criteria"]["rows_at_least_min"]
                       and sample_audit["criteria"]["every_family_at_least_min"]
                       and sample_audit["criteria"]["every_class_at_least_min"]
                       and sample_audit["gz_rows_verifiable"] >= 10000),
            "evidence": [sample_audit["corpus_gz"], sample_audit["corpus_head"],
                         str(out_dir / "samples" / "sample_audit.json")],
        },
        "C6_provenance_dedup": {
            "provenance_completeness": sample_audit["provenance_completeness"],
            "dedupe_unique_rate": sample_audit["dedupe_unique_rate"],
            "output_non_empty_ratio": sample_audit["output_non_empty_ratio"],
            "idempotent_reload_inserted": sample_audit["idempotent_reload"],
            "passed": (sample_audit["criteria"]["provenance_at_least_min"]
                       and sample_audit["criteria"]["unique_at_least_min"]
                       and sample_audit["criteria"]["output_non_empty_all"]
                       and sample_audit["idempotent_reload"] == 0),
            "evidence": [str(out_dir / "samples" / "sample_audit.json")],
        },
        "C7_zero_domain_vocabulary": {
            "scanned_files": domain_scan["scanned_files"],
            "n_hits": domain_scan["n_hits"],
            "hits": domain_scan["hits"][:10],
            "passed": domain_scan["passed"],
            "evidence": SCAN_TARGETS,
        },
    }

    criteria_doc = {
        "schema": SCHEMA,
        "node": NODE,
        "mission_id": MISSION_ID,
        "generated_at": _now(),
        "started_at": started,
        "repo": str(REPO),
        "git_commit": _git_commit(),
        "test_gate": test_gate,
        "expectations": expectations,
        "criteria": criteria,
        "all_passed": all(c["passed"] for c in criteria.values()),
        "limitations": [
            "Frozen negative set is constructed (defect modes), so the reported FPR is a "
            "lower bound, not a field estimate.",
            "Sample corpus rows are produced by the spec-declared generic IVP model; they are "
            "structurally complete and traceable but are not validated against any external "
            "reference. Plugging a real solver in means replacing spec.model, not code.",
            "Acceptance writes to the SQLite instance at samples/ when no DATABASE_URL is "
            "supplied; the loader uses the same ORM table as production, but no PostgreSQL "
            "driver is available in this sandbox (psycopg2 is a stub).",
            "Attribution corrections are first-order log-linear in the declared sensitivity "
            "exponents; strongly non-linear input response will leak into the residual "
            "(visible, not hidden).",
        ],
    }
    (out_dir / "criteria.json").write_text(
        json.dumps(criteria_doc, indent=1, ensure_ascii=False), encoding="utf-8")

    try:  # figures are optional: never fail the acceptance run for a font
        from . import figures as figures_mod
        figure_paths = figures_mod.render_all(out_dir)
        criteria_doc["figures"] = figure_paths
        (out_dir / "criteria.json").write_text(
            json.dumps(criteria_doc, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        criteria_doc["figures_error"] = f"{type(exc).__name__}: {exc}"

    return criteria_doc


def _git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None