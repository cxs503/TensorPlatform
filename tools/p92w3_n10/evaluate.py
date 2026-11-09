"""Frozen evaluation for P92W3-N10 (terminal-state layering + closure integrity).

Stages
------
``sample``  build the stratified adjudication sample (evidence summaries only)
``full``    primary tables, confidence intervals, label-noise bound, metrics.json

Pre-registration: runs/p92w3-n10/preregistration.json (written before any
primary table existed). Corpus: runs/p92w3-n10/corpus/missions_succeeded.json
(sha256 recorded next to it).

Zero domain semantics: only file/check/claim facts and process facts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.domain.delivery_state import (  # noqa: E402
    assess_attested_delivery,
    assess_delivery,
    classify_closure,
    compute_missing_item_counts,
    normalize_artifacts,
)

CORPUS = ROOT / "runs/p92w3-n10/corpus/missions_succeeded.json"
SHA = ROOT / "runs/p92w3-n10/corpus/missions_succeeded.sha256"
OUT = ROOT / "runs/p92w3-n10"
FORCED_TOKEN = "forced_finish"
STATES = ("delivered_complete", "delivered_partial", "suspect_undeclared")
SEED = 20261109


def load_corpus() -> list[dict]:
    raw = CORPUS.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    recorded = SHA.read_text(encoding="utf-8").strip() if SHA.is_file() else ""
    if recorded and digest != recorded:
        raise SystemExit(f"corpus sha256 mismatch: {digest} != {recorded}")
    return json.loads(raw)


def _result(rec: dict) -> dict | None:
    value = rec.get("result")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def is_forced(rec: dict) -> bool:
    """Weak label — harness fact. Never read by the classifiers under test."""
    res = _result(rec) or {}
    return FORCED_TOKEN in str(res.get("fail_stage") or "")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4)}


def bootstrap_f1(pairs: list[tuple[bool, bool]], resamples: int = 2000, seed: int = SEED) -> list[float]:
    """Bootstrap 95% CI for F1 over the population (vectorised, numpy)."""
    import numpy as np

    if not pairs:
        return [0.0, 0.0]
    arr = np.asarray(pairs, dtype=bool)          # column 0 = label, 1 = prediction
    label, pred = arr[:, 0], arr[:, 1]
    rng = np.random.default_rng(seed)
    n = len(arr)
    f1s = np.empty(resamples)
    for i in range(resamples):
        idx = rng.integers(0, n, n)
        tp = int(np.sum(label[idx] & pred[idx]))
        fp = int(np.sum(~label[idx] & pred[idx]))
        fn = int(np.sum(label[idx] & ~pred[idx]))
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s[i] = (2 * p * r / (p + r)) if p + r else 0.0
    f1s.sort()
    return [round(float(f1s[int(0.025 * resamples)]), 4),
            round(float(f1s[int(0.975 * resamples) - 1]), 4)]


def macro_f1(confusion: dict[tuple[str, str], int]) -> float:
    scores = []
    for state in STATES:
        tp = confusion.get((state, state), 0)
        fp = sum(confusion.get((other, state), 0) for other in STATES if other != state)
        fn = sum(confusion.get((state, other), 0) for other in STATES if other != state)
        scores.append(prf(tp, fp, fn)["f1"])
    return round(sum(scores) / len(scores), 4)


def evidence_summary(rec: dict) -> dict:
    """Compact, auditable evidence for human adjudication (no label leaked)."""
    res = _result(rec) or {}
    facts = normalize_artifacts(res)
    checks = facts.verification_text.replace("\n", " ")[:160]
    claim_keys = [k for k in ("summary", "headline", "evidence", "seed_report",
                              "report_id", "decision_note", "negative_control") if k in res]
    return {
        "id": rec.get("id"),
        "repo": rec.get("repo"),
        "implementation_count": len(facts.implementation),
        "implementation_sample": facts.implementation[:4],
        "checks_len": len(facts.verification_text),
        "checks_concrete": facts.verification_concrete,
        "checks_excerpt": checks,
        "claim_keys": claim_keys,
        "optional_missing": facts.missing_optional,
    }


def sample_stage(records: list[dict], per_stratum: int = 30) -> dict:
    rng = random.Random(SEED)
    forced, suspect = [], []
    for rec in records:
        res = _result(rec)
        artifact_state = assess_delivery(res).state
        if is_forced(rec):
            forced.append(rec)
        elif artifact_state == "suspect_undeclared":
            suspect.append(rec)
    sample = []
    for stratum, pool in (("forced_finish", forced), ("non_forced_suspect", suspect)):
        pool = sorted(pool, key=lambda r: r.get("id") or 0)
        picked = rng.sample(pool, min(per_stratum, len(pool)))
        for rec in picked:
            item = evidence_summary(rec)
            item["stratum"] = stratum
            sample.append(item)
    sample.sort(key=lambda e: (e["stratum"], e["id"] or 0))
    payload = {
        "seed": SEED,
        "per_stratum": per_stratum,
        "n": len(sample),
        "rubric": {
            "delivered_complete": "implementation >=1 file AND checks carry a machine-checkable "
                                  "statement AND a claim artifact is present",
            "delivered_partial": "some artifacts present but at least one of "
                                 "{implementation, machine-checkable checks, claim} is absent",
            "suspect_undeclared": "no implementation artifact at all (claim without work product)",
        },
        "items": sample,
    }
    (OUT / "adjudication_sample.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"n": len(sample), "strata": {
        "forced_finish": sum(1 for s in sample if s["stratum"] == "forced_finish"),
        "non_forced_suspect": sum(1 for s in sample if s["stratum"] == "non_forced_suspect")}}


def full_stage(records: list[dict]) -> dict:
    rows = []
    for rec in records:
        res = _result(rec)
        artifact = assess_delivery(res)
        attested = assess_attested_delivery(res)
        rows.append({
            "id": rec.get("id"),
            "fail_stage": str((res or {}).get("fail_stage") or ""),
            "forced": is_forced(rec),
            "closure": classify_closure(res),
            "artifact_state": artifact.state,
            "attested_state": attested.state,
            "artifact_missing": artifact.missing,
            "attested_missing": attested.missing,
            "has_payload": bool(res),
        })

    n = len(rows)
    n_forced = sum(1 for r in rows if r["forced"])
    n_non_forced = n - n_forced

    def rule_metrics(state_key: str, positive_states: set[str]) -> dict:
        tp = sum(1 for r in rows if r["forced"] and r[state_key] in positive_states)
        fp = sum(1 for r in rows if not r["forced"] and r[state_key] in positive_states)
        fn = n_forced - tp
        m = prf(tp, fp, fn)
        m["recall_ci"] = wilson(tp, n_forced)
        m["precision_ci"] = wilson(tp, tp + fp)
        m["f1_ci_bootstrap"] = bootstrap_f1([(r["forced"], r[state_key] in positive_states) for r in rows])
        return m

    not_complete = {"delivered_partial", "suspect_undeclared"}

    # R3 = replay of the label itself (flag iff the marker is present). It scores
    # precision = recall = 1.0 by construction and therefore carries *no*
    # information about delivery; it exists only to show that the only rule which
    # satisfies the pre-registered P/R pair is the tautology.
    tp = n_forced
    marker = prf(tp, 0, 0)
    marker.update({"recall_ci": [1.0, 1.0], "precision_ci": [1.0, 1.0],
                   "f1_ci_bootstrap": [1.0, 1.0], "tautological": True})
    rules = {
        "R3_marker_replay_tautology": marker,
        "R1_artifacts_only_not_complete": rule_metrics("artifact_state", not_complete),
        "R1_artifacts_only_suspect_only": rule_metrics("artifact_state", {"suspect_undeclared"}),
        "R2_attested_not_complete": rule_metrics("attested_state", not_complete),
        "R2_attested_suspect_only": rule_metrics("attested_state", {"suspect_undeclared"}),
    }

    # Label-noise estimate: non-forced missions with zero implementation AND no checks.
    zero_artifact = [r for r in rows if not r["forced"] and r["artifact_state"] == "suspect_undeclared"]
    n_zero = len(zero_artifact)
    bound = n_forced / (n_forced + n_zero) if (n_forced + n_zero) else 0.0

    strata: dict[str, dict] = {}
    for r in rows:
        if not r["forced"]:
            continue
        key = r["fail_stage"] or "(empty)"
        entry = strata.setdefault(key, {"n": 0, "complete": 0, "partial": 0, "suspect": 0})
        entry["n"] += 1
        short = {"delivered_complete": "complete", "delivered_partial": "partial",
                 "suspect_undeclared": "suspect"}[r["artifact_state"]]
        entry[short] += 1

    confusion = {}
    for r in rows:
        confusion[(r["artifact_state"], r["attested_state"])] = confusion.get(
            (r["artifact_state"], r["attested_state"]), 0) + 1

    metrics = {
        "corpus": {
            "path": str(CORPUS.relative_to(ROOT)),
            "sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
            "n_succeeded": n,
            "n_forced_finish": n_forced,
            "forced_finish_share_pct": round(n_forced / n * 100, 2) if n else 0.0,
            "n_non_forced": n_non_forced,
        },
        "rules": rules,
        "predeclared_thresholds": {"recall_min": 0.85, "precision_min": 0.90},
        "threshold_verdicts": {
            name: {"recall_ok": m["recall"] >= 0.85, "precision_ok": m["precision"] >= 0.90}
            for name, m in rules.items()
        },
        "label_noise": {
            "n_non_forced_suspect_undeclared": n_zero,
            "precision_upper_bound": round(bound, 4),
            "note": "any rule catching all forced_finish cases plus these zero-artifact "
                    "missions scores precision <= this bound against the weak label",
        },
        "artifact_vs_attested_confusion": {f"{k[0]}->{k[1]}": v for k, v in sorted(confusion.items())},
        "state_distribution": {
            "artifact_only": {s: sum(1 for r in rows if r["artifact_state"] == s) for s in STATES},
            "attested": {s: sum(1 for r in rows if r["attested_state"] == s) for s in STATES},
        },
        "closure_coverage": {
            c: sum(1 for r in rows if r["closure"] == c)
            for c in ("clean", "forced", "unknown")
        },
        "forced_finish_strata": strata,
        "missing_items_top": compute_missing_item_counts(
            [assess_attested_delivery(_result(rec)) for rec in records]),
        "rows": rows,
    }
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["sample", "full"], default="full")
    args = ap.parse_args()
    records = load_corpus()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.stage == "sample":
        info = sample_stage(records)
        print(json.dumps(info, ensure_ascii=False))
        return
    metrics = full_stage(records)
    rows = metrics.pop("rows")
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in
                      ("corpus", "rules", "threshold_verdicts", "label_noise",
                       "state_distribution")}, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()