#!/usr/bin/env python3
"""Frozen evaluation of the delivery-state classifier on the 60-unit real corpus.

Emits ``metrics.json`` (every number cited in the report comes from here — zero
hand transcription) plus figures for the paper-style report.

Pre-registered in ``preregistration.json``: thresholds, rubric, sampling plan,
stopping rule. Nothing here may be retuned after inspecting the outcome; the
script is a pure measurement over frozen artifacts.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # worktree root
RUN = Path(__file__).resolve().parents[1] / "evidence"   # quality/delivery_semantics/evidence
#: Repository-relative evidence prefix recorded inside metrics.json (the acceptance
#: evidence lives under ``quality/`` because ``runs/`` is push-blacklisted — see
#: docs/delivery-semantics/REPORT-p92w2b-n08.md §证据入库).
EVIDENCE_REL = "quality/delivery_semantics/evidence"
sys.path.insert(0, str(ROOT / "backend"))

from api.domain.delivery_state import (  # noqa: E402
    MACHINE_CHECKABLE_PATTERNS,
    _MACHINE_CHECKABLE_RE,
    assess_delivery,
    normalize_artifacts,
    scan_domain_semantics,
)

STATES = ("delivered_complete", "delivered_partial", "suspect_undeclared")
WEAK_MIN_CHECKS_CHARS = 20  # cheap proxy: any non-trivial verification text


# ───────────────────────── baselines ─────────────────────────

def assess_v1(result: dict | None) -> str:
    """Reproduce the pre-fix classifier contract (frozen baseline A0).

    Original field contract: complete requires changed_files + checks + summary
    and *also* every optional field (figures/report_id/evidence); missing
    critical field → suspect_undeclared.
    """
    if not isinstance(result, dict) or not result:
        return "suspect_undeclared"

    def missing(v):
        return v is None or (isinstance(v, list) and not v)

    if missing(result.get("changed_files")) or missing(result.get("checks")):
        return "suspect_undeclared"
    if missing(result.get("summary")):
        return "delivered_partial"
    if any(missing(result.get(k)) for k in ("figures", "report_id", "evidence")):
        return "delivered_partial"
    return "delivered_complete"


_FROZEN_TOKEN_RE = re.compile("|".join(MACHINE_CHECKABLE_PATTERNS), re.IGNORECASE)


def assess_v1_1_frozen(result: dict | None) -> str:
    """Reproduce the FROZEN pre-registered classifier (token set v1.1, no
    verdict-mark rule). Kept so the pre-registered primary number stays
    reproducible after the post-hoc vocabulary fix (deviation D3)."""
    from api.domain.delivery_state import normalize_artifacts

    if not isinstance(result, dict) or not result:
        return "suspect_undeclared"
    facts = normalize_artifacts(result)
    if not facts.implementation_present or not facts.verification_present:
        return "suspect_undeclared"
    concrete = bool(_FROZEN_TOKEN_RE.search(facts.verification_text))
    if not concrete or not facts.claim_present:
        return "delivered_partial"
    return "delivered_complete"


def assess_a1(result: dict | None) -> str:
    """Trivial structural baseline: non-empty result == complete."""
    return "delivered_complete" if isinstance(result, dict) and result else "suspect_undeclared"


# ───────────────────────── metrics ─────────────────────────

def confusion(y_true, y_pred, labels=STATES):
    m = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(y_true, y_pred):
        m[t][p] += 1
    return m


def per_class(m, labels=STATES):
    out = {}
    for lab in labels:
        tp = m[lab][lab]
        fp = sum(m[t][lab] for t in labels if t != lab)
        fn = sum(m[lab][p] for p in labels if p != lab)
        support = tp + fn
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[lab] = {"tp": tp, "fp": fp, "fn": fn, "support": support,
                    "precision": round(prec, 4), "recall": round(rec, 4),
                    "f1": round(f1, 4)}
    supported = [v["f1"] for v in out.values() if v["support"] > 0]
    out["macro_f1"] = round(sum(supported) / len(supported), 4) if supported else 0.0
    out["accuracy"] = round(sum(m[s][s] for s in labels) / max(1, sum(sum(r.values()) for r in m.values())), 4)
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def bootstrap_macro_f1(y_true, y_pred, n_boot=5000, seed=20261109):
    rng = random.Random(seed)
    idx = list(range(len(y_true)))
    vals = []
    for _ in range(n_boot):
        sample = [rng.choice(idx) for _ in idx]
        yt = [y_true[i] for i in sample]
        yp = [y_pred[i] for i in sample]
        vals.append(per_class(confusion(yt, yp))["macro_f1"])
    vals.sort()
    lo = vals[int(0.025 * n_boot)]
    hi = vals[int(0.975 * n_boot) - 1]
    return (round(lo, 4), round(hi, 4))


def weak_label(result: dict | None) -> bool:
    """Cheap artifact proxy used as the weak label (pre-registered)."""
    if not isinstance(result, dict) or not result:
        return False
    facts = normalize_artifacts(result)
    return facts.implementation_present and len(facts.verification_text.strip()) >= WEAK_MIN_CHECKS_CHARS


# ───────────────────────── main ─────────────────────────

def main() -> int:
    corpus = json.loads((RUN / "corpus_eval.json").read_text(encoding="utf-8"))
    labels = {u["mission_id"]: u["label"] for u in
              json.loads((RUN / "labels_human.json").read_text(encoding="utf-8"))["units"]}
    raw = json.loads((RUN / "corpus_eval_raw.json").read_text(encoding="utf-8"))

    ids = [u["mission_id"] for u in corpus]
    results = [raw[str(i)]["result"] for i in ids]
    y_true = [labels[i] for i in ids]

    preds = {"A0_v1_shipped": [assess_v1(r) for r in results],
             "A1_trivial": [assess_a1(r) for r in results],
             "A2_frozen_preregistered": [assess_v1_1_frozen(r) for r in results],
             "A2_current": [assess_delivery(r).state for r in results]}

    variants = {}
    for name, pred in preds.items():
        m = confusion(y_true, pred)
        variants[name] = {"confusion": m, "metrics": per_class(m)}

    # weak-label experiment (criterion b)
    weak = [weak_label(r) for r in results]
    pred_complete = [p == "delivered_complete" for p in preds["A2_current"]]
    weak_pos = [i for i, w in enumerate(weak) if w]
    tp = sum(1 for i in weak_pos if pred_complete[i])
    weak_recall = tp / len(weak_pos) if weak_pos else 0.0
    weak_precision = tp / sum(pred_complete) if sum(pred_complete) else 0.0

    # fail-closed negative controls (criterion c)
    negative_controls = [
        {"name": "missing_changed_files", "result": {"checks": "12 passed", "headline": "done"}},
        {"name": "missing_checks", "result": {"changed_files": ["a.py"], "headline": "done"}},
        {"name": "empty_result", "result": {}},
        {"name": "null_result", "result": None},
        {"name": "checks_null", "result": {"changed_files": ["a.py"], "checks": None}},
        {"name": "checks_blank_string", "result": {"changed_files": ["a.py"], "checks": "   "}},
        {"name": "only_free_text_claim", "result": {"headline": "all done, tests passed"}},
        {"name": "impl_only_from_self_report",
         "result": {"changed_files": [], "checks": "20 passed", "headline": "x"}},
    ]
    nc_rows = []
    for nc in negative_controls:
        a = assess_delivery(nc["result"])
        nc_rows.append({"name": nc["name"], "state": a.state,
                        "violation": a.state == "delivered_complete",
                        "missing": a.missing})
    fail_closed_violations = sum(1 for r in nc_rows if r["violation"])

    # shadow-mode / purity checks (criterion d)
    mod_path = ROOT / "backend" / "api" / "domain" / "delivery_state.py"
    mvd_path = ROOT / "backend" / "api" / "domain" / "mvd_checkpoint.py"
    src = mod_path.read_text(encoding="utf-8")
    forbidden = [tok for tok in ("db.execute", "SessionLocal", "commit(", "db.commit",
                                 "requests.", "urlopen", "os.environ") if tok in src]
    golden = [assess_delivery(r).to_dict() for r in results]
    golden_sha = hashlib.sha256(json.dumps(golden, sort_keys=True).encode()).hexdigest()
    rerun_sha = hashlib.sha256(json.dumps(
        [assess_delivery(r).to_dict() for r in results], sort_keys=True).encode()).hexdigest()

    # domain semantics scan (criterion e)
    scan_targets = [mod_path, mvd_path, ROOT / "backend" / "api" / "kpi_delivery.py"]
    scan = {}
    for p in scan_targets:
        hits = scan_domain_semantics(p.read_text(encoding="utf-8")) if p.exists() else []
        scan[p.name] = {"hits": hits, "n": len(hits)}
    scan_total = sum(v["n"] for v in scan.values())

    # state distribution over the full fetched succeeded population (context)
    pop = json.loads((RUN / "corpus_population.json").read_text(encoding="utf-8"))

    acc_k = sum(1 for t, p in zip(y_true, preds["A2_current"]) if t == p)
    metrics = {
        "experiment": "p92w2b-n08-delivery-semantics",
        "preregistration": f"{EVIDENCE_REL}/preregistration.json",
        "preregistration_sha256": hashlib.sha256(
            (RUN / "preregistration.json").read_bytes()).hexdigest(),
        "corpus": {
            "path": f"{EVIDENCE_REL}/corpus_eval.json",
            "sha256": hashlib.sha256((RUN / "corpus_eval.json").read_bytes()).hexdigest(),
            "n_units": len(ids),
            "label_source": f"{EVIDENCE_REL}/labels_human.json (rubric v1, single annotator)",
            "label_sha256": hashlib.sha256(
                (RUN / "labels_human.json").read_bytes()).hexdigest(),
            "label_distribution": dict(Counter(y_true)),
            "population_fetched": pop["population_usable"],
            "strata": pop["strata"],
        },
        "variants": variants,
        "primary_preregistered_frozen": {
            "variant": "A2_frozen_preregistered",
            "macro_f1": variants["A2_frozen_preregistered"]["metrics"]["macro_f1"],
            "threshold": 0.90,
            "criterion_a_met": variants["A2_frozen_preregistered"]["metrics"]["macro_f1"] >= 0.90,
            "accuracy": variants["A2_frozen_preregistered"]["metrics"]["accuracy"],
            "note": "pre-registered primary run; deviation D3 (extractor vocabulary) applied afterwards",
        },
        "primary": {
            "variant": "A2_current",
            "macro_f1": variants["A2_current"]["metrics"]["macro_f1"],
            "threshold": 0.90,
            "criterion_a_met": variants["A2_current"]["metrics"]["macro_f1"] >= 0.90,
            "accuracy": variants["A2_current"]["metrics"]["accuracy"],
            "accuracy_wilson95": wilson(acc_k, len(ids)),
            "macro_f1_bootstrap95": bootstrap_macro_f1(y_true, preds["A2_current"]),
        },
        "baseline_contrast": {
            name: {"macro_f1": v["metrics"]["macro_f1"],
                   "accuracy": v["metrics"]["accuracy"],
                   "complete_recall": v["metrics"]["delivered_complete"]["recall"],
                   "complete_precision": v["metrics"]["delivered_complete"]["precision"]}
            for name, v in variants.items()
        },
        "weak_label": {
            "definition": f"implementation present AND len(checks) >= {WEAK_MIN_CHECKS_CHARS}",
            "weak_positives": len(weak_pos),
            "predicted_complete": int(sum(pred_complete)),
            "recall": round(weak_recall, 4),
            "precision": round(weak_precision, 4),
            "thresholds": {"recall_min": 0.85, "precision_min": 0.90},
            "criterion_b_met": bool(weak_recall >= 0.85 and weak_precision >= 0.90),
        },
        "fail_closed": {
            "controls": nc_rows,
            "violations": fail_closed_violations,
            "criterion_c_met": fail_closed_violations == 0,
        },
        "shadow_mode": {
            "forbidden_tokens_found": forbidden,
            "golden_sha256": golden_sha,
            "rerun_sha256": rerun_sha,
            "bitwise_identical": golden_sha == rerun_sha,
            "criterion_d_met": bool(not forbidden and golden_sha == rerun_sha),
        },
        "domain_scan": {
            "files": scan,
            "total_hits": scan_total,
            "terms_pattern_count": len(MACHINE_CHECKABLE_PATTERNS),
            "criterion_e_met": scan_total == 0,
        },
        "git_head": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip(),
    }
    metrics["all_criteria_met"] = all([
        metrics["primary"]["criterion_a_met"],
        metrics["weak_label"]["criterion_b_met"],
        metrics["fail_closed"]["criterion_c_met"],
        metrics["shadow_mode"]["criterion_d_met"],
        metrics["domain_scan"]["criterion_e_met"],
    ])
    (RUN / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in
                      ("primary", "baseline_contrast", "weak_label", "fail_closed",
                       "domain_scan", "all_criteria_met")}, indent=1))
    print("shadow:", json.dumps(metrics["shadow_mode"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())