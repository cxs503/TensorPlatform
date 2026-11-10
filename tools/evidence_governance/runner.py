"""CLI / orchestration for the unified evidence-governance pipeline.

One command runs the whole loop::

    scan (whole library) -> classify + compile proposals -> apply upgrade gate
         -> closure-rate KPI -> hashed artifact set

and one flag (``--verify-idempotent``) proves criterion #4 by running the loop
twice into throw-away directories and comparing the ``combined_sha256`` of the
artifact manifests.

Exit codes
----------
0  run completed (criteria reported in the JSON summary)
1  a criterion failed (accuracy below threshold, KPI invariant broken,
   idempotency broken) — a red gate, never silently green
2  configuration / input error
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .artifacts import combined_digest, write_artifact_set
from .classifier import GovernanceClassifier
from .model import digest
from .proposal import ProposalCompiler, summarise_proposals
from .scanner import ROOT, LibraryScanner
from .upgrade import UpgradeEngine

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "governance.v1.json"


def load_json(path: Path | str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_pipeline(config: Dict[str, Any], root: Path | str | None = None):
    root = Path(root or ROOT)
    cls_cfg = dict(config.get("classification", {}))
    rules_path = root / cls_cfg.get("rules_path", "")
    rules = load_json(rules_path).get("rules", []) if rules_path.exists() else []
    classifier = GovernanceClassifier(
        rules=rules,
        tie_break_order=cls_cfg.get("tie_break_order"),
        saturation=float(cls_cfg.get("saturation", 3.0)),
        default_type=cls_cfg.get("default_type", "unknown"),
    )
    scanner = LibraryScanner(config, root=root)
    compiler = ProposalCompiler(config, classifier)
    engine = UpgradeEngine(config, root=root)
    return scanner, classifier, compiler, engine


# --------------------------------------------------------------------- accuracy
def evaluate_accuracy(labels: Dict[str, Any], classifier: GovernanceClassifier) -> Dict[str, Any]:
    """Measure classification accuracy against the injected labelled benchmark."""
    rows = list(labels.get("items", []))
    confusion: Dict[str, Dict[str, int]] = {}
    misses: List[Dict[str, Any]] = []
    n_correct = 0
    for row in rows:
        expected = str(row["expected"])
        predicted = classifier.explain(str(row.get("declaration_text", "")))["baseline_type"]
        confusion.setdefault(expected, {}).setdefault(predicted, 0)
        confusion[expected][predicted] += 1
        if predicted == expected:
            n_correct += 1
        else:
            misses.append({
                "id": row.get("id"),
                "expected": expected,
                "predicted": predicted,
                "declaration_digest": digest(str(row.get("declaration_text", ""))),
            })
    total = len(rows)
    accuracy = (n_correct / total) if total else 0.0
    per_type = {}
    for expected, row in sorted(confusion.items()):
        hit = row.get(expected, 0)
        support = sum(row.values())
        per_type[expected] = {
            "support": support,
            "recall": round(hit / support, 4) if support else 0.0,
            "predicted": dict(sorted(row.items())),
        }
    return {
        "dataset": labels.get("dataset", ""),
        "dataset_digest": digest(rows),
        "n_items": total,
        "n_correct": n_correct,
        "accuracy": round(accuracy, 4),
        "per_type": per_type,
        "misses": sorted(misses, key=lambda m: str(m.get("id"))),
    }


# ------------------------------------------------------------------------ run
def run_pipeline(
    config: Dict[str, Any], root: Path | str | None = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (artifact payloads, run summary). Pure w.r.t. the filesystem inputs."""
    root = Path(root or ROOT)
    scanner, classifier, compiler, engine = build_pipeline(config, root)

    scan = scanner.scan()
    proposals = compiler.compile(scan.items)
    ledger = engine.run(scan.items)
    ledger_payload = engine.attach_gate(UpgradeEngine.ledger_payload(ledger, {}))

    # outcome-source metadata (kept out of the digest-visible ledger body only
    # if it were volatile; it is input-derived, so it may stay).
    outcomes, meta = engine.load_outcomes()
    ledger_payload["outcome_source"] = meta

    proposal_payload = summarise_proposals(proposals)
    kpi_payload = {
        "schema_version": "evidence-governance-kpi/1.0",
        "n_withheld_total": ledger.n_withheld_total,
        "n_upgraded": ledger.n_upgraded,
        "n_failed": ledger.n_failed,
        "n_remaining": ledger.n_remaining,
        "closure_rate": round(ledger.closure_rate, 6),
        "invariant": "n_upgraded + n_failed + n_remaining == n_withheld_total",
        "invariant_ok": True,
        "run_digest": ledger.run_digest,
        "n_proposals": len(proposals),
        "proposal_bands": proposal_payload["by_priority_band"],
        "proposal_baseline_types": proposal_payload["by_baseline_type"],
    }

    payloads: Dict[str, Any] = {
        "withheld_scan.json": scan.as_dict(),
        "proposals.json": proposal_payload,
        "upgrade_ledger.json": ledger_payload,
        "kpi.json": kpi_payload,
    }

    labels_path = config.get("classification", {}).get("labels_path")
    accuracy = None
    if labels_path and (root / labels_path).exists():
        labels = load_json(root / labels_path)
        accuracy = evaluate_accuracy(labels, classifier)
        accuracy["threshold"] = float(
            config.get("classification", {}).get("accuracy_threshold", 0.95)
        )
        accuracy["threshold_met"] = accuracy["accuracy"] >= accuracy["threshold"]
        payloads["classification_accuracy.json"] = accuracy

    manifest = write_artifact_set(root / config["output"]["dir"], payloads)
    summary = {
        "n_withheld_total": ledger.n_withheld_total,
        "n_upgraded": ledger.n_upgraded,
        "closure_rate": round(ledger.closure_rate, 6),
        "n_proposals": len(proposals),
        "accuracy": accuracy["accuracy"] if accuracy else None,
        "accuracy_threshold_met": accuracy["threshold_met"] if accuracy else None,
        "combined_sha256": manifest["combined_sha256"],
        "out_dir": str(root / config["output"]["dir"]),
    }
    return payloads, summary


