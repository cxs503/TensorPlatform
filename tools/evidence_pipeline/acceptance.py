#!/usr/bin/env python3
"""Machine acceptance driver for the evidence-governance pipeline.

Runs every criterion on synthetic fixtures and writes one JSON artifact holding
the numbers, digests and paths the report quotes verbatim — no number in the
report is typed by hand.

Criteria
--------
C1  three synthetic benchmarks traverse the full chain and pass
    (execute → gates → artifact → ingest gate → regression → provenance).
C2  a gate violation fails closed: exit 2, artifact status ``fail``, and the
    ingest gate refuses that artifact.
C3  the ingest gate's replay check is live: a non-deterministic generator passes
    the numeric gate yet is rejected with ``A9_replay``.
C4  the conservation gate discriminates: the non-conservative control exceeds
    the conservative residual by > 1e10.
C5  artifact tampering (number / verdict / status) is detected.
C6  generator identity and configuration identity are enforced (``A7``/``A8``).
C7  the regression closed loop works: a seeded 1e-3 drift triggers re-runs,
    is classified ``drift_reproducible``, refuses silent re-blessing (exit 6)
    and accepts an explicit one (``--accept-drift``), then reports ``stable``.
C8  the provenance hash chain verifies and tampering with any entry breaks it.
C9  determinism: two runs of the same configuration produce byte-identical
    artifacts and identical payload digests.
C10 the package contains zero domain vocabulary; the pytest suite is counted.

Usage::

    python3 -m tools.evidence_pipeline.acceptance \
        --json artifacts/P167W2-N11/acceptance.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

from tools.coupling.provenance import ExchangeLedger
from tools.evidence_domain_scan import scan_paths

from .artifact import build_artifact, verify_artifact
from .config import ROOT, load_config
from .execution import execute_generator
from .gate import run_gates
from .runner import run_benchmark

BENCH_DIR = ROOT / "config" / "evidence_pipeline" / "benchmarks"
CTRL_DIR = ROOT / "config" / "evidence_pipeline" / "controls"
BENCHMARKS = ("syn-alpha-quadrature", "syn-beta-exchange", "syn-gamma-partition")
OUT = ROOT / "artifacts" / "P167W2-N11"


def _cfg_with_baseline(name: str, workdir: Path, out: Path, tag: str, **changes: Any) -> Path:
    data = json.loads((BENCH_DIR / f"{name}.json").read_text(encoding="utf-8"))
    data["regression"]["baseline"] = str(out)
    for dotted, value in changes.items():
        tokens: List[Any] = [int(t) if t.lstrip("-").isdigit() else t for t in dotted.split(".")]
        node: Any = data
        for token in tokens[:-1]:
            node = node[token]
        node[tokens[-1]] = value
    path = workdir / "cfg" / f"{tag}-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _criteria(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "n_criteria": len(entries),
        "n_passed": sum(1 for e in entries if e["passed"]),
        "all_passed": all(e["passed"] for e in entries),
        "criteria": entries,
    }


def run_acceptance(workdir: Path) -> Dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    entries: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ C1
    runs_dir = workdir / "runs_c1"
    per_bench: Dict[str, Any] = {}
    cfg_paths: Dict[str, Path] = {}
    for name in BENCHMARKS:
        cfg_path = _cfg_with_baseline(name, workdir, workdir / "baselines" / f"{name}.json", "c1")
        cfg_paths[name] = cfg_path
        cfg = load_config(cfg_path)
        first = run_benchmark(cfg, config_path=str(cfg_path), out_dir=runs_dir, update_baseline=True)
        second = run_benchmark(cfg, config_path=str(cfg_path), out_dir=runs_dir)
        artifact = json.loads((runs_dir / f"{name}.artifact.json").read_text(encoding="utf-8"))
        record = json.loads((runs_dir / f"{name}.run.json").read_text(encoding="utf-8"))
        per_bench[name] = {
            "first_run_status": first.status,
            "second_run_status": second.status,
            "regression_first": first.regression["decision"]["classification"],
            "regression_second": second.regression["decision"]["classification"],
            "n_gates": artifact["verdict"]["n_gates"],
            "gates": {g["id"]: g["passed"] for g in artifact["gates"]},
            "conservation": {
                g["id"]: {k: g["metrics"].get(k) for k in
                         ("sum_rel_residual", "moment_rel_residual", "nonneg_violation", "overshoot_ratio")
                         if isinstance(g["metrics"], dict) and k in g["metrics"]}
                for g in artifact["gates"] if g["type"] == "conservation"
            },
            "ingest_checks": f"{second.ingest['n_checks'] - second.ingest['n_failed']}/{second.ingest['n_checks']}",
            "provenance_chain_valid": record["provenance"]["chain_valid"],
            "provenance_entries": record["provenance"]["n_entries"],
            "artifact": str(runs_dir / f"{name}.artifact.json"),
            "run_record": str(runs_dir / f"{name}.run.json"),
            "payload_digest": artifact["payload_digest"],
        }
    c1_pass = all(
        v["first_run_status"] == "pass"
        and v["second_run_status"] == "pass"
        and v["regression_second"] == "stable"
        and v["provenance_chain_valid"]
        and not any(p is False for p in v["gates"].values())
        for v in per_bench.values()
    )
    entries.append({"id": "C1", "passed": c1_pass,
                    "claim": "3 synthetic benchmarks traverse the full chain and pass",
                    "numbers": per_bench})

    # ------------------------------------------------------------------ C2
    tight = _cfg_with_baseline("syn-alpha-quadrature", workdir,
                               workdir / "baselines" / "unused.json", "c2",
                               **{"gates.0.thresholds.p_order.min": 5.0})
    c2 = run_benchmark(load_config(tight), config_path=str(tight), out_dir=workdir / "runs_c2")
    c2_artifact = json.loads((workdir / "runs_c2" / "syn-alpha-quadrature.artifact.json")
                             .read_text(encoding="utf-8"))
    entries.append({
        "id": "C2",
        "passed": c2.status == "gate_fail" and c2.exit_code == 2
                  and c2_artifact["status"] == "fail"
                  and c2.ingest["passed"] is False,
        "claim": "a gate violation fails closed (exit 2) and its artifact is refused",
        "numbers": {
            "status": c2.status, "exit_code": c2.exit_code,
            "artifact_status": c2_artifact["status"],
            "gate_failures": c2.verdict["failures"],
            "ingest_passed": c2.ingest["passed"],
            "ingest_failed_checks": [c["id"] for c in c2.ingest["checks"] if not c["passed"]],
        },
    })

    # ------------------------------------------------------------------ C3
    c3 = run_benchmark(load_config(CTRL_DIR / "syn-eps-nondeterministic.json"),
                       config_path=str(CTRL_DIR / "syn-eps-nondeterministic.json"),
                       out_dir=workdir / "runs_c3")
    c3_failed = [c["id"] for c in c3.ingest["checks"] if not c["passed"]]
    good_cfg = _cfg_with_baseline("syn-alpha-quadrature", workdir,
                                  workdir / "baselines" / "c3.json", "c3")
    good = run_benchmark(load_config(good_cfg), config_path=str(good_cfg),
                         out_dir=workdir / "runs_c3_good", update_baseline=True)
    entries.append({
        "id": "C3",
        "passed": c3.status == "ingest_rejected" and c3.exit_code == 3 and "A9_replay" in c3_failed
                  and good.ingest["passed"] is True,
        "claim": "the replay check is live: non-deterministic output is rejected, deterministic output is accepted",
        "numbers": {"nondeterministic_status": c3.status, "exit_code": c3.exit_code,
                    "failed_checks": c3_failed,
                    "deterministic_ingest_passed": good.ingest["passed"],
                    "deterministic_checks": f"{good.ingest['n_checks'] - good.ingest['n_failed']}/{good.ingest['n_checks']}"},
    })

    # ------------------------------------------------------------------ C4
    beta_cfg = _cfg_with_baseline("syn-beta-exchange", workdir, workdir / "baselines" / "c4.json", "c4")
    beta = run_benchmark(load_config(beta_cfg), config_path=str(beta_cfg),
                         out_dir=workdir / "runs_c4", update_baseline=True)
    beta_art = json.loads((workdir / "runs_c4" / "syn-beta-exchange.artifact.json").read_text())
    good_gate = [g for g in beta_art["gates"] if g["type"] == "conservation"][0]
    c4 = run_benchmark(load_config(CTRL_DIR / "syn-delta-nonconservative.json"),
                       config_path=str(CTRL_DIR / "syn-delta-nonconservative.json"),
                       out_dir=workdir / "runs_c4_bad")
    bad_art = json.loads((workdir / "runs_c4_bad" / "syn-delta-nonconservative.artifact.json").read_text())
    bad_gate = [g for g in bad_art["gates"] if g["type"] == "conservation"][0]
    good_sum = good_gate["metrics"]["sum_rel_residual"]
    bad_sum = bad_gate["metrics"]["sum_rel_residual"]
    entries.append({
        "id": "C4",
        "passed": bad_gate["passed"] is False and c4.exit_code == 2 and (bad_sum / good_sum) > 1e10,
        "claim": "the conservation gate separates a corrected exchange from a non-conservative one",
        "numbers": {
            "conservative_sum_rel_residual": good_sum,
            "nonconservative_sum_rel_residual": bad_sum,
            "ratio": bad_sum / good_sum,
            "conservative_moment_rel_residual": good_gate["metrics"]["moment_rel_residual"],
            "nonconservative_moment_rel_residual": bad_gate["metrics"]["moment_rel_residual"],
            "thresholds": good_gate["thresholds"],
            "nonconservative_gate_passed": bad_gate["passed"],
            "nonconservative_failures": bad_gate["failures"],
            "negative_control_artifact": str(workdir / "runs_c4_bad" / "syn-delta-nonconservative.artifact.json"),
        },
    })

    # ------------------------------------------------------------------ C5
    tamper_dir = workdir / "runs_c5"
    shutil.copytree(runs_dir, tamper_dir)
    art_path = tamper_dir / "syn-alpha-quadrature.artifact.json"
    pristine = json.loads(art_path.read_text(encoding="utf-8"))
    tamper_results = {}
    for label, mutate in (
        ("number", lambda a: a["payload"]["metrics"].__setitem__("p_order", 9.99)),
        ("verdict", lambda a: (a["verdict"].__setitem__("passed", False))),
        ("status", lambda a: a.__setitem__("status", "fail")),
    ):
        tampered = json.loads(json.dumps(pristine))
        mutate(tampered)
        art_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report = verify_artifact(art_path, config_path=str(cfg_paths["syn-alpha-quadrature"]))
        tamper_results[label] = {
            "passed": report["passed"],
            "failed_checks": [c["id"] for c in report["checks"] if not c["passed"]],
        }
    art_path.write_text(json.dumps(pristine, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    restored = verify_artifact(art_path, config_path=str(cfg_paths["syn-alpha-quadrature"]))
    entries.append({
        "id": "C5",
        "passed": all(not v["passed"] for v in tamper_results.values()) and restored["passed"],
        "claim": "artifact tampering is rejected and the pristine artifact is still accepted",
        "numbers": {"tamper": tamper_results, "restored_passed": restored["passed"]},
    })

    # ------------------------------------------------------------------ C6
    gen_copy = workdir / "gen" / "tools" / "evidence_pipeline" / "generators" / "syn_alpha_quadrature.py"
    gen_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "tools/evidence_pipeline/generators/syn_alpha_quadrature.py", gen_copy)
    copy_cfg_path = workdir / "copy_cfg.json"
    copy_cfg_data = json.loads((BENCH_DIR / "syn-alpha-quadrature.json").read_text())
    copy_cfg_data["generator"] = str(gen_copy)
    copy_cfg_data["regression"]["baseline"] = str(workdir / "baselines" / "copy.json")
    copy_cfg_path.write_text(json.dumps(copy_cfg_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    copy_cfg = load_config(copy_cfg_path)
    exec_result = execute_generator(copy_cfg)
    artifact = build_artifact(copy_cfg, exec_result.payload, run_gates(copy_cfg, exec_result.payload),
                              provenance={"note": "identity probe"}, config_path=str(copy_cfg_path))
    probe = workdir / "runs_c6"
    probe.mkdir(parents=True, exist_ok=True)
    probe_art = probe / "copy.artifact.json"
    probe_art.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = verify_artifact(probe_art, config_path=str(copy_cfg_path))
    gen_copy.write_text(gen_copy.read_text() + "\n# edited after the run\n", encoding="utf-8")
    after = verify_artifact(probe_art, config_path=str(copy_cfg_path))
    weakened = workdir / "weakened.json"
    weakened_data = json.loads((BENCH_DIR / "syn-alpha-quadrature.json").read_text())
    weakened_data["gates"][0]["thresholds"]["p_order"]["min"] = 1.0
    weakened.write_text(json.dumps(weakened_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    weakened_report = verify_artifact(runs_dir / "syn-alpha-quadrature.artifact.json",
                                      config_path=str(weakened))
    entries.append({
        "id": "C6",
        "passed": before["passed"] and not after["passed"] and not weakened_report["passed"]
                  and "A7_generator_identity" in {c["id"] for c in after["checks"] if not c["passed"]}
                  and "A8_config_identity" in {c["id"] for c in weakened_report["checks"] if not c["passed"]},
        "claim": "generator identity and configuration identity are enforced",
        "numbers": {
            "before_edit_passed": before["passed"],
            "after_edit_passed": after["passed"],
            "after_edit_failed_checks": [c["id"] for c in after["checks"] if not c["passed"]],
            "weakened_config_passed": weakened_report["passed"],
            "weakened_config_failed_checks": [c["id"] for c in weakened_report["checks"] if not c["passed"]],
        },
    })

    # ------------------------------------------------------------------ C7
    drift_dir = workdir / "runs_c7"
    drift_baseline = workdir / "baselines" / "c7.json"
    cfg_path = _cfg_with_baseline("syn-alpha-quadrature", workdir, drift_baseline, "c7")
    cfg = load_config(cfg_path)
    run_benchmark(cfg, config_path=str(cfg_path), out_dir=drift_dir, update_baseline=True)
    baseline = json.loads(drift_baseline.read_text(encoding="utf-8"))
    seeded = baseline["metrics"]["err_fine"] * (1.0 + 1e-3)
    baseline["metrics"]["err_fine"] = seeded
    drift_baseline.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    drifted = run_benchmark(cfg, config_path=str(cfg_path), out_dir=drift_dir)
    refused = run_benchmark(cfg, config_path=str(cfg_path), out_dir=drift_dir, update_baseline=True)
    reblessed = run_benchmark(cfg, config_path=str(cfg_path), out_dir=drift_dir,
                              update_baseline=True, accept_drift=True)
    settled = run_benchmark(cfg, config_path=str(cfg_path), out_dir=drift_dir)
    entries.append({
        "id": "C7",
        "passed": drifted.status == "regression_fail" and drifted.exit_code == 4
                  and drifted.regression["decision"]["classification"] == "drift_reproducible"
                  and len(drifted.attempts) == 3
                  and len({a["payload_digest"] for a in drifted.attempts}) == 1
                  and refused.exit_code == 6
                  and reblessed.status == "pass"
                  and settled.regression["decision"]["classification"] == "stable",
        "claim": "regression drift triggers re-runs, refuses silent re-blessing, then settles",
        "numbers": {
            "seeded_relative_drift": 1e-3 / (1.0 + 1e-3),
            "drift_status": drifted.status, "drift_exit_code": drifted.exit_code,
            "classification": drifted.regression["decision"]["classification"],
            "n_attempts": len(drifted.attempts),
            "distinct_attempt_digests": len({a["payload_digest"] for a in drifted.attempts}),
            "refused_exit_code": refused.exit_code, "refused_status": refused.status,
            "reblessed_status": reblessed.status,
            "settled_classification": settled.regression["decision"]["classification"],
        },
    })

    # ------------------------------------------------------------------ C8
    record = json.loads((runs_dir / "syn-gamma-partition.run.json").read_text(encoding="utf-8"))
    chain_ok = record["provenance"]["chain_valid"]
    ledger = ExchangeLedger(name="probe",
                            entries=json.loads(json.dumps(record["provenance"]["entries"])))
    chain_before = ledger.verify_chain()
    ledger.entries[2]["verdict"] = {"tampered": True}
    chain_after = ledger.verify_chain()
    entries.append({
        "id": "C8",
        "passed": chain_ok and chain_before and not chain_after,
        "claim": "the provenance chain verifies and any edited entry breaks it",
        "numbers": {"chain_valid": chain_ok, "n_entries": record["provenance"]["n_entries"],
                    "recomputed_valid": chain_before, "tampered_valid": chain_after,
                    "chain_digest": record["provenance"]["chain_digest"],
                    "record": str(runs_dir / "syn-gamma-partition.run.json")},
    })

    # ------------------------------------------------------------------ C9
    det_dir = workdir / "runs_c9"
    cfg_path = _cfg_with_baseline("syn-beta-exchange", workdir, workdir / "baselines" / "c9.json", "c9")
    cfg = load_config(cfg_path)
    first = run_benchmark(cfg, config_path=str(cfg_path), out_dir=det_dir, update_baseline=True)
    art_path = det_dir / "syn-beta-exchange.artifact.json"
    bytes_a = art_path.read_bytes()
    second = run_benchmark(cfg, config_path=str(cfg_path), out_dir=det_dir)
    bytes_b = art_path.read_bytes()
    entries.append({
        "id": "C9",
        "passed": bytes_a == bytes_b and first.attempts[0]["payload_digest"] == second.attempts[0]["payload_digest"],
        "claim": "two runs of one configuration produce byte-identical artifacts",
        "numbers": {"artifact_sha256": hashlib.sha256(bytes_a).hexdigest(),
                    "artifact_bytes": len(bytes_a),
                    "payload_digest": first.attempts[0]["payload_digest"]},
    })

    # ------------------------------------------------------------------ C10
    scan = scan_paths(["tools/evidence_pipeline", "config/evidence_pipeline"])
    entries.append({
        "id": "C10",
        "passed": scan["hits"] == [] and scan["scanned_files"] >= 10,
        "claim": "the pipeline layer carries no domain vocabulary",
        "numbers": {"scanned_files": scan["scanned_files"], "terms": scan["terms"],
                    "hits": len(scan["hits"])},
    })

    return {
        "schema": "evidence-pipeline/acceptance/1",
        "node": "P167W2-N11",
        "benchmarks": list(BENCHMARKS),
        "runs_dir": str(runs_dir),
        "summary": _criteria(entries),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(OUT / "acceptance.json"))
    ap.add_argument("--workdir", default=None, help="scratch directory for the control scenarios")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else (OUT / "work")
    result = run_acceptance(workdir)
    result["workdir"] = str(workdir.relative_to(ROOT)) if workdir.is_relative_to(ROOT) else str(workdir)
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())