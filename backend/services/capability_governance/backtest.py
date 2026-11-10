"""Frozen-endpoint backtest: measure the auto-grading mechanism itself.

An "auto-upgrade" pipeline is a classifier.  Saying it works is meaningless
without a labelled set and a reported false-positive rate.  This module replays a
**frozen** endpoint table (spec + expected grade, hashed as one digest so a silent
edit is visible) through the real pipeline ``probe -> grade -> evidence chain ->
ledger`` and reports:

* ``accuracy``            agreement on the decision that actually gets written into
                          the ledger: **REAL vs not-REAL**
* ``false_positive_rate`` negatives graded REAL — the error that matters, because
                          it fabricates a capability
* ``false_negative_rate`` real capabilities held back (costly, but honest)
* ``sublevel_agreement``  diagnostic agreement on *how* a negative fails
                          (MOCK fingerprint vs WITHHELD); reported for information,
                          not as a pass/fail gate, because "hardcoded" and "wrong"
                          are not always distinguishable from outside
* a single-gate **ablation** — the same set graded by one numeric check at the
  design point against a loose plausibility tolerance (what a "does it answer with
  a sensible number" test does).  The gap is the mechanism's measured power.

The labelled negatives are *constructed* defect modes (constant, scaled constant,
biased formula, deterministic jitter, unit error, clamp, quantised lattice,
call-order drift).  Honest caveat: a constructed negative set is easier than
production traffic, so the reported FPR is a lower bound, not a field estimate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .grading import build_chain, digest_of, grade_probe, sha256_file
from .ledger import CapabilityLedger
from .probes import run_endpoint

SCHEMA = "capability-autograde-backtest/1.0"
_PROBES_SOURCE = Path(__file__).with_name("probes.py")


def freeze_digest(specs: Sequence[Mapping[str, Any]]) -> str:
    """Content digest of the frozen endpoint table (order-sensitive)."""
    return digest_of([dict(s) for s in specs])


def predicted_level(verdict: Mapping[str, Any]) -> str:
    """Ledger-visible level, with the MOCK fingerprint kept as a diagnostic."""
    if verdict.get("observed_level") == "MOCK":
        return "MOCK"
    return str(verdict["decided_level"])


def single_gate_label(spec: Mapping[str, Any], verdict: Mapping[str, Any]) -> str:
    """Ablation baseline: one numeric check at the design point, loose tolerance."""
    errors = verdict.get("errors_pct") or [float("inf")]
    return "REAL" if errors[0] <= float(spec.get("loose_tol_pct", 1.0)) else "WITHHELD"


def run_frozen_set(specs: Sequence[Mapping[str, Any]], *, test_report_path: Path,
                   mission_id: int, ledger: CapabilityLedger,
                   routine_source_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Replay the frozen table through the pipeline and score it."""
    routine_sha = routine_source_sha256 or sha256_file(_PROBES_SOURCE)
    rows: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []

    for spec in specs:
        result = run_endpoint(spec)
        spec_digest = digest_of(dict(spec))
        verdict = grade_probe(result.to_dict(), spec, routine_sha, spec_digest)
        chain = build_chain(verdict, test_report_path=Path(test_report_path), mission_id=mission_id,
                            routine_source_sha256=routine_sha, spec_digest=spec_digest)
        decision = ledger.apply(chain, mission_id)
        chains.append(chain)
        pred = predicted_level(verdict)
        baseline = single_gate_label(spec, verdict)
        rows.append({
            "endpoint_id": spec["endpoint_id"],
            "routine": spec["routine"],
            "defect": spec["defect"],
            "expected_level": spec["expected_level"],
            "predicted_level": pred,
            "ledger_level": decision.get("level"),
            "ledger_action": decision.get("action"),
            "chain_digest": chain["chain_digest"],
            "criteria": {c["name"]: c["passed"] for c in verdict["criteria"]},
            "finest_error_pct": verdict["errors_pct"][-1],
            "first_error_pct": verdict["errors_pct"][0],
            "observed_level": verdict.get("observed_level"),
            "expected_is_real": spec["expected_level"] == "REAL",
            "predicted_is_real": pred == "REAL",
            "correct": (spec["expected_level"] == "REAL") == (pred == "REAL"),
            "sublevel_correct": spec["expected_level"] == pred,
            "single_gate_level": baseline,
        })

    pipeline = _score(rows, level_key="predicted_level")
    ablation = _score(rows, level_key="single_gate_level")
    return {
        "schema": SCHEMA,
        "frozen_set_digest": freeze_digest(specs),
        "n_cases": len(specs),
        "test_report": str(test_report_path),
        "routine_source_sha256": routine_sha,
        "metrics": pipeline,
        "single_gate_ablation": ablation,
        "rows": rows,
        "chains": chains,
    }


def _score(rows: Sequence[Mapping[str, Any]], level_key: str) -> Dict[str, Any]:
    total = len(rows) or 1
    correct = sum(1 for r in rows if (r["expected_is_real"]) == (r[level_key] == "REAL"))
    positives = [r for r in rows if r["expected_is_real"]]
    negatives = [r for r in rows if not r["expected_is_real"]]
    false_positives = [r for r in negatives if r[level_key] == "REAL"]
    false_negatives = [r for r in positives if r[level_key] != "REAL"]
    confusion: Dict[str, Dict[str, int]] = {}
    for r in rows:
        exp = r["expected_level"]
        got = r[level_key]
        confusion.setdefault(exp, {})
        confusion[exp][got] = confusion[exp].get(got, 0) + 1
    sublevel = sum(1 for r in negatives if r["expected_level"] == r[level_key])
    return {
        "n_cases": len(rows),
        "n_positives": len(positives),
        "n_negatives": len(negatives),
        "accuracy": correct / total,
        "false_positive_rate": (len(false_positives) / len(negatives)) if negatives else 0.0,
        "false_negative_rate": (len(false_negatives) / len(positives)) if positives else 0.0,
        "n_false_positives": len(false_positives),
        "n_false_negatives": len(false_negatives),
        "false_positive_ids": [r["endpoint_id"] for r in false_positives],
        "false_negative_ids": [r["endpoint_id"] for r in false_negatives],
        "sublevel_agreement": (sublevel / len(negatives)) if negatives else 1.0,
        "confusion": confusion,
    }