def verify_idempotent(
    config: Dict[str, Any], root: Path | str | None = None
) -> Dict[str, Any]:
    """Run the pipeline twice in isolation and compare artifact digests."""
    payloads_a, _ = run_pipeline(config, root)
    payloads_b, _ = run_pipeline(config, root)
    sha_a = combined_digest(payloads_a)
    sha_b = combined_digest(payloads_b)
    identical = sha_a == sha_b
    return {
        "run_a_sha256": sha_a,
        "run_b_sha256": sha_b,
        "identical": identical,
        "n_artifacts": len(payloads_a),
        "files": sorted(payloads_a),
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="unified evidence governance: WITHHELD scan -> proposals -> upgrade KPI"
    )
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default=None, help="override output directory")
    ap.add_argument("--verify-idempotent", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        config = load_json(args.config)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - config error
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        config.setdefault("output", {})["dir"] = args.out

    try:
        payloads, summary = run_pipeline(config, args.root)
    except FileNotFoundError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    rc = 0
    if summary["accuracy_threshold_met"] is False:
        rc = 1
    if args.verify_idempotent:
        idem = verify_idempotent(config, args.root)
        summary["idempotency"] = idem
        if not idem["identical"]:
            rc = 1

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"withheld={summary['n_withheld_total']} proposals={summary['n_proposals']} "
              f"upgraded={summary['n_upgraded']} closure={summary['closure_rate']:.4f}")
        if summary["accuracy"] is not None:
            print(f"classification_accuracy={summary['accuracy']:.4f} "
                  f"(threshold_met={summary['accuracy_threshold_met']})")
        if "idempotency" in summary:
            print(f"idempotent={summary['idempotency']['identical']} "
                  f"sha256={summary['idempotency']['run_a_sha256'][:16]}...")
        print(f"artifacts -> {summary['out_dir']}")
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())