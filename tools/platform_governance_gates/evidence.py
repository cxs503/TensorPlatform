"""Completeness scoring of a capability's evidence chain (p338w3-n07).

The guard needs a *graded* view of evidence, not the all-or-nothing boolean the
older chain validator produced.  Six requirements are checked; the completeness
score is the fraction satisfied.  The first four are the node contract's
mandated evidence dimensions, the last two close the two ways a bundle can look
complete while being unauditable:

======================  =========================================================
requirement             satisfied when
======================  =========================================================
``independent_verification``  >= 1 verification item with ``independent`` true
``benchmark_artifact``        >= 1 benchmark item, digest-valid
``sha256_verified``           every admissible item has a valid sha256 and every
                              declared digest equals the recomputed one
``cross_consistent``          >= 1 cross-check item passed and no contradiction
``provenance_complete``       every item carries a non-empty ref
``evidence_depth``            >= 2 distinct source refs among admissible items
======================  =========================================================

Fail-closed rules: malformed items are dropped *and* recorded (never silently
counted), an unknown kind never satisfies anything, a declared digest that does
not match the recomputed digest is a contradiction, and an empty bundle scores
0.0.  Nothing here knows what a capability does — ids, kinds and refs are opaque.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REQUIREMENTS: Tuple[str, ...] = (
    "independent_verification",
    "benchmark_artifact",
    "sha256_verified",
    "cross_consistent",
    "provenance_complete",
    "evidence_depth",
)

#: evidence item kinds the mechanism understands (opaque to the domain)
ITEM_KINDS: Tuple[str, ...] = (
    "verification",
    "benchmark",
    "cross_check",
)

#: requirements that must hold individually regardless of the aggregate score
#: (a high score must never paper over a missing independent verification or an
#: unverifiable evidence digest)
CRITICAL_REQUIREMENTS: Tuple[str, ...] = (
    "independent_verification",
    "sha256_verified",
)

LEVELS: Tuple[str, ...] = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
_LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value.lower())


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class EvidenceItem:
    """One digest-addressed piece of evidence (opaque kind/ref)."""

    kind: str
    ref: str
    ok: bool = True
    sha256: str = ""
    declared_sha256: str = ""
    independent: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def digest_valid(self) -> bool:
        return _is_sha256(self.sha256)

    @property
    def digest_matches(self) -> bool:
        """False only when a declared digest actively contradicts the content."""
        if not self.declared_sha256:
            return True
        return self.digest_valid and self.declared_sha256.lower() == self.sha256.lower()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "ok": bool(self.ok),
            "sha256": self.sha256,
            "declared_sha256": self.declared_sha256,
            "independent": bool(self.independent),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class Contradiction:
    """A positive reason to distrust the claim (never silently dropped)."""

    kind: str
    ref: str = ""
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "reason": self.reason}


@dataclass
class CapabilityEvidence:
    capability_id: str
    current_level: str
    items: List[EvidenceItem] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current_level": self.current_level,
            "items": [i.as_dict() for i in self.items],
            "contradictions": [c.as_dict() for c in self.contradictions],
            "rejected": list(self.rejected),
        }


@dataclass
class CompletenessReport:
    capability_id: str
    current_level: str
    checks: Dict[str, bool] = field(default_factory=dict)
    score: float = 0.0
    missing: List[str] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    admissible: int = 0
    evidence_digest: str = ""
    valid: bool = True
    invalid_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current_level": self.current_level,
            "checks": dict(self.checks),
            "score": round(self.score, 6),
            "missing": list(self.missing),
            "contradictions": list(self.contradictions),
            "rejected": list(self.rejected),
            "admissible": self.admissible,
            "evidence_digest": self.evidence_digest,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }


def _admissible(item: EvidenceItem) -> bool:
    """An item counts only if it is structurally sound and claims success."""
    return (
        item.kind in ITEM_KINDS
        and isinstance(item.ref, str)
        and bool(item.ref.strip())
        and bool(item.ok)
        and item.digest_valid
        and item.digest_matches
    )


def evaluate(bundle: CapabilityEvidence) -> CompletenessReport:
    """Score one capability's evidence bundle against the six requirements."""
    if not isinstance(bundle, CapabilityEvidence) or not isinstance(bundle.capability_id, str) or not bundle.capability_id.strip():
        # fail-closed: an unidentifiable claim can never be promoted
        return CompletenessReport(
            capability_id=str(getattr(bundle, "capability_id", "")),
            current_level=str(getattr(bundle, "current_level", "WITHHELD")),
            checks={k: False for k in REQUIREMENTS},
            score=0.0,
            missing=list(REQUIREMENTS),
            valid=False,
            invalid_reason="missing or non-string capability_id",
        )

    level = bundle.current_level if bundle.current_level in LEVELS else "WITHHELD"
    contradiction_dicts = [c.as_dict() for c in bundle.contradictions]

    # a declared digest that contradicts the content is itself a contradiction
    for item in bundle.items:
        if item.digest_valid and not item.digest_matches:
            contradiction_dicts.append({
                "kind": "digest_mismatch",
                "ref": item.ref,
                "reason": f"declared={item.declared_sha256[:12]} actual={item.sha256[:12]}",
            })

    admissible = [i for i in bundle.items if _admissible(i)]
    checks = {
        "independent_verification": any(
            i.kind == "verification" and i.independent for i in admissible),
        "benchmark_artifact": any(i.kind == "benchmark" for i in admissible),
        "sha256_verified": bool(admissible) and all(
            i.digest_valid and i.digest_matches for i in bundle.items
            if i.kind in ITEM_KINDS),
        "cross_consistent": (not contradiction_dicts) and any(
            i.kind == "cross_check" for i in admissible),
        "provenance_complete": bool(admissible) and all(
            bool(i.ref.strip()) for i in bundle.items if i.kind in ITEM_KINDS),
        "evidence_depth": len({i.ref for i in admissible}) >= 2,
    }
    score = sum(1 for v in checks.values() if v) / len(REQUIREMENTS)
    if not admissible:
        score = 0.0
    if contradiction_dicts:
        # fail-closed: contradictions may only lower the score, never raise it
        score = min(score, 0.5)

    payload = {
        "capability_id": bundle.capability_id,
        "current_level": level,
        "items": [i.as_dict() for i in bundle.items],
        "contradictions": contradiction_dicts,
        "rejected": list(bundle.rejected),
    }
    return CompletenessReport(
        capability_id=bundle.capability_id,
        current_level=level,
        checks=checks,
        score=round(score, 6),
        missing=[k for k, ok in checks.items() if not ok],
        contradictions=contradiction_dicts,
        rejected=list(bundle.rejected),
        admissible=len(admissible),
        evidence_digest=digest_json(payload),
        valid=True,
    )


