"""Bridge from a radar verdict to the arbitration de-dup gate.

The gate does not want an essay; it wants a machine-actionable answer plus the
citations that justify it:

``block_duplicate``  covered — the pool already has this; attach the citations
``narrow_claim``     partial — adjacent work exists; the proposal must say what
                     is new relative to the cited documents
``allow``            blank — no evidence in the pool; the zero-hit proof must
                     travel with the proposal so a reviewer can re-run it

The bridge is a pure function of the sealed envelope (no I/O, no clock), so the
gate's decision inherits the envelope's sha256: two gates that disagree must
disagree about a digest, which is a reviewable fact.
"""
from __future__ import annotations

from typing import Any, Dict

from .evidence import seal

GATE_SCHEMA = "radar-gate/v1"

_DECISION = {
    "covered": "block_duplicate",
    "generic_only": "narrow_claim",
    "partial": "narrow_claim",
    "blank": "allow",
}


def dedup_gate(verdict_envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Map a ``radar/v1`` envelope to a ``radar-gate/v1`` decision."""
    verdict = verdict_envelope.get("verdict")
    if verdict not in _DECISION:
        raise ValueError(f"not a radar verdict: {verdict!r}")
    # A blank verdict carries no citations on purpose: the BM25 hits are
    # generic-word neighbours, and shipping them as "citations" would let a
    # reviewer mistake lexical noise for prior art.
    citations = (
        []
        if verdict == "blank"
        else [
            {
                "doc_id": d.get("doc_id", ""),
                "ref": d.get("ref", ""),
                "score": d.get("score", 0.0),
            }
            for d in (verdict_envelope.get("hits") or {}).get("docs") or []
        ]
    )
    decision = _DECISION[verdict]
    payload = {
        "topic": verdict_envelope.get("topic", ""),
        "verdict": verdict,
        "decision": decision,
        "citations": citations,
        "evidence_sha256": (verdict_envelope.get("digest") or {}).get("sha256", ""),
        "pool_digest": verdict_envelope.get("pool_digest", ""),
        "action_required": {
            "block_duplicate": "cite the citations and close the proposal as duplicate",
            "narrow_claim": "state what is new relative to the cited documents",
            "allow": "attach zero_hit_proof verbatim; a reviewer must be able to re-run it",
        }[decision],
    }
    if verdict == "generic_only":
        # The refusal itself is the evidence: hand the reviewer the ledger of
        # probes that were found but not corroborated, plus the generic wording.
        ledger = verdict_envelope.get("generic_ledger") or {}
        payload["generic_ledger"] = {
            "witness_doc": ledger.get("witness_doc"),
            "generic_terms": [r["term"] for r in ledger.get("generic_terms") or []],
            "refused_probes": [
                {k: p.get(k) for k in ("probe", "df", "best_doc", "best_doc_score", "why")}
                for p in ledger.get("generic_probes") or []
            ],
        }
    if verdict == "blank":
        proof = verdict_envelope.get("zero_hit_proof") or {}
        payload["zero_hit_proof"] = {
            "rule": proof.get("rule", ""),
            "window": proof.get("window"),
            "stride": proof.get("stride"),
            "specific_probes": proof.get("specific_probes") or [],
            "existence_hits": proof.get("existence_hits", 0),
            "statement": proof.get("statement", ""),
        }
    return seal(payload, schema=GATE_SCHEMA)