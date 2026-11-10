"""Cross-validation: machine verdict vs human-authored verdict, with provenance.

Why this module matters: an engine that only reads the *human's own status
field* and echoes it is worthless.  Here the machine verdict is derived from
numbers inside the artifacts (errors, counts, flags, tolerances) while the human
verdict is read from the status tokens humans wrote (``verdict``, ``passed``,
``overall_status``, ``all_passed``).  The two paths share a document but not a
computation, so a disagreement is a real signal: either the prose over-claims or
the numbers no longer support the claim.

Every comparison row carries a source pointer on both sides - that is the
"定源表" (origin table) required by the node contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .criteria import load_lexicon
from .model import (
    NodeEvidence,
    NodeVerdict,
    Verdict,
    classify_human_token,
)

NODE_STATUS_KEYS = ("overall_status", "overall", "verdict", "status", "result", "all_passed", "all_pass", "passed")
ITEM_STATUS_KEYS = ("verdict", "status", "passed", "pass", "ok", "result")


@dataclass(frozen=True)
class HumanClaim:
    scope: str
    verdict: Verdict
    token: Any
    source: str
    pointer: str

    def as_dict(self) -> dict:
        return {"scope": self.scope, "verdict": self.verdict.value, "token": _safe(self.token),
                "source": self.source, "pointer": self.pointer}


@dataclass
class NodeCrossCheck:
    node_id: str
    machine_verdict: Verdict
    human_verdict: Verdict
    agreement: str            # agree | disagree | machine_undetermined | incomparable
    human_source: str = ""
    human_pointer: str = ""
    rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "machine_verdict": self.machine_verdict.value,
            "human_verdict": self.human_verdict.value,
            "agreement": self.agreement,
            "human_source": self.human_source,
            "human_pointer": self.human_pointer,
            "rows": self.rows,
            "notes": self.notes,
        }


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:120]


# ---------------------------------------------------------------------------
# Human claim extraction
# ---------------------------------------------------------------------------

def extract_human_claims(evidence: NodeEvidence) -> tuple[list[HumanClaim], HumanClaim | None]:
    """Return (per-item claims, node-level claim) authored by humans."""
    from .scanner import role_of

    lex = load_lexicon()
    priority = {r: i for i, r in enumerate(lex.get("human_claim_role_priority", []))}
    primary = tuple(lex.get("criteria_role_allowlist", ("validation_report", "criteria")))
    docs = sorted(evidence.documents, key=lambda d: priority.get(role_of(evidence, d[0]), 99))
    tiers = [[d for d in docs if role_of(evidence, d[0]) in primary],
             [d for d in docs if role_of(evidence, d[0]) not in primary]]
    for tier in tiers:
        items, node_claim = _claims_from_docs(tier)
        if items or node_claim:
            break
    else:
        items, node_claim = [], None

    return items, node_claim


def _claims_from_docs(docs) -> tuple[list[HumanClaim], HumanClaim | None]:
    items: list[HumanClaim] = []
    node_claim: HumanClaim | None = None
    for source, doc in docs:
        if not isinstance(doc, dict):
            continue
        for key in NODE_STATUS_KEYS:
            if key in doc and not isinstance(doc[key], (list, dict)):
                v = classify_human_token(doc[key])
                if v is not Verdict.UNKNOWN:
                    node_claim = node_claim or HumanClaim(scope="", verdict=v, token=doc[key],
                                                          source=source, pointer=key)
                break
        for list_key in ("criteria", "anchors", "checks"):
            arr = doc.get(list_key)
            if isinstance(arr, list):
                for i, rec in enumerate(arr):
                    if not isinstance(rec, dict):
                        continue
                    for k in ITEM_STATUS_KEYS:
                        if k in rec and not isinstance(rec[k], (list, dict)):
                            v = classify_human_token(rec[k])
                            if v is not Verdict.UNKNOWN:
                                items.append(HumanClaim(scope=f"{source}::{list_key}[{i}]", verdict=v, token=rec[k],
                                                        source=source, pointer=f"{list_key}[{i}].{k}"))
                            break
            elif isinstance(arr, dict):
                for name, rec in arr.items():
                    if not isinstance(rec, dict):
                        continue
                    for k in ITEM_STATUS_KEYS:
                        if k in rec and not isinstance(rec[k], (list, dict)):
                            v = classify_human_token(rec[k])
                            if v is not Verdict.UNKNOWN:
                                items.append(HumanClaim(scope=f"{source}::{list_key}.{name}", verdict=v, token=rec[k],
                                                        source=source, pointer=f"{list_key}.{name}.{k}"))
                            break

    if node_claim is None and items:
        pos = sum(1 for c in items if c.verdict is Verdict.PASS)
        neg = sum(1 for c in items if c.verdict is Verdict.FAIL)
        if pos + neg:
            v = Verdict.FAIL if neg else Verdict.PASS
            node_claim = HumanClaim(scope="", verdict=v, token=f"{pos} pass / {neg} fail (derived)",
                                    source=items[0].source, pointer="derived_from_item_claims")
    return items, node_claim


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def cross_validate(verdict: NodeVerdict, evidence: NodeEvidence | None = None) -> NodeCrossCheck:
    ev = evidence or verdict.evidence
    if ev is None:
        return NodeCrossCheck(verdict.node_id, verdict.verdict, Verdict.UNKNOWN, "incomparable",
                              notes=["no evidence attached"])
    items, node_claim = extract_human_claims(ev)
    human_verdict = node_claim.verdict if node_claim else Verdict.UNKNOWN

    by_scope = {c.scope: c for c in items}
    rows: list[dict] = []
    used: set[str] = set()
    for o in verdict.outcomes:
        c = by_scope.get(o.criterion.scope)
        if c is not None:
            used.add(c.scope)
        rows.append(_row(o.criterion.raw, o.criterion.scope, o.verdict, o.observation.value,
                         o.observation.source, ", ".join(o.observation.matched_keys[:2]),
                         c.verdict if c else Verdict.UNKNOWN,
                         _safe(c.token) if c else None, c.pointer if c else None,
                         c.source if c else None))
    unmatched = [c for c in items if c.scope not in used]
    for c in unmatched:
        rows.append(_row(f"(no machine criterion for {c.scope})", c.scope, Verdict.UNKNOWN, None, "",
                         "", c.verdict, _safe(c.token), c.pointer, c.source))

    has_fail = any(o.verdict is Verdict.FAIL for o in verdict.outcomes)
    has_conflict = any(o.verdict is Verdict.MECHANICAL_CONFLICT for o in verdict.outcomes)
    agreement = _agreement(verdict.verdict, human_verdict, has_fail, has_conflict)
    notes: list[str] = []
    if human_verdict is Verdict.UNKNOWN:
        notes.append("no human-authored status token found; node cannot be cross-validated")
    if agreement == "documented_deviation":
        notes.append("at least one declared number is violated while the record documents the deviation: "
                     "curation required (not counted as agreement or disagreement)")
    if agreement == "agree_no_contradiction":
        notes.append("machine found no contradicting criterion (all judgeable criteria PASS) but "
                     f"{sum(1 for o in verdict.outcomes if o.verdict is Verdict.UNKNOWN)} criterion/criteria stay "
                     "UNKNOWN: recorded as no-contradiction, not as certified agreement")
    elif agreement == "undetermined":
        notes.append("machine had no criterion to judge: undetermined")

    return NodeCrossCheck(
        node_id=verdict.node_id,
        machine_verdict=verdict.verdict,
        human_verdict=human_verdict,
        agreement=agreement,
        human_source=node_claim.source if node_claim else "",
        human_pointer=node_claim.pointer if node_claim else "",
        rows=rows,
        notes=notes,
    )


def _row(item: str, scope: str, machine: Verdict, observed: Any, machine_source: str, machine_pointer: str,
         human: Verdict, human_token: Any, human_pointer: str | None, human_source: str | None) -> dict:
    return {
        "item": item,
        "scope": scope,
        "machine_verdict": machine.value,
        "machine_observed": observed,
        "machine_source": machine_source,
        "machine_pointer": machine_pointer,
        "human_verdict": human.value,
        "human_token": human_token,
        "human_source": human_source or "",
        "human_pointer": human_pointer or "",
        "relation": _relation(machine, human),
    }


def _relation(machine: Verdict, human: Verdict) -> str:
    if human is Verdict.UNKNOWN:
        return "no_human_claim"
    if machine is Verdict.MECHANICAL_CONFLICT:
        return "documented_deviation"
    if machine is Verdict.UNKNOWN or machine in (Verdict.INCOMPLETE, Verdict.NO_CRITERIA):
        return "machine_undetermined"
    return "agree" if machine is human else "disagree"


AGREEMENT_CLASSES = ("agree_certified", "agree_no_contradiction", "disagree", "undetermined", "incomparable")


def _agreement(machine: Verdict, human: Verdict, has_fail: bool = False, has_conflict: bool = False) -> str:
    """Classify machine-vs-human agreement (four honest tiers).

    * ``agree_certified``        machine certified the same binary verdict
    * ``agree_no_contradiction`` machine could not certify (INCOMPLETE, i.e.
      some criteria unjudgeable) but found no FAIL; it never contradicts the
      human claim, yet it does not confirm it either
    * ``disagree``               machine found contradicting evidence
    * ``undetermined``           machine had nothing to judge (NO_CRITERIA)
    * ``incomparable``           no human-authored verdict at all

    The tier split matters: reporting only the strict tier would hide that most
    verdicts are "not contradicted but not certified", and merging the tiers
    would launder missing evidence into a PASS.
    """
    if human is Verdict.UNKNOWN:
        return "incomparable"
    if machine is Verdict.NO_CRITERIA:
        return "undetermined"
    if machine in (Verdict.PASS, Verdict.FAIL):
        return "agree_certified" if machine is human else "disagree"
    # INCOMPLETE
    if has_fail:
        return "disagree"
    if has_conflict:
        # the artifact's own numbers violate a declared threshold, while the
        # record's prose/flag records the deviation as accepted: this needs a
        # curator, so it is neither agreement nor disagreement
        return "documented_deviation"
    return "agree_no_contradiction"


# ---------------------------------------------------------------------------
# Fleet-level summary
# ---------------------------------------------------------------------------

def summarise(checks: Sequence[NodeCrossCheck]) -> dict:
    """Agreement statistics over many nodes.

    ``agreement_strict`` counts machine_undetermined as NOT agreeing (the honest
    denominator: an undecided machine did not confirm the human report).
    ``agreement_comparable`` restricted to nodes where both sides are decided.
    """
    total = len(checks)
    certified = [c for c in checks if c.agreement == "agree_certified"]
    deviation = [c for c in checks if c.agreement == "documented_deviation"]
    nocontra = [c for c in checks if c.agreement == "agree_no_contradiction"]
    disagree = [c for c in checks if c.agreement == "disagree"]
    undet = [c for c in checks if c.agreement == "undetermined"]
    incomparable = [c for c in checks if c.agreement == "incomparable"]
    decided = len(certified) + len(disagree) + len(nocontra) + len(deviation)
    # "consistent" = the machine never asserted the opposite of the human claim.
    # certified: it reproduced it; no_contradiction: it could not certify but found
    # nothing against it; documented_deviation: a declared number is violated while
    # the record itself documents the deviation (curation, not contradiction).
    consistent = len(certified) + len(nocontra) + len(deviation)
    return {
        "nodes": total,
        "agree_certified": len(certified),
        "agree_no_contradiction": len(nocontra),
        "consistency": consistent,
        "documented_deviation": len(deviation),
        "disagree": len(disagree),
        "undetermined": len(undet),
        "incomparable": len(incomparable),
        "agreement_strict": round(len(certified) / total, 4) if total else None,
        "consistency_rate": round(consistent / total, 4) if total else None,
        "agreement_decided": round(len(certified) / decided, 4) if decided else None,
        "certification_rate": round(len(certified) / total, 4) if total else None,
        "deviation_nodes": [c.node_id for c in deviation],
        "mismatch_nodes": [
            {
                "node_id": c.node_id,
                "machine": c.machine_verdict.value,
                "human": c.human_verdict.value,
                "relation": c.agreement,
                "human_source": f"{c.human_source}#{c.human_pointer}",
                "disagreeing_items": [r for r in c.rows if r["relation"] == "disagree"][:5],
            }
            for c in checks if c.agreement in ("disagree", "undetermined", "agree_no_contradiction",
                                               "documented_deviation")
        ],
    }