"""Evidence-chain construction: three items, machine verdicts, one digest.

An evidence chain for a capability is exactly three items::

    test_pass_rate        >= test_pass_rate_min
    benchmark_error_pct   <  benchmark_error_max_pct
    independent_review    == pass                (when required)

Each item is evaluated against the pre-declared threshold of the run; a missing
measurement counts as a **failed** item (absence of evidence is not evidence).
The chain digest is computed over the canonical JSON of the item verdicts, so a
reviewer can recompute it from the ledger alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .spec import (
    EvidenceRecord,
    Thresholds,
    canonical_json,
    fmt_number,
    sha256_json,
)


@dataclass(frozen=True)
class GateVerdict:
    """One measured item of the chain, evaluated against its threshold."""

    item: str
    measured: str
    threshold: str
    comparator: str
    passed: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item": self.item,
            "measured": self.measured,
            "threshold": self.threshold,
            "comparator": self.comparator,
            "passed": bool(self.passed),
            "note": self.note,
        }


@dataclass(frozen=True)
class ChainVerdict:
    """Three gate verdicts plus their machine verdict and digest."""

    capability_id: str
    items: Tuple[GateVerdict, ...]
    chain_complete: bool
    failed_items: Tuple[str, ...]
    thresholds_sha256: str

    def body(self) -> Dict[str, Any]:
        """Verdict fields the digest is computed over (digest excluded)."""
        return {
            "capability_id": self.capability_id,
            "items": [i.to_dict() for i in self.items],
            "chain_complete": bool(self.chain_complete),
            "failed_items": list(self.failed_items),
            "thresholds_sha256": self.thresholds_sha256,
        }

    def to_dict(self) -> Dict[str, Any]:
        body = self.body()
        body["chain_sha256"] = self.sha256()
        return body

    def sha256(self) -> str:
        return sha256_json(self.body())

    def detail(self, item: str) -> GateVerdict:
        for verdict in self.items:
            if verdict.item == item:
                return verdict
        raise KeyError(item)


def build_chain(record: EvidenceRecord, thresholds: Thresholds) -> ChainVerdict:
    """Evaluate the three evidence items of ``record`` against ``thresholds``."""
    thr = thresholds.validate()
    items: List[GateVerdict] = []

    # item 1: test pass rate
    if record.test_pass_rate is None:
        items.append(
            GateVerdict(
                item="test_pass_rate",
                measured="null",
                threshold=fmt_number(thr.test_pass_rate_min),
                comparator=">=",
                passed=False,
                note="no executable test evidence",
            )
        )
    else:
        rate = float(record.test_pass_rate)
        items.append(
            GateVerdict(
                item="test_pass_rate",
                measured=fmt_number(rate),
                threshold=fmt_number(thr.test_pass_rate_min),
                comparator=">=",
                passed=rate >= thr.test_pass_rate_min,
            )
        )

    # item 2: benchmark agreement
    if record.benchmark_error_pct is None:
        items.append(
            GateVerdict(
                item="benchmark_error_pct",
                measured="null",
                threshold=fmt_number(thr.benchmark_error_max_pct),
                comparator="<",
                passed=False,
                note="no benchmark comparison recorded",
            )
        )
    else:
        err = float(record.benchmark_error_pct)
        items.append(
            GateVerdict(
                item="benchmark_error_pct",
                measured=fmt_number(err),
                threshold=fmt_number(thr.benchmark_error_max_pct),
                comparator="<",
                passed=err < thr.benchmark_error_max_pct,
            )
        )

    # item 3: independent review
    review_required = thr.require_independent_review
    items.append(
        GateVerdict(
            item="independent_review",
            measured=record.review_verdict,
            threshold="pass" if review_required else "not_required",
            comparator="==",
            passed=(record.review_verdict == "pass") if review_required else True,
            note="" if review_required else "review not required by this run's thresholds",
        )
    )

    failed = tuple(i.item for i in items if not i.passed)
    return ChainVerdict(
        capability_id=record.capability_id,
        items=tuple(items),
        chain_complete=not failed,
        failed_items=failed,
        thresholds_sha256=thr.sha256(),
    )


def build_chains(
    records: List[EvidenceRecord], thresholds: Thresholds
) -> List[ChainVerdict]:
    return [build_chain(r, thresholds) for r in records]


def chain_digest(chain: ChainVerdict) -> str:
    """Digest of the verdict as stored on the ledger row."""
    return sha256_of_chain(chain)


def sha256_of_chain(chain: ChainVerdict) -> str:
    return chain.sha256()


def chain_table(chains: List[ChainVerdict]) -> str:
    """Human-readable (and diffable) gate matrix, used by the CLI and docs."""
    head = "capability_id | test_pass_rate | benchmark_error_pct | independent_review | complete"
    lines = [head, "-" * len(head)]
    for chain in sorted(chains, key=lambda c: c.capability_id):
        cells = []
        for item in chain.items:
            cells.append("PASS" if item.passed else "FAIL")
        lines.append(
            " | ".join([chain.capability_id, *cells, "yes" if chain.chain_complete else "no"])
        )
    return "\n".join(lines)


def gate_matrix_json(chains: List[ChainVerdict]) -> str:
    return canonical_json([c.to_dict() for c in sorted(chains, key=lambda c: c.capability_id)])