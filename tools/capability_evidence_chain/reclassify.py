"""Historical PASS reclassification: point claims -> power / MDE audit.

The platform has accumulated many ``"status": "PASS"`` declarations produced by
simple threshold comparisons.  Such a PASS says nothing about whether the test
could have *detected* a violation of the threshold: with one sample and no
recorded spread the claim is not a statistical statement at all.

This module re-reads those declarations from configured sources and labels
each one:

=====================  =====================================================
classification         meaning
=====================  =====================================================
``ADEQUATE``           value, sample size and spread recorded; the TOST
                       interval sits inside the margin and the design's MDE
                       covers the margin at the target power
``UNDERPOWERED``       statistics computable but the MDE does not cover the
                       margin (or achieved power < target): PASS is nominal,
                       flagged ``needs_reverification``
``THRESHOLD_ONLY``     PASS by point comparison: no ``n`` / spread recorded, so
                       no uncertainty-aware statement can be recovered;
                       flagged ``needs_reverification``
``INCONSISTENT``       PASS declared although the value violates its own
                       threshold (overclaim; always an audit finding)
``UNVERIFIABLE``       PASS with no machine-readable value at all
``FAIL_RECORDED``      an honest FAIL declaration, preserved unchanged
=====================  =====================================================

The summary is *recomputable*: a SHA-256 digest over the canonical claim list
plus the re-derived rate is stored, and :func:`recompute` independently
re-derives the rate from the claim records so criterion (d) can be executed
rather than trusted.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re

import numpy as np
from scipy import stats
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import equivalence as eq
from .config import ReclassSpec

__all__ = [
    "Claim",
    "extract_claims",
    "classify",
    "summarize",
    "recompute",
    "parse_threshold",
    "scan_claims",
]

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_threshold(raw: Any) -> Optional[float]:
    """Extract a numeric threshold from a number or a string like ``"≤1e-12"``."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v == v else None
    if isinstance(raw, str):
        m = _NUM_RE.search(raw.replace(" ", ""))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


@dataclass
class Claim:
    source: str
    pointer: str
    status: str
    value: Optional[float]
    threshold: Optional[float]
    n: Optional[float] = None
    sd: Optional[float] = None
    classification: str = ""
    needs_reverification: bool = False
    power: Optional[float] = None
    power_at_zero: Optional[float] = None
    mde: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "pointer": self.pointer,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "n": self.n,
            "sd": self.sd,
            "classification": self.classification,
            "needs_reverification": self.needs_reverification,
            "power": self.power,
            "power_at_zero": self.power_at_zero,
            "mde": self.mde,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "reason": self.reason,
        }

    def canonical(self) -> str:
        return json.dumps(
            [self.source, self.pointer, self.status, self.value, self.threshold,
             self.n, self.sd, self.classification],
            sort_keys=True, separators=(",", ":"),
        )


def _paired_numbers(
    node: Dict[str, Any], spec: ReclassSpec
) -> Tuple[Optional[float], Optional[float]]:
    """Resolve ``(value, threshold)`` through unit-consistent key pairs first."""
    for vk, tk in spec.value_threshold_pairs:
        if vk in node and tk in node:
            v = parse_threshold(node[vk])
            t = parse_threshold(node[tk])
            if v is not None and t is not None:
                return v, t
    return None, None


