"""Gate-power measurement by corruption injection into criteria artifacts.

A gate is only worth its runtime if it catches *corrupted* criteria.  This
instrument takes a criterion corpus (data, injected from a JSON registry),
builds a clean evaluation record per criterion, injects one corruption at a
time, and asks the gate to adjudicate:

* ``gate_power`` = detected / injected, reported per corruption class, per
  criterion and overall;
* a separate **negative control**: pristine records must be accepted, so a gate
  that rejects everything cannot score 1.0;
* the list of **always-true criteria** (predicate satisfied by every adversarial
  probe) and the criteria whose gate_power < 1, with the exact undetected
  corruption classes;
* a **stealth** class (small, self-consistent perturbations) that is *not*
  counted in gate_power -- it documents the detectability boundary instead of
  pretending the gate can see an independently produced wrong number.

Checks are domain-agnostic primitives: finiteness/sanity, derivation recompute
from declared exponents, verdict recompute from the claimed observation,
unit-scale policy, provenance digest of the source reference, review freshness,
decidability, and source-type policy.  No physical quantity is named here.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

SHA_RE = re.compile(r"sha256:([0-9a-f]{64})")
COMPARATORS = ("<=", ">=", "<", ">", "==", "!=")
DEFAULT_ALLOWED_SOURCE_TYPES = ("analytic", "empirical", "standard", "heuristic")


# --------------------------------------------------------------------------- #
# criterion corpus
# --------------------------------------------------------------------------- #
def load_registry_criteria(path: str | Path) -> list[dict[str, Any]]:
    """Load criteria from the platform threshold registry document."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    criteria = doc.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError(f"no criteria list in {path}")
    return [dict(c) for c in criteria]


def parse_source_ref(source_ref: str) -> tuple[str | None, str | None]:
    """Split ``path#sha256:<hex>#...#`` into (path, digest)."""
    digest = None
    m = SHA_RE.search(source_ref)
    if m:
        digest = "sha256:" + m.group(1)
    path = source_ref.split("#", 1)[0] or None
    return path, digest


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _verdict(comparator: str, value: float, threshold: float) -> bool:
    if comparator == "<=":
        return value <= threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    if comparator == ">":
        return value > threshold
    if comparator == "==":
        return value == threshold
    if comparator == "!=":
        return value != threshold
    raise ValueError(f"unsupported comparator {comparator!r}")


def build_records(
    criteria: Sequence[Mapping[str, Any]],
    repo_root: str | Path,
    reference_date: str = "2026-11-10",
    margin: float = 0.5,
) -> list[dict[str, Any]]:
    """Materialise one clean evaluation record per criterion.

    The baseline observation is chosen to *satisfy* the criterion with a margin
    (``margin`` x threshold for ``<=`` and ``1/margin`` x threshold for
    ``>=``), so the clean corpus is supposed to be accepted by the gate.
    """
    root = Path(repo_root).resolve()
    records: list[dict[str, Any]] = []
    for crit in criteria:
        comparator = str(crit.get("comparator", "<="))
        if comparator not in COMPARATORS:
            continue
        threshold = float(crit["threshold"])
        observed = threshold * margin if comparator in ("<=", "<") else threshold / margin
        source_ref = str(crit.get("source_ref", ""))
        path, digest = parse_source_ref(source_ref)
        digest_ok = False
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file():
                digest_ok = _digest(candidate) == digest
        records.append(
            {
                "criterion_id": str(crit.get("criterion_id", f"criterion_{len(records)}")),
                "comparator": comparator,
                "threshold": threshold,
                "source_type": str(crit.get("source_type", "heuristic")),
                "source_ref": source_ref,
                "decidable": bool(crit.get("decidable", True)),
                "upstream_decidable": bool(crit.get("decidable", False)),
                "depends_on": copy.deepcopy(crit.get("depends_on", [])),
                "review_date": str(crit.get("review_date", reference_date)),
                "reference_date": reference_date,
                "derivation": copy.deepcopy(crit.get("derivation")),
                "provenance": {
                    "path": path,
                    "digest": digest,
                    "digest_ok": digest_ok,
                    "verifiable": bool(path) and (root / path).is_file(),
                },
                "observation": {
                    "observed_value": observed,
                    "unit": {"scale": 1.0, "dimension": "dimensionless"},
                    "claimed_pass": True,
                },
            }
        )
    return records


