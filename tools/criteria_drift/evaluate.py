"""Measurement harness: labelled corpora -> accuracy / false-positive rate.

Every number in the mission report comes from this module; nothing is typed by
hand.  Two views are reported because they answer different questions:

* **pair level** - per proposal criterion: did the auditor flag exactly the
  criteria whose bar was narrowed?  This is where a detector can look good for
  the wrong reason (e.g. flagging everything).
* **document level** - per node acceptance report: does the gate block exactly
  the reports that contain at least one silent narrowing?  This is what the DAG
  gate consumes.

Ablations (each removes one mechanism) are measured on the same inputs so the
claim "the mechanism earns its keep" is a measured delta, not an opinion.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import compare, corpus_synth, matching, model, real_corpus

_IDX = re.compile(r"\[(\d+)\]")


def _row_proposal_index(row: model.CriterionDrift) -> Optional[int]:
    if row.proposal is None:
        return None
    m = _IDX.search(row.proposal.locator or "")
    return int(m.group(1)) if m else None


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def add(self, label: bool, pred: bool) -> None:
        if label and pred:
            self.tp += 1
        elif label and not pred:
            self.fn += 1
        elif not label and pred:
            self.fp += 1
        else:
            self.tn += 1

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def fpr(self) -> float:
        d = self.fp + self.tn
        return self.fp / d if d else 0.0

    def as_dict(self, prefix: str = "") -> Dict[str, Any]:
        lo, hi = wilson(self.tp + self.tn, self.n)
        return {
            f"{prefix}tp": self.tp, f"{prefix}fp": self.fp,
            f"{prefix}tn": self.tn, f"{prefix}fn": self.fn,
            f"{prefix}n": self.n,
            f"{prefix}accuracy": round(self.accuracy, 6),
            f"{prefix}accuracy_ci95": [round(lo, 6), round(hi, 6)],
            f"{prefix}precision": round(self.precision, 6),
            f"{prefix}recall": round(self.recall, 6),
            f"{prefix}f1": round(self.f1, 6),
            f"{prefix}false_positive_rate": round(self.fpr, 6),
        }


VARIANTS: Dict[str, Dict[str, Any]] = {
    "full": {},
    "no_synonyms": {"match": {"use_synonyms": False}},
    "no_value_gate": {"match": {"accept_equal_value_with_same_kind": False}},
    "no_waivers": {"recognize_waivers": False},
    "no_op_rule": {"operator_downgrade_significant": False},
    "no_family_fallback": {"match": {"enable_family_fallback": False}},
}


def variant_policy(base: Dict[str, Any], name: str) -> Tuple[Dict[str, Any], bool]:
    import copy
    pol = copy.deepcopy(base)
    spec = VARIANTS[name]
    for k, v in spec.get("match", {}).items():
        pol.setdefault("match", {})[k] = v
    for k, v in spec.items():
        if k != "match":
            pol[k] = v
    return pol, bool(spec.get("recognize_waivers", True))


def evaluate_synthetic(docs: Sequence[corpus_synth.DocCase],
                       policy: Optional[Dict[str, Any]] = None,
                       recognize_waivers: bool = True) -> Dict[str, Any]:
    pol = policy or matching.load_policy()
    pair_c, doc_c = Confusion(), Confusion()
    per_kind: Dict[str, Confusion] = {}
    rows_out: List[Dict[str, Any]] = []
    for doc in docs:
        prop, rep = corpus_synth.to_documents(doc)
        audit = compare.audit(prop, rep, proposal_ref=doc.doc_id, report_ref=doc.doc_id + "/report",
                              policy=pol, recognize_waivers=recognize_waivers)
        pred_by_idx: Dict[int, bool] = {}
        for row in audit.rows:
            i = _row_proposal_index(row)
            if i is None:
                continue
            pred_by_idx[i] = pred_by_idx.get(i, False) or (row.status == "significant")
        for case in doc.criteria:
            pred = pred_by_idx.get(case.index, False)
            label = case.label_drift
            pair_c.add(label, pred)
            per_kind.setdefault(case.kind, Confusion()).add(label, pred)
            rows_out.append({
                "doc_id": doc.doc_id, "index": case.index, "kind": case.kind,
                "mutation": case.mutation, "label": label, "pred": pred,
                "report_text": case.report_text, "proposal_text": case.proposal_text,
                "label_reason": case.label_reason,
            })
        doc_c.add(doc.doc_label, audit.block)
    out = {"n_docs": len(docs), "n_criteria": pair_c.n,
           "pair": pair_c.as_dict("pair_"), "document": doc_c.as_dict("doc_")}
    out["per_kind"] = {k: c.as_dict() for k, c in sorted(per_kind.items())}
    out["worst_cases"] = [r for r in rows_out if r["label"] != r["pred"]][:12]
    out["rows"] = rows_out
    return out


def evaluate_real(cases: Sequence[real_corpus.RealCase],
                  policy: Optional[Dict[str, Any]] = None,
                  recognize_waivers: bool = True) -> Dict[str, Any]:
    pol = policy or matching.load_policy()
    overall = Confusion()
    per_family: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        prop = {"acceptance_criteria": list(case.proposal)}
        rep = {"acceptance_criteria": list(case.report)}
        if case.waivers:
            rep["waivers"] = list(case.waivers)
        audit = compare.audit(prop, rep, proposal_ref=case.case_id, report_ref=case.case_id + "/report",
                              policy=pol, recognize_waivers=recognize_waivers)
        pred = audit.block
        overall.add(case.label_drift, pred)
        fam = per_family.setdefault(case.family, {
            "n": 0, "n_positive": 0, "detected": 0, "n_negative": 0, "flagged_negative": 0,
            "max_relaxation": [], "score": [],
        })
        fam["n"] += 1
        if case.label_drift:
            fam["n_positive"] += 1
            fam["detected"] += int(pred)
        else:
            fam["n_negative"] += 1
            fam["flagged_negative"] += int(pred)
        fam["max_relaxation"].append(audit.max_relaxation)
        fam["score"].append(audit.drift_score)
    for fam in per_family.values():
        fam["detection_rate"] = round(fam["detected"] / fam["n_positive"], 6) if fam["n_positive"] else None
        fam["false_flag_rate"] = round(fam["flagged_negative"] / fam["n_negative"], 6) if fam["n_negative"] else None
        fam["max_relaxation"] = round(max(fam["max_relaxation"] or [0.0]), 6)
        fam["mean_score"] = round(sum(fam["score"]) / len(fam["score"]), 6) if fam["score"] else 0.0
        del fam["score"]
    return {"n_cases": len(cases), "overall": overall.as_dict(""), "per_family": per_family}


def evaluate_all(n_docs: int = 240, seed: int = 20261111,
                 repo_root=None) -> Dict[str, Any]:
    from pathlib import Path
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    base_policy = matching.load_policy()
    docs = corpus_synth.generate_corpus(n_docs=n_docs, seed=seed)
    real = real_corpus.harvest(root)
    cases = real_corpus.build_cases(real)

    result: Dict[str, Any] = {
        "synthetic": {"n_docs": n_docs, "seed": seed},
        "real": {"n_criteria_harvested": len(real), "n_cases": len(cases),
                 "sources": sorted({f"{r.campaign}/{r.node}" for r in real})[:8],
                 "n_sources": len({f"{r.campaign}/{r.node}" for r in real})},
        "variants": {},
    }
    for name in VARIANTS:
        pol, rec_waivers = variant_policy(base_policy, name)
        syn = evaluate_synthetic(docs, pol, rec_waivers)
        rl = evaluate_real(cases, pol, rec_waivers)
        result["variants"][name] = {
            "synthetic_pair_accuracy": syn["pair"]["pair_accuracy"],
            "synthetic_pair_fpr": syn["pair"]["pair_false_positive_rate"],
            "synthetic_doc_accuracy": syn["document"]["doc_accuracy"],
            "synthetic_doc_fpr": syn["document"]["doc_false_positive_rate"],
            "synthetic_pair_f1": syn["pair"]["pair_f1"],
            "real_accuracy": rl["overall"]["accuracy"],
            "real_fpr": rl["overall"]["false_positive_rate"],
            "real_f1": rl["overall"]["f1"],
        }
        if name == "full":
            result["synthetic_full"] = syn
            result["real_full"] = rl
    return result