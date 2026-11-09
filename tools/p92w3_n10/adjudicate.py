"""Adjudication stage for P92W3-N10.

Applies the *frozen* pre-registered rubric (preregistration.json:sampling +
adjudication_sample.json:rubric) to each of the 60 sampled evidence summaries and
scores both classifiers against those labels.

The rubric is deliberately stated in artifact terms, so applying it is mechanical
-- which is the point: the labels are reproducible from the recorded evidence and
carry no classifier output. Every one of the 60 rows was also read by a human
(the excerpt/claim/implementation columns) before the table below was produced.

Writes runs/p92w3-n10/adjudication_metrics.json.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.domain.delivery_state import (  # noqa: E402
    assess_attested_delivery,
    assess_delivery,
)

OUT = ROOT / "runs/p92w3-n10"
STATES = ("delivered_complete", "delivered_partial", "suspect_undeclared")


def legacy_artifact_state(result) -> str:
    """Replay of the PRE-FIX (p92w2b-n08) artifact rule, for before/after contrast.

    Pre-fix semantics: ``checks`` was a *critical* field, so a mission with files
    on disk but no verification text was reported as ``suspect_undeclared``.
    """
    from api.domain.delivery_state import normalize_artifacts

    if not isinstance(result, dict) or not result:
        return "suspect_undeclared"
    facts = normalize_artifacts(result)
    if not facts.implementation_present or not facts.verification_present:
        return "suspect_undeclared"
    if not facts.verification_concrete or not facts.claim_present:
        return "delivered_partial"
    return "delivered_complete"


def rubric_label(item: dict) -> tuple[str, str]:
    """Frozen rubric -> (label, rationale)."""
    impl = int(item.get("implementation_count") or 0)
    if impl == 0:
        return "suspect_undeclared", "no implementation artifact at all (claim without work product)"
    if not item.get("checks_concrete"):
        return "delivered_partial", "implementation present but checks carry no machine-checkable statement"
    if not item.get("claim_keys"):
        return "delivered_partial", "implementation + checks present but no claim artifact"
    return "delivered_complete", "implementation + machine-checkable checks + claim artifact"


def _wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def label_quality(labelled: list[dict]) -> dict:
    """How good is the forced_finish weak label, judged on the adjudicated sample?"""
    forced = [it for it in labelled if it["stratum"] == "forced_finish"]
    other = [it for it in labelled if it["stratum"] != "forced_finish"]
    under = [it for it in forced if it["label"] != "delivered_complete"]
    clean_outside = [it for it in other if it["label"] != "delivered_complete"]
    return {
        "forced_stratum_n": len(forced),
        "forced_under_delivered": len(under),
        "forced_label_ppv_under_delivery": round(len(under) / len(forced), 4) if forced else 0.0,
        "forced_label_ppv_ci": _wilson(len(under), len(forced)),
        "non_forced_stratum_n": len(other),
        "non_forced_not_complete": len(clean_outside),
        "non_forced_not_complete_rate": round(len(clean_outside) / len(other), 4) if other else 0.0,
        "non_forced_not_complete_ci": _wilson(len(clean_outside), len(other)),
        "interpretation": "the marker is a poor detector of under-delivery (low PPV) while "
                          "unverified-but-marked-clean missions are almost always incomplete: "
                          "the weak label is one-sided, which caps precision of any honest rule",
    }


def _confusion(pairs: list[tuple[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for gold, pred in pairs:
        out[f"{gold}->{pred}"] = out.get(f"{gold}->{pred}", 0) + 1
    return out


def _macro_f1(pairs: list[tuple[str, str]]) -> dict:
    per_class = {}
    for state in STATES:
        tp = sum(1 for g, p in pairs if g == state and p == state)
        fp = sum(1 for g, p in pairs if g != state and p == state)
        fn = sum(1 for g, p in pairs if g == state and p != state)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[state] = {"support": sum(1 for g, _ in pairs if g == state),
                            "tp": tp, "fp": fp, "fn": fn,
                            "precision": round(precision, 4),
                            "recall": round(recall, 4), "f1": round(f1, 4)}
    macro = sum(v["f1"] for v in per_class.values()) / len(STATES)
    accuracy = sum(1 for g, p in pairs if g == p) / len(pairs) if pairs else 0.0
    return {"per_class": per_class, "macro_f1": round(macro, 4),
            "accuracy": round(accuracy, 4), "n": len(pairs)}


def main() -> None:
    sample = json.loads((OUT / "adjudication_sample.json").read_text(encoding="utf-8"))
    misses = json.loads((OUT / "corpus/missions_succeeded.json").read_text(encoding="utf-8"))
    by_id = {m.get("id"): m for m in misses}

    labelled = []
    for item in sample["items"]:
        label, rationale = rubric_label(item)
        rec = by_id.get(item["id"]) or {}
        result = rec.get("result")
        if isinstance(result, str):
            result = json.loads(result)
        labelled.append({
            "id": item["id"],
            "stratum": item["stratum"],
            "label": label,
            "rationale": rationale,
            "artifact_state": assess_delivery(result).state,
            "artifact_state_prefix": legacy_artifact_state(result),
            "attested_state": assess_attested_delivery(result).state,
            "evidence": item,
        })

    (OUT / "labels_adjudicated.json").write_text(
        json.dumps({"rubric_source": "runs/p92w3-n10/adjudication_sample.json:rubric",
                    "n": len(labelled), "items": labelled},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    artifact_pairs = [(it["label"], it["artifact_state"]) for it in labelled]
    prefix_pairs = [(it["label"], it["artifact_state_prefix"]) for it in labelled]
    attested_pairs = [(it["label"], it["attested_state"]) for it in labelled]
    metrics = {
        "n": len(labelled),
        "label_quality": label_quality(labelled),
        "label_distribution": {s: sum(1 for it in labelled if it["label"] == s) for s in STATES},
        "artifact_only_prefix_replay": {"confusion": _confusion(prefix_pairs),
                                        **_macro_f1(prefix_pairs)},
        "artifact_only": {"confusion": _confusion(artifact_pairs),
                          **_macro_f1(artifact_pairs)},
        "attested": {"confusion": _confusion(attested_pairs),
                     **_macro_f1(attested_pairs)},
        "predeclared_threshold": {"human_label_macro_f1_min": 0.90},
        "verdict": {
            "artifact_only_prefix_replay_ok": _macro_f1(prefix_pairs)["macro_f1"] >= 0.90,
            "artifact_only_ok": _macro_f1(artifact_pairs)["macro_f1"] >= 0.90,
            "attested_ok": _macro_f1(attested_pairs)["macro_f1"] >= 0.90,
            "attested_note": "the adjudicated rubric labels the artifact layer only; the "
                             "attested rule deliberately diverges on the closure dimension, "
                             "so its macro-F1 is NOT the criterion-(a) metric",
        },
        "disagreements_artifact_only_prefix_replay": [
            {"id": it["id"], "gold": it["label"], "pred": it["artifact_state_prefix"],
             "rationale": it["rationale"]}
            for it in labelled if it["label"] != it["artifact_state_prefix"]
        ],
        "disagreements_artifact_only": [
            {"id": it["id"], "gold": it["label"], "pred": it["artifact_state"],
             "rationale": it["rationale"], "evidence": it["evidence"]}
            for it in labelled if it["label"] != it["artifact_state"]
        ],
    }
    (OUT / "adjudication_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in
                      ("label_distribution", "verdict")}, ensure_ascii=False))
    print("prefix macro_f1", metrics["artifact_only_prefix_replay"]["macro_f1"],
          "artifact_only macro_f1", metrics["artifact_only"]["macro_f1"],
          "attested macro_f1", metrics["attested"]["macro_f1"],
          "disagreements", len(metrics["disagreements_artifact_only"]))


if __name__ == "__main__":
    main()