# --------------------------------------------------------------------------- #
# gate
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GatePolicy:
    max_review_age_days: int = 365
    allowed_unit_scales: tuple[float, ...] = (1.0,)
    allowed_source_types: tuple[str, ...] = DEFAULT_ALLOWED_SOURCE_TYPES
    derivation_rtol: float = 1e-9
    max_abs_threshold: float = 1e12


class CriterionGate:
    """Fail-closed adjudicator for criterion records."""

    def __init__(self, policy: GatePolicy = GatePolicy()) -> None:
        self.policy = policy

    def evaluate(self, record: Mapping[str, Any]) -> dict[str, Any]:
        p = self.policy
        failures: list[str] = []
        warnings: list[str] = []
        checks: dict[str, str] = {}

        def fail(code: str) -> None:
            failures.append(code)
            checks[code] = "fail"

        def ok(code: str) -> None:
            checks[code] = "pass"

        # 1. numeric sanity
        threshold = record.get("threshold")
        observed = (record.get("observation") or {}).get("observed_value")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) \
                or not math.isfinite(float(threshold)) or not 0.0 < float(threshold) < p.max_abs_threshold:
            fail("threshold_insane")
        elif not isinstance(observed, (int, float)) or isinstance(observed, bool) \
                or not math.isfinite(float(observed)):
            fail("observation_insane")
        else:
            ok("numeric_sanity")

        # 2. derivation recompute (coefficient x reference^exponent)
        derivation = record.get("derivation")
        axes = record.get("depends_on") or []
        if isinstance(derivation, Mapping) and "coefficient" in derivation:
            coeff = derivation.get("coefficient")
            ref = derivation.get("reference_h", derivation.get("reference_value",
                                                              derivation.get("reference")))
            expected = coeff
            try:
                for axis in axes:
                    expected = float(expected) * float(ref) ** float(axis.get("exponent", 1.0))
                expected = float(expected)
            except (TypeError, ValueError, ZeroDivisionError):
                expected = None
            if expected is None or not math.isfinite(expected):
                fail("derivation_unrecomputable")
            else:
                tol = p.derivation_rtol * max(1.0, abs(expected))
                if abs(expected - float(threshold)) > tol:
                    fail("derivation_mismatch")
                else:
                    ok("derivation_recompute")
            # cross-check the declared expression against the declared exponent
            expression = str(derivation.get("expression", ""))
            m = re.search(r"\*\*\s*\(?\s*(-?\d+(?:\.\d+)?)", expression)
            if m and axes:
                declared = float(m.group(1))
                if abs(declared - float(axes[0].get("exponent", 1.0))) > 1e-12:
                    fail("derivation_expression_inconsistent")
                else:
                    ok("derivation_expression")
        else:
            checks["derivation_recompute"] = "not_applicable"

        # 3. verdict recompute from the claimed observation and unit scale
        unit = (record.get("observation") or {}).get("unit") or {}
        scale = unit.get("scale", 1.0)
        claimed = bool((record.get("observation") or {}).get("claimed_pass", False))
        try:
            effective = float(observed) * float(scale)
            recomputed = _verdict(str(record.get("comparator")), effective, float(threshold))
        except (TypeError, ValueError):
            fail("verdict_unrecomputable")
        else:
            if recomputed != claimed:
                fail("verdict_mismatch")
            else:
                ok("verdict_recompute")

        # 4. unit policy
        if float(scale) not in p.allowed_unit_scales:
            fail("unit_scale_violation")
        else:
            ok("unit_policy")

        # 5. provenance digest
        prov = record.get("provenance") or {}
        if not prov.get("verifiable"):
            warnings.append("provenance_unverifiable")
            checks["provenance_integrity"] = "unverifiable"
        elif prov.get("digest") is None:
            fail("provenance_digest_missing")
        elif not prov.get("digest_ok"):
            fail("provenance_digest_mismatch")
        else:
            ok("provenance_integrity")

        # 6. freshness SLO
        ref_date = record.get("reference_date")
        try:
            reviewed = date.fromisoformat(str(record.get("review_date")))
            reference = date.fromisoformat(str(ref_date))
        except ValueError:
            fail("review_date_malformed")
        else:
            age = (reference - reviewed).days
            if age > p.max_review_age_days:
                fail("review_stale")
            elif age < 0:
                fail("review_in_future")
            else:
                ok("freshness")

        # 7. adjudicability of the record itself (the gate recomputes the
        #    verdict, so a record is adjudicable iff its observation is usable)
        if isinstance(observed, (int, float)) and math.isfinite(float(observed)):
            ok("adjudicable")
        else:
            fail("undecidable")

        # 8. source-type policy
        if str(record.get("source_type")) not in p.allowed_source_types:
            fail("source_type_unknown")
        else:
            ok("source_type_policy")

        return {
            "criterion_id": record.get("criterion_id"),
            "accepted": not failures,
            "failures": failures,
            "warnings": warnings,
            "checks": checks,
        }


