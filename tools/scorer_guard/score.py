"""Tournament scorer core with fail-closed Goodhart gates (IN25-T5).

Score model (``linear_cliff_v1``), deliberately analytic so that every limit has
a closed form that a red-team case can pin to machine precision:

    rel_err_i = |x_i - a_i| / max(|a_i|, tiny)
    s_i       = 100 * clip(1 - rel_err_i / e_max_i, 0, 1)
    score     = w * mean_i(s_i) + (1 - w) * min_i(s_i),   w = 0.5

Limits: exact anchor -> 100 exactly; rel_err = e_max/2 -> 50 exactly;
rel_err >= e_max -> 0 exactly.  Because the mean half of the aggregate can be
bought by acing easy anchors, the ``min`` half caps Goodhart-style cherry
picking, and the gates below cap the degenerate submissions outright.

Gates (any trip => REJECTED with score 0, reasons recorded):

LOCK              scorer fingerprint must equal the pre-registered lock
FINITE            every submitted value must be a finite float
KNOWN_ANCHOR      no unknown anchor ids
ANCHOR_VERIFIED   no scoring against quarantined / unsourced anchors
ENVELOPE          reported regime must lie inside the anchor envelope
REGIME            reported regime must be the anchor's regime (no regime slop)
DEGENERATE        >= 3 anchors with one constant output = degenerate filler
ANCHOR_LEAK       bit-exact match to >= 2 anchors = memorised gold values
UNIT_DECADE       off by >= 2 decades on one anchor = unit confusion
PHYSICS           submitted physics claims must satisfy the invariants
MISSING           full anchor coverage required
REPLAY            byte-identical resubmission is refused
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import physics

SCORER_ID = "ship_perf_tournament_scorer"
SCORER_VERSION = "1.0.0"
SCORE_FORM = "linear_cliff_v1"
E_MAX_DEFAULT = 0.10
MEAN_WEIGHT = 0.5
SCORE_SCALE = 100.0

UNIT_DECADE_TOL = 2.0
LEAK_MIN_HITS = 2
CONST_SPREAD_TOL = 1e-12
CONST_ANCHOR_SEP = 1e-2
REGIME_TOL = 1e-2
TINY = 1e-300

GATES = (
    "LOCK", "FINITE", "KNOWN_ANCHOR", "ANCHOR_VERIFIED", "ENVELOPE", "REGIME",
    "DEGENERATE", "ANCHOR_LEAK", "UNIT_DECADE", "PHYSICS", "MISSING", "REPLAY",
)


def canonical_json(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def scorer_spec() -> Dict[str, Any]:
    """The locked, hashable part of the scorer: change here => new fingerprint."""
    return {
        "scorer_id": SCORER_ID,
        "version": SCORER_VERSION,
        "score_form": SCORE_FORM,
        "score_scale": SCORE_SCALE,
        "e_max_default": E_MAX_DEFAULT,
        "mean_weight": MEAN_WEIGHT,
        "unit_decade_tol": UNIT_DECADE_TOL,
        "leak_min_hits": LEAK_MIN_HITS,
        "regime_tol": REGIME_TOL,
        "gates": list(GATES),
    }


def scorer_fingerprint(anchor_head: str, version: Optional[str] = None) -> str:
    """Version lock: fingerprint binds scorer spec *and* the anchor ledger head."""
    spec = scorer_spec()
    spec["version"] = SCORER_VERSION if version is None else version
    spec["anchor_head"] = anchor_head
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def payload_hash(entries: Sequence[Mapping[str, Any]]) -> str:
    norm = sorted(
        (str(e.get("anchor_id")), repr(float(e.get("value")))) for e in entries
        if _isfinite(e.get("value"))
    )
    return hashlib.sha256(canonical_json(norm).encode("utf-8")).hexdigest()


def _isfinite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _resolve_e_max(anchor: Any, e_max: Optional[float], default: float = E_MAX_DEFAULT) -> float:
    if e_max is not None:
        return float(e_max)
    val = getattr(anchor, "score_e_max", None)
    return float(val) if val else float(default)


def anchor_score(
    value: float,
    anchor: Any,
    e_max: Optional[float] = None,
    e_max_default: float = E_MAX_DEFAULT,
) -> Dict[str, float]:
    """Closed-form per-anchor score (see module docstring for the limits)."""
    a = float(anchor.value)
    emax = _resolve_e_max(anchor, e_max, e_max_default)
    scale = abs(a) if a != 0.0 else 1.0
    rel_err = abs(float(value) - a) / scale
    err_ratio = rel_err / emax
    clipped = min(1.0, max(0.0, 1.0 - err_ratio))
    return {"rel_err": rel_err, "e_max": emax, "score": SCORE_SCALE * clipped}


def aggregate(scores: Sequence[float]) -> Dict[str, float]:
    """Robust aggregate: half mean, half worst (worst-case cap for Goodhart)."""
    if not scores:
        return {"score": 0.0, "mean": 0.0, "worst": 0.0}
    mean_s = sum(scores) / len(scores)
    worst_s = min(scores)
    total = MEAN_WEIGHT * mean_s + (1.0 - MEAN_WEIGHT) * worst_s
    return {"score": total, "mean": mean_s, "worst": worst_s}


def _reject(reasons: List[Dict[str, Any]], code: str, **detail: Any) -> None:
    reasons.append({"code": code, **detail})


def score_submission(
    entries: Sequence[Mapping[str, Any]],
    anchors: Sequence[Any],
    *,
    expected_fingerprint: Optional[str] = None,
    anchor_head: Optional[str] = None,
    seen_payloads: Optional[Iterable[str]] = None,
    require_all: bool = True,
    e_max_default: float = E_MAX_DEFAULT,
) -> Dict[str, Any]:
    """Score a submission; fail-closed. Deterministic and JSON serialisable."""
    from .anchors import chain_head

    anchors_by_id = {a.anchor_id: a for a in anchors}
    head = anchor_head if anchor_head is not None else chain_head([a.to_dict() for a in anchors])
    fp = scorer_fingerprint(head)
    reasons: List[Dict[str, Any]] = []

    # --- gate LOCK: refuse to score with an unlocked / drifted scorer
    if expected_fingerprint is not None and fp != expected_fingerprint:
        _reject(reasons, "SCORER_LOCK_MISMATCH", expected=expected_fingerprint, actual=fp)

    # --- gate REPLAY
    ph = payload_hash(entries)
    if seen_payloads is not None and ph in set(seen_payloads):
        _reject(reasons, "REPLAY_DUPLICATE", payload_hash=ph)

    per_anchor: Dict[str, Dict[str, Any]] = {}
    known_pairs: List[tuple] = []
    leak_hits = 0
    unit_hits: List[Dict[str, Any]] = []

    for entry in entries:
        aid = entry.get("anchor_id")
        value = entry.get("value")

        # --- gate FINITE
        if not _isfinite(value):
            _reject(reasons, "NON_FINITE_OR_NON_NUMERIC", anchor_id=str(aid), value=repr(value))
            continue

        anchor = anchors_by_id.get(aid)
        if anchor is None:
            _reject(reasons, "UNKNOWN_ANCHOR", anchor_id=str(aid))
            continue

        # --- gate ANCHOR_VERIFIED: quarantined/sourceless anchors are refused
        if anchor.provenance_status != "VERIFIED" or anchor.value is None:
            _reject(reasons, "ANCHOR_NOT_VERIFIED", anchor_id=str(aid),
                    provenance_status=str(anchor.provenance_status))
            continue

        # --- gate ENVELOPE / REGIME on a self-declared regime
        regime = entry.get("regime") or {}
        out_of_envelope = False
        for key, bounds in (anchor.envelope or {}).items():
            if key in regime and _isfinite(regime[key]):
                lo, hi = float(bounds[0]), float(bounds[1])
                if not (lo <= float(regime[key]) <= hi):
                    out_of_envelope = True
                    _reject(reasons, "OUT_OF_ENVELOPE", anchor_id=str(aid), covariate=key,
                            reported=float(regime[key]), envelope=[lo, hi])
        for key, target in (anchor.covariates or {}).items():
            if key in regime and _isfinite(regime[key]):
                ref = float(target)
                if ref != 0.0 and abs(float(regime[key]) - ref) / abs(ref) > REGIME_TOL:
                    if not out_of_envelope:
                        _reject(reasons, "REGIME_MISMATCH", anchor_id=str(aid), covariate=key,
                                reported=float(regime[key]), anchor=ref)

        a_val = float(anchor.value)
        res = anchor_score(float(value), anchor, e_max_default=e_max_default)
        per_anchor[str(aid)] = {"value": float(value), "anchor": a_val, **res}

        # only *measured* anchors can be leaked: an analytic closed form may be
        # legitimately reproduced bit-exactly (that is the L-case limit)
        if float(value) == a_val and anchor.source_type == "EXPERIMENT":
            leak_hits += 1
        ratio = abs(float(value) / a_val) if a_val != 0.0 else float("inf")
        if ratio and (ratio > 0.0) and abs(math.log10(ratio)) >= UNIT_DECADE_TOL:
            unit_hits.append({"anchor_id": str(aid), "decades": math.log10(ratio)})
        known_pairs.append((str(aid), float(value), a_val))

    # --- gate ANCHOR_LEAK: bit-exact gold values on several *measured* anchors
    if leak_hits >= LEAK_MIN_HITS:
        _reject(reasons, "ANCHOR_LEAKAGE_SUSPECT", hits=leak_hits)

    # --- gate UNIT_DECADE
    if unit_hits:
        _reject(reasons, "UNIT_SCALE_ERROR", hits=unit_hits)

    # --- gate DEGENERATE: one constant output spread over distinct anchors
    if len(known_pairs) >= 3:
        vals = [v for _, v, _ in known_pairs]
        ancs = [av for _, _, av in known_pairs]
        vmax = max(abs(v) for v in vals) or TINY
        spread = max(vals) - min(vals)
        anchor_sep = (max(ancs) - min(ancs)) / (max(abs(x) for x in ancs) or TINY)
        if abs(spread) / vmax <= CONST_SPREAD_TOL and anchor_sep >= CONST_ANCHOR_SEP:
            _reject(reasons, "DEGENERATE_CONSTANT_OUTPUT", n_anchors=len(vals))

    # --- gate PHYSICS: submitted invariants must close
    claims = {}
    for entry in entries:
        c = entry.get("physics")
        if isinstance(c, Mapping):
            claims.update(c)
    phys_violations = physics.check_claims(claims)
    if phys_violations:
        _reject(reasons, "PHYSICS_INVARIANT_VIOLATION", violations=phys_violations)

    # --- gate MISSING: full anchor coverage
    verified_ids = {a.anchor_id for a in anchors if a.provenance_status == "VERIFIED"}
    missing = sorted(verified_ids - {aid for aid, _, _ in known_pairs})
    if require_all and missing:
        _reject(reasons, "MISSING_ANCHOR", missing=missing)

    scores = [v["score"] for v in per_anchor.values()]
    agg = aggregate(scores)
    accepted = not reasons
    return {
        "verdict": "ACCEPTED" if accepted else "REJECTED",
        "score": agg["score"] if accepted else 0.0,
        "reasons": reasons,
        "per_anchor": per_anchor,
        "diagnostics": {"mean": agg["mean"], "worst": agg["worst"], "n_scored": len(scores)},
        "scorer": {
            "scorer_id": SCORER_ID,
            "version": SCORER_VERSION,
            "score_form": SCORE_FORM,
            "fingerprint": fp,
            "anchor_head": head,
            "payload_hash": ph,
        },
    }