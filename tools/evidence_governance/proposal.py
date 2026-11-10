"""Evidence-debt compiler: turn each WITHHELD item into a structured proposal.

Acceptance criterion #2 requires, for *every* WITHHELD entry, a machine-readable
"acquire this missing evidence" proposal carrying at least:

* ``baseline_type``            — what kind of baseline is missing,
* ``missing_magnitude``        — how large the gap is (normalised 0..1 index),
* ``priority``                 — where the debt sits in the queue.

Design notes
------------
* The magnitude is a **normalised, auditable index**, not a physical quantity:
  it is a weighted sum of three structural deficits — evidence density,
  claim/reality divergence and corroboration breadth. Nothing here needs a
  subject domain: the weights and saturations are configuration.
* The verification spec (required evidence + acceptance criteria + task text) is
  produced by the debt-compiler's ``VerificationTaskGenerator``. That is the
  second merge point: the two previously separate pipelines now share one model.
* Deterministic: proposals are sorted by (band, -score, capability id) and carry
  a content digest.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.evidence_debt_compiler.model import BaselineType
from tools.evidence_debt_compiler.task_generator import VerificationTaskGenerator

from .classifier import GovernanceClassifier, as_extracted_debt
from .model import LibraryItem, VerificationProposal, digest


class ProposalCompiler:
    """Compile structured evidence-acquisition proposals from scanned items."""

    def __init__(
        self,
        config: Dict[str, Any],
        classifier: GovernanceClassifier,
        task_generator: Optional[VerificationTaskGenerator] = None,
    ):
        self.config = config
        self.classifier = classifier
        self.task_generator = task_generator or VerificationTaskGenerator()
        mag = config.get("magnitude", {})
        self.sat_evidence = float(mag.get("evidence_count_saturation", 3))
        self.sat_independent = float(mag.get("independent_ref_saturation", 2))
        self.mag_weights = dict(mag.get(
            "weights",
            {"evidence_gap": 0.45, "claim_gap": 0.35, "corroboration_gap": 0.20},
        ))
        pri = config.get("priority", {})
        self.pri_weights = dict(pri.get(
            "weights", {"magnitude": 0.5, "criticality": 0.3, "overclaim": 0.2},
        ))
        self.bands = list(pri.get("bands", [
            {"band": "P0", "min": 0.75},
            {"band": "P1", "min": 0.50},
            {"band": "P2", "min": 0.0},
        ]))
        self.ladder = list(config.get("evidence_ladder", [
            "physical_consistency",
            "cross_validation",
            "mesh_convergence",
            "analytical_solution",
            "experimental",
        ]))
        self.rank = dict(config.get("level_rank", {}))
        self.non_evidence_band = str(pri.get("non_evidence_band", "P3"))

    # ------------------------------------------------------------- components
    def _rank(self, level: str) -> int:
        return int(self.rank.get(str(level), 0))

    def _claim_gap(self, item: LibraryItem) -> float:
        """How far the *claim* sits above the reconciled level, normalised."""
        r_claim = self._rank(item.claimed_level)
        r_now = self._rank(item.reconciled_level)
        headroom = max(self.rank.values(), default=4) - r_now
        if headroom <= 0 or r_claim <= r_now:
            return 0.0
        return min(1.0, (r_claim - r_now) / headroom)

    def magnitude_components(self, item: LibraryItem) -> Dict[str, float]:
        evidence_gap = 1.0 - min(1.0, item.evidence_count / max(self.sat_evidence, 1.0))
        corroboration_gap = 1.0 - min(
            1.0, item.independent_refs / max(self.sat_independent, 1.0)
        )
        return {
            "evidence_gap": evidence_gap,
            "claim_gap": self._claim_gap(item),
            "corroboration_gap": corroboration_gap,
        }

    def missing_magnitude(self, item: LibraryItem) -> float:
        comps = self.magnitude_components(item)
        total = sum(self.mag_weights.get(k, 0.0) * v for k, v in comps.items())
        return max(0.0, min(1.0, total))

    def priority(self, item: LibraryItem, magnitude: float) -> tuple[float, str]:
        overclaim = 1.0 if self._rank(item.claimed_level) > self._rank(
            item.reconciled_level
        ) else 0.0
        score = (
            self.pri_weights.get("magnitude", 0.5) * magnitude
            + self.pri_weights.get("criticality", 0.3) * item.criticality
            + self.pri_weights.get("overclaim", 0.2) * overclaim
        )
        score = max(0.0, min(1.0, score))
        if not item.requires_evidence:
            return score, self.non_evidence_band
        for band in self.bands:
            if score >= float(band["min"]):
                return score, str(band["band"])
        return score, self.bands[-1]["band"]

    # ---------------------------------------------------------------- compile
    def compile_item(self, item: LibraryItem) -> VerificationProposal:
        debt = as_extracted_debt(item)
        result = self.classifier.explain(item.declaration_text)
        classified = self.classifier.classify(debt)
        task = self.task_generator.generate(classified)
        spec = dict(task.spec or {})
        required = dict(spec.get("required_evidence", {}))
        acceptance = dict(spec.get("acceptance_criteria", {}))

        comps = self.magnitude_components(item)
        mag = self.missing_magnitude(item)
        score, band = self.priority(item, mag)

        baseline_type = str(result["baseline_type"])
        if baseline_type == BaselineType.UNKNOWN.value:
            first_rung = self.ladder[0] if self.ladder else BaselineType.UNKNOWN.value
        else:
            first_rung = baseline_type

        n_metrics = len(required.get("metrics", []) or [])
        if item.requires_evidence:
            units = max(
                1,
                n_metrics
                + (1 if item.evidence_count == 0 else 0)
                + (1 if comps["claim_gap"] > 0 else 0),
            )
        else:
            units = 0

        return VerificationProposal(
            capability_id=item.capability_id,
            baseline_type=baseline_type,
            classification_confidence=float(result["confidence"]),
            classification_rationale=str(result["rationale"]),
            recommended_first_rung=first_rung,
            missing_magnitude=mag,
            missing_evidence_units=units,
            magnitude_components=comps,
            priority_score=score,
            priority_band=band,
            requires_evidence=item.requires_evidence,
            required_evidence=required,
            acceptance_criteria=acceptance,
            task_description=str(spec.get("task_description", "")),
        )

    def compile(self, items: List[LibraryItem]) -> List[VerificationProposal]:
        proposals = [self.compile_item(i) for i in items]
        proposals.sort(key=lambda p: (p.priority_band, -p.priority_score, p.capability_id))
        return proposals


def summarise_proposals(proposals: List[VerificationProposal]) -> Dict[str, Any]:
    """Aggregate counts used by the report and by the KPI artifact."""
    by_type: Dict[str, int] = {}
    by_band: Dict[str, int] = {}
    for p in proposals:
        by_type[p.baseline_type] = by_type.get(p.baseline_type, 0) + 1
        by_band[p.priority_band] = by_band.get(p.priority_band, 0) + 1
    payload = {
        "n_proposals": len(proposals),
        "by_baseline_type": dict(sorted(by_type.items())),
        "by_priority_band": dict(sorted(by_band.items())),
        "sum_missing_units": sum(p.missing_evidence_units for p in proposals),
        "mean_missing_magnitude": round(
            (sum(p.missing_magnitude for p in proposals) / len(proposals))
            if proposals else 0.0, 4
        ),
        "proposals": [p.as_dict() for p in proposals],
    }
    payload["digest"] = digest(payload)
    return payload