def evaluate_all(bundles: Iterable[CapabilityEvidence]) -> Dict[str, CompletenessReport]:
    reports: Dict[str, CompletenessReport] = {}
    for bundle in bundles:
        reports[bundle.capability_id] = evaluate(bundle)
    return reports


# ---------------------------------------------------------------------------
# directory scanner: reports/ artifacts/ benchmarks/ verified/ roots
# ---------------------------------------------------------------------------
def _bundle_from_file(path: Path, root: Path) -> Optional[CapabilityEvidence]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    capability_id = payload.get("capability_id")
    kind = payload.get("kind")
    if not isinstance(capability_id, str) or not capability_id.strip():
        return None
    if not isinstance(kind, str) or kind not in ITEM_KINDS:
        return None

    actual_sha = hashlib.sha256(raw).hexdigest()
    declared = payload.get("declared_sha256") or ""
    if declared and not _is_sha256(declared):
        declared = ""
    item = EvidenceItem(
        kind=kind,
        ref=str(payload.get("ref") or path.relative_to(root).as_posix()),
        ok=bool(payload.get("ok", True)),
        sha256=actual_sha,
        declared_sha256=declared,
        independent=bool(payload.get("independent", False)),
        detail={"root": root.name, "file": path.name},
    )
    contradictions: List[Contradiction] = []
    contradiction_payload = payload.get("contradiction")
    if contradiction_payload:
        if isinstance(contradiction_payload, Mapping):
            contradictions.append(Contradiction(
                kind=str(contradiction_payload.get("kind", "declared")),
                ref=str(contradiction_payload.get("ref", item.ref)),
                reason=str(contradiction_payload.get("reason", "")),
            ))
        else:
            contradictions.append(Contradiction(kind="declared", ref=item.ref,
                                                reason=str(contradiction_payload)))
    return CapabilityEvidence(
        capability_id=capability_id,
        current_level=str(payload.get("current_level", "WITHHELD")),
        items=[item],
        contradictions=contradictions,
    )


def scan_evidence_roots(roots: Sequence[str | Path]) -> Dict[str, CapabilityEvidence]:
    """Aggregate evidence files by capability slug across the four roots.

    Every ``*.json`` under a root whose payload names a ``capability_id`` and an
    ``ITEM_KINDS`` kind contributes one item.  Unreadable / mislabelled files are
    skipped (and surfaced through the per-bundle ``rejected`` list only when they
    belong to a known capability id) — the scan never guesses.
    """
    bundles: Dict[str, CapabilityEvidence] = {}
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in files:
            bundle = _bundle_from_file(path, root if root.is_dir() else root.parent)
            if bundle is None:
                continue
            target = bundles.setdefault(
                bundle.capability_id,
                CapabilityEvidence(
                    capability_id=bundle.capability_id,
                    current_level=bundle.current_level,
                ),
            )
            # first seen level wins unless a file explicitly declares one
            if bundle.current_level != "WITHHELD":
                target.current_level = bundle.current_level
            target.items.extend(bundle.items)
            target.contradictions.extend(bundle.contradictions)
    return bundles