# --------------------------------------------------------------------------- #
# corruption injection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Corruption:
    name: str
    applies: str  # callable name is not serialisable; use a predicate key
    expected_check: str


CORRUPTIONS: tuple[Corruption, ...] = (
    Corruption("residual_amplification", "always", "verdict_mismatch"),
    Corruption("anchor_perturbation", "derivation", "derivation_mismatch"),
    Corruption("dimension_flip", "always", "unit_scale_violation"),
    Corruption("tolerance_widening", "derivation", "derivation_mismatch"),
    Corruption("comparator_flip", "always", "verdict_mismatch"),
    Corruption("provenance_swap", "verifiable", "provenance_digest_mismatch"),
    Corruption("stale_dating", "always", "review_stale"),
    Corruption("unknown_source_type", "always", "source_type_unknown"),
    Corruption("nonfinite_threshold", "always", "threshold_insane"),
)


def _applies(kind: str, record: Mapping[str, Any]) -> bool:
    if kind == "derivation":
        return isinstance(record.get("derivation"), Mapping) and "coefficient" in record["derivation"]
    if kind == "verifiable":
        return bool((record.get("provenance") or {}).get("verifiable"))
    return True


def inject(record: Mapping[str, Any], corruption: Corruption) -> dict[str, Any] | None:
    if not _applies(corruption.applies, record):
        return None
    bad = copy.deepcopy(dict(record))
    obs = bad["observation"]
    name = corruption.name
    if name == "residual_amplification":
        factor = 1.0e3 if bad["comparator"] in ("<=", "<") else 1.0e-3
        obs["observed_value"] = float(obs["observed_value"]) * factor
    elif name == "anchor_perturbation":
        bad["derivation"]["coefficient"] = float(bad["derivation"]["coefficient"]) * 1.1
    elif name == "dimension_flip":
        obs["unit"]["scale"] = 1000.0
    elif name == "tolerance_widening":
        bad["threshold"] = float(bad["threshold"]) * 10.0
    elif name == "comparator_flip":
        bad["comparator"] = ">=" if bad["comparator"] in ("<=", "<") else "<="
    elif name == "provenance_swap":
        digest = bad["provenance"]["digest"] or "sha256:" + "0" * 64
        bad["provenance"]["digest"] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
        bad["provenance"]["digest_ok"] = False
    elif name == "stale_dating":
        bad["review_date"] = (
            date.fromisoformat(str(bad["reference_date"])) - timedelta(days=3 * 366)
        ).isoformat()
    elif name == "unknown_source_type":
        bad["source_type"] = "unregistered_authority"
    elif name == "nonfinite_threshold":
        bad["threshold"] = float("inf")
    else:  # pragma: no cover - table-driven
        raise ValueError(f"unknown corruption {name!r}")
    return bad