def _first_number(node: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
    for k in keys:
        if k in node:
            v = parse_threshold(node[k])
            if v is not None:
                return v
    return None


def _status_token(value: Any, spec: ReclassSpec) -> Optional[str]:
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if token in {t.upper() for t in spec.pass_tokens}:
        return "PASS"
    if token in {t.upper() for t in spec.fail_tokens}:
        return "FAIL"
    return None


def _walk_claims(
    obj: Any, spec: ReclassSpec, pointer: str = ""
) -> Iterator[Tuple[str, Dict[str, Any], str]]:
    """Yield ``(pointer, node, status)`` for every dict carrying a PASS/FAIL."""
    if isinstance(obj, dict):
        for k in spec.status_keys:
            if k in obj:
                token = _status_token(obj[k], spec)
                if token is not None:
                    yield pointer or "/", obj, token
                    break
        for k, v in obj.items():
            yield from _walk_claims(v, spec, f"{pointer}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_claims(v, spec, f"{pointer}[{i}]")


def extract_claims(
    payload: Any, source: str, spec: ReclassSpec
) -> List[Claim]:
    claims: List[Claim] = []
    for pointer, node, status in _walk_claims(payload, spec):
        paired_value, paired_threshold = _paired_numbers(node, spec)
        claims.append(
            Claim(
                source=source,
                pointer=pointer,
                status=status,
                value=paired_value
                if paired_value is not None
                else _first_number(node, spec.value_keys),
                threshold=paired_threshold
                if paired_threshold is not None
                else _first_number(node, spec.threshold_keys),
                n=_first_number(node, spec.n_keys),
                sd=_first_number(node, spec.spread_keys),
            )
        )
    return claims


def classify(claim: Claim, spec: ReclassSpec) -> Claim:
    """Assign a classification (and re-verification flag) to a single claim."""
    if claim.status == "FAIL":
        claim.classification = "FAIL_RECORDED"
        claim.reason = "honest FAIL declaration is preserved and not reclassified"
        return claim
    threshold = claim.threshold
    if claim.value is None:
        claim.classification = "UNVERIFIABLE"
        claim.needs_reverification = True
        claim.reason = "no machine-readable value recorded"
        return claim
    if threshold is None:
        claim.classification = "THRESHOLD_ONLY"
        claim.needs_reverification = True
        claim.reason = "no machine-readable threshold: PASS is a point claim"
        return claim
    margin = abs(threshold)
    if margin == 0.0:
        margin = spec.margin_default
    if abs(claim.value) > margin:
        claim.classification = "INCONSISTENT"
        claim.reason = (
            f"value {claim.value} violates its own threshold {threshold}"
        )
        return claim
    if claim.n is None or claim.sd is None or claim.n < spec.min_computable_n or claim.sd <= 0:
        claim.classification = "THRESHOLD_ONLY"
        claim.needs_reverification = True
        claim.reason = (
            "PASS by point comparison: sample size and spread not recorded, so "
            "power/MDE cannot be recovered"
        )
        return claim
    n = int(claim.n)
    se = claim.sd / np.sqrt(n)
    t_crit = float(stats.t.ppf(1.0 - spec.alpha, n - 1))
    claim.ci_low = claim.value - t_crit * se
    claim.ci_high = claim.value + t_crit * se
    claim.power = eq.power_mean_one_sample(n, claim.sd, claim.value, margin, spec.alpha)
    claim.power_at_zero = eq.power_mean_one_sample(n, claim.sd, 0.0, margin, spec.alpha)
    claim.mde = eq.mde_mean_one_sample(
        n, claim.sd, margin, spec.alpha, spec.power_target
    )
    interval_inside = claim.ci_low > -margin and claim.ci_high < margin
    adequate = (
        interval_inside
        and claim.power_at_zero >= spec.power_target
        and claim.mde >= spec.mde_fraction * margin
    )
    if adequate:
        claim.classification = "ADEQUATE"
        claim.reason = (
            f"dual interval inside the margin, power at zero deviation "
            f"{claim.power_at_zero:.3f}, MDE {claim.mde:.4g} covers "
            f"{spec.mde_fraction:.0%} of margin {margin:.4g}"
        )
    else:
        claim.classification = "UNDERPOWERED"
        claim.needs_reverification = True
        claim.reason = (
            f"power at zero deviation {claim.power_at_zero:.3f} / MDE "
            f"{claim.mde:.4g} do not support margin {margin:.4g} at target "
            f"{spec.power_target:.2f} (MDE coverage fraction "
            f"{spec.mde_fraction:.2f}); interval "
            f"[{claim.ci_low:.4g}, {claim.ci_high:.4g}]"
        )
    return claim


def summarize(claims: Sequence[Claim]) -> Dict[str, Any]:
    """Aggregate claims into the reclassification statistics (criterion (d))."""
    counts: Dict[str, int] = {}
    for c in claims:
        counts[c.classification] = counts.get(c.classification, 0) + 1
    n_pass = sum(1 for c in claims if c.status == "PASS")
    n_fail = sum(1 for c in claims if c.status == "FAIL")
    flagged = [c for c in claims if c.status == "PASS" and c.needs_reverification]
    digest = hashlib.sha256(
        "\n".join(c.canonical() for c in sorted(claims, key=lambda x: (x.source, x.pointer))).encode()
    ).hexdigest()
    return {
        "n_claims": len(claims),
        "n_pass": n_pass,
        "n_fail": n_fail,
        "classification": counts,
        "n_flagged": len(flagged),
        "low_power_flag_rate": (len(flagged) / n_pass) if n_pass else 0.0,
        "claims_digest": digest,
    }


def recompute(summary: Dict[str, Any], claims: Sequence[Claim]) -> Dict[str, Any]:
    """Independently re-derive the flag rate and digest from claim records.

    Returns a dict with ``rate_matches`` / ``digest_matches`` so an auditor can
    execute criterion (d) instead of trusting the published number.
    """
    fresh = summarize(claims)
    return {
        "expected_rate": summary.get("low_power_flag_rate"),
        "recomputed_rate": fresh["low_power_flag_rate"],
        "rate_matches": abs(
            float(summary.get("low_power_flag_rate", 0.0)) - fresh["low_power_flag_rate"]
        ) < 1e-12,
        "expected_digest": summary.get("claims_digest"),
        "recomputed_digest": fresh["claims_digest"],
        "digest_matches": summary.get("claims_digest") == fresh["claims_digest"],
        "n_claims": fresh["n_claims"],
    }


def scan_claims(
    root: Path, spec: ReclassSpec
) -> Tuple[List[Claim], List[Dict[str, Any]]]:
    """Read every configured source, returning claims plus a source inventory."""
    claims: List[Claim] = []
    inventory: List[Dict[str, Any]] = []
    for pattern in spec.sources:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path.suffix != ".json":
                continue
            rel = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in spec.exclude):
                continue
            record = {"source": rel, "bytes": path.stat().st_size}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                record["status"] = "unreadable"
                record["error"] = str(exc)[:200]
                inventory.append(record)
                continue
            found = extract_claims(payload, rel, spec)
            for c in found:
                classify(c, spec)
            record["status"] = "ok"
            record["n_claims"] = len(found)
            inventory.append(record)
            claims.extend(found)
    return claims, inventory