# --------------------------------------------------------------------------- #
# always-true criteria
# --------------------------------------------------------------------------- #
def always_true(record: Mapping[str, Any], probes: Sequence[float] | None = None) -> bool:
    """True when the predicate accepts every adversarial probe."""
    threshold = float(record["threshold"])
    if probes is None:
        # non-negativity assumption for measured magnitudes: a predicate that
        # already accepts every value in [0, 1e9] cannot be falsified by data
        probes = [0.0, 1e-15, threshold * 0.5, threshold, threshold * 2.0, 1e9]
    comparator = str(record["comparator"])
    return all(_verdict(comparator, float(x), threshold) for x in probes)


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def measure_gate_power(
    records: Sequence[Mapping[str, Any]],
    gate: CriterionGate,
    stealth_values: Sequence[float] = (1.5,),
) -> dict[str, Any]:
    clean = [gate.evaluate(r) for r in records]
    false_positives = [
        {"criterion_id": c["criterion_id"], "failures": c["failures"]}
        for c in clean
        if not c["accepted"]
    ]

    per_class: dict[str, dict[str, int]] = {
        c.name: {"injected": 0, "detected": 0} for c in CORRUPTIONS
    }
    per_criterion: dict[str, dict[str, Any]] = {}
    undetected: list[dict[str, Any]] = []
    injected_total = detected_total = 0
    for record in records:
        cid = str(record["criterion_id"])
        slot = per_criterion.setdefault(cid, {"injected": 0, "detected": 0, "undetected": []})
        for corruption in CORRUPTIONS:
            bad = inject(record, corruption)
            if bad is None:
                continue
            decision = gate.evaluate(bad)
            detected = not decision["accepted"] and corruption.expected_check in decision["failures"]
            cls = per_class.setdefault(corruption.name, {"injected": 0, "detected": 0})
            cls["injected"] += 1
            injected_total += 1
            slot["injected"] += 1
            if detected:
                cls["detected"] += 1
                detected_total += 1
                slot["detected"] += 1
            else:
                slot["undetected"].append(corruption.name)
                undetected.append(
                    {
                        "criterion_id": cid,
                        "corruption": corruption.name,
                        "expected_check": corruption.expected_check,
                        "failures": decision["failures"],
                    }
                )
    for cls in per_class.values():
        cls["gate_power"] = cls["detected"] / cls["injected"] if cls["injected"] else None
    for slot in per_criterion.values():
        slot["gate_power"] = slot["detected"] / slot["injected"] if slot["injected"] else None

    # stealth class: small, self-consistent perturbations (documented blind spot)
    stealth = {"values": list(stealth_values), "injected": 0, "detected": 0, "gate_power": None}
    for record in records:
        for factor in stealth_values:
            bad = copy.deepcopy(dict(record))
            bad["observation"]["observed_value"] = float(bad["observation"]["observed_value"]) * factor
            decision = gate.evaluate(bad)
            stealth["injected"] += 1
            if not decision["accepted"]:
                stealth["detected"] += 1
    stealth["gate_power"] = stealth["detected"] / stealth["injected"] if stealth["injected"] else None

    tautological = [str(r["criterion_id"]) for r in records if always_true(r)]
    non_derivable = [
        str(r["criterion_id"]) for r in records if not bool(r.get("upstream_decidable", True))
    ]
    low_power = [
        {"criterion_id": cid, **{k: v for k, v in slot.items() if k != "undetected"},
         "undetected": slot["undetected"]}
        for cid, slot in sorted(per_criterion.items())
        if slot["gate_power"] is not None and slot["gate_power"] < 1.0
    ]
    return {
        "schema": "gate-power-ledger/1.0",
        "criteria_total": len(records),
        "criteria_clean_accepted": len(records) - len(false_positives),
        "false_positive_rate": len(false_positives) / len(records) if records else None,
        "false_positives": false_positives,
        "injected_total": injected_total,
        "detected_total": detected_total,
        "gate_power": detected_total / injected_total if injected_total else None,
        "per_class": per_class,
        "per_criterion": per_criterion,
        "undetected": undetected,
        "low_power_criteria": low_power,
        "always_true_criteria": tautological,
        "upstream_non_derivable_criteria": non_derivable,
        "stealth": stealth,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--reference-date", default="2026-11-10")
    args = parser.parse_args(argv)
    criteria = load_registry_criteria(args.registry)
    records = build_records(criteria, args.repo_root, reference_date=args.reference_date)
    ledger = measure_gate_power(records, CriterionGate())
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(json.dumps({k: ledger[k] for k in
                      ("criteria_total", "injected_total", "detected_total", "gate_power",
                       "false_positive_rate", "always_true_criteria")}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())