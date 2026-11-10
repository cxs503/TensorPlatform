"""Evidence-chain validator for automatic capability-ledger promotion (p262w1-N07).

The ledger only promotes a capability to ``REAL`` when a *complete* evidence chain
exists, and demotes it again when that chain breaks.  A chain has five links:

============  ===========================================================
link          machine-checkable requirement
============  ===========================================================
artifact      ``<verified>/<capability_id>/artifact.json`` exists and carries a
              content digest of the inputs that produced the numbers
pytest        a pytest summary (junit-xml or json) with ``passed >= N`` and
              ``failed <= M``
report        ``report.json`` carries every required element (the six reporting
              elements) with non-empty content
error         ``metrics.json`` holds raw ``measured``/``reference`` numbers plus a
              *claimed* error; the claim is recomputed from the raw numbers by the
              platform metric gate and must agree, and the recomputed error must
              stay below the tolerance
review        ``review.json`` records an independent review whose verdict is a
              pass value
============  ===========================================================

Zero domain semantics: element names, file names, thresholds, pass vocabularies
and the error expression are all injected through :class:`ChainSpec` (JSON
configurable).  Capability ids are opaque strings -- this module never knows what
a capability *means*.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LEVELS: Tuple[str, ...] = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")

#: ``failure_kind`` values -- they decide *which* level a broken chain degrades to.
FAILURE_EVIDENCE = "evidence"      # chain link missing / unreadable -> WITHHELD
FAILURE_REGRESSION = "regression"  # numbers or tests disprove the claim -> MOCK


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every digest in this package."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_file(path: "str | Path") -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChainSpec:
    """Machine-readable definition of "evidence chain complete".

    Every field is data, never a domain constant: the same engine audits any
    capability registry by swapping the spec.
    """

    min_pytest_passed: int = 3
    max_pytest_failed: int = 0
    max_pytest_errors: int = 0
    required_report_elements: Tuple[str, ...] = (
        "case",
        "method",
        "results",
        "figures",
        "notes",
        "error_judgement",
    )
    max_rel_error: float = 0.03
    claim_tol: float = 1e-9
    error_expression: str = "abs(measured - reference) / abs(reference)"
    error_metric_name: str = "rel_error"
    require_independent_review: bool = True
    review_pass_values: Tuple[str, ...] = ("PASS", "APPROVED", "ACCEPTED")
    artifact_filename: str = "artifact.json"
    report_filename: str = "report.json"
    metrics_filename: str = "metrics.json"
    review_filename: str = "review.json"
    pytest_xml_filename: str = "pytest.xml"
    pytest_json_filename: str = "pytest.json"
    default_level: str = "WITHHELD"

    def __post_init__(self) -> None:  # pragma: no cover - validation only
        if self.max_rel_error < 0:
            raise ValueError("max_rel_error must be >= 0")
        if self.default_level not in LEVELS:
            raise ValueError(f"default_level must be one of {LEVELS}")

    # -- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = {
            "min_pytest_passed": self.min_pytest_passed,
            "max_pytest_failed": self.max_pytest_failed,
            "max_pytest_errors": self.max_pytest_errors,
            "required_report_elements": list(self.required_report_elements),
            "max_rel_error": self.max_rel_error,
            "claim_tol": self.claim_tol,
            "error_expression": self.error_expression,
            "error_metric_name": self.error_metric_name,
            "require_independent_review": self.require_independent_review,
            "review_pass_values": list(self.review_pass_values),
            "artifact_filename": self.artifact_filename,
            "report_filename": self.report_filename,
            "metrics_filename": self.metrics_filename,
            "review_filename": self.review_filename,
            "pytest_xml_filename": self.pytest_xml_filename,
            "pytest_json_filename": self.pytest_json_filename,
            "default_level": self.default_level,
        }
        return d

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "ChainSpec":
        if not data:
            return cls()
        known = {f for f in cls().to_dict()}
        kwargs = {k: v for k, v in data.items() if k in known}
        for key in ("required_report_elements", "review_pass_values"):
            if key in kwargs:
                kwargs[key] = tuple(kwargs[key])
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: "str | Path") -> "ChainSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def digest(self) -> str:
        return sha256_json(self.to_dict())


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def parse_junit_xml(text: str) -> Dict[str, int]:
    """Count passed/failed/error/skipped from a junit-xml report.

    Both ``<testsuite>`` aggregates and bare ``<testcase>`` lists are accepted;
    the counters are derived from the test cases themselves so a lying
    ``testsuites`` header cannot inflate the pass count.
    """
    root = ET.fromstring(text)
    cases = list(root.iter("testcase"))
    failed = errors = skipped = 0
    for case in cases:
        tags = {child.tag for child in case}
        if "failure" in tags:
            failed += 1
        elif "error" in tags:
            errors += 1
        elif "skipped" in tags:
            skipped += 1
    total = len(cases)
    return {
        "total": total,
        "passed": total - failed - errors - skipped,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
    }


def parse_pytest_json(payload: Mapping[str, Any]) -> Dict[str, int]:
    """Normalise a pytest-style summary dict."""
    def _int(*keys: str) -> int:
        for key in keys:
            if key in payload:
                try:
                    return int(payload[key])
                except (TypeError, ValueError):
                    return 0
        return 0

    passed = _int("passed")
    failed = _int("failed")
    errors = _int("errors", "error")
    skipped = _int("skipped")
    total = _int("total", "collected")
    if total == 0:
        total = passed + failed + errors + skipped
    return {"total": total, "passed": passed, "failed": failed,
            "errors": errors, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Evidence discovery
# --------------------------------------------------------------------------- #
@dataclass
class DiscoveredEvidence:
    """Raw material found on disk for one capability."""

    capability_id: str
    artifact_dir: Optional[Path] = None
    artifact: Optional[Dict[str, Any]] = None
    pytest_summary: Optional[Dict[str, int]] = None
    pytest_source: str = ""
    report: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    review: Optional[Dict[str, Any]] = None
    load_errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir else None,
            "artifact_present": self.artifact is not None,
            "pytest_summary": self.pytest_summary,
            "pytest_source": self.pytest_source,
            "report_present": self.report is not None,
            "metrics_present": self.metrics is not None,
            "review_present": self.review is not None,
            "load_errors": list(self.load_errors),
        }


def _read_json(path: Path, errors: List[str]) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        return None


def load_evidence(verified_dir: "str | Path", capability_id: str,
                  spec: ChainSpec | None = None) -> DiscoveredEvidence:
    """Read everything the chain needs for ``capability_id`` from ``verified_dir``."""
    spec = spec or ChainSpec()
    base = Path(verified_dir) / capability_id
    ev = DiscoveredEvidence(capability_id=capability_id, artifact_dir=base)
    if not base.is_dir():
        ev.load_errors.append(f"missing directory: {capability_id}")
        return ev

    ev.artifact = _read_json(base / spec.artifact_filename, ev.load_errors)
    ev.report = _read_json(base / spec.report_filename, ev.load_errors)
    ev.metrics = _read_json(base / spec.metrics_filename, ev.load_errors)
    ev.review = _read_json(base / spec.review_filename, ev.load_errors)

    xml_path = base / spec.pytest_xml_filename
    json_path = base / spec.pytest_json_filename
    if xml_path.is_file():
        try:
            ev.pytest_summary = parse_junit_xml(xml_path.read_text(encoding="utf-8"))
            ev.pytest_source = spec.pytest_xml_filename
        except (ET.ParseError, OSError) as exc:
            ev.load_errors.append(f"{spec.pytest_xml_filename}: {exc}")
    elif json_path.is_file():
        payload = _read_json(json_path, ev.load_errors)
        if payload is not None:
            ev.pytest_summary = parse_pytest_json(payload)
            ev.pytest_source = spec.pytest_json_filename
    return ev


# --------------------------------------------------------------------------- #
# Error recompute (delegates to the platform metric gate)
# --------------------------------------------------------------------------- #
class ErrorGate:
    """Recompute the claimed error from raw numbers; never trust the hand-written value.

    Wraps :class:`tools.metric_gate.MetricGate` when importable (that is the
    platform's write-time recompute gate, p262w1-N06).  If it is unavailable the
    expression is evaluated by :meth:`_inline_eval` and the evidence records
    ``gate_source="inline"`` so the weaker provenance is visible.
    """

    def __init__(self, spec: ChainSpec):
        self.spec = spec
        self.source = "inline"
        self._gate = None
        try:  # pragma: no cover - import depends on sys.path layout
            from tools.metric_gate import ExprRegistry, MetricGate

            registry = ExprRegistry()
            registry.register(spec.error_metric_name, spec.error_expression,
                              allowed_vars={"measured", "reference"})
            self._gate = MetricGate(registry, default_tol=spec.claim_tol)
            self.source = "metric_gate"
        except Exception:  # pragma: no cover
            self._gate = None

    def evaluate(self, measured: float, reference: float) -> Dict[str, Any]:
        row = {"measured": float(measured), "reference": float(reference)}
        if self._gate is not None:
            result = self._gate.check(self.spec.error_metric_name, 0.0, row)
            recomputed = result.recomputed_value
            if recomputed is None:  # pragma: no cover - expression failure
                recomputed = self._inline_eval(**row)
            return {"value": float(recomputed), "source": self.source, "row": row}
        return {"value": self._inline_eval(**row), "source": self.source, "row": row}

    def check_claim(self, claimed: float, measured: float, reference: float) -> Dict[str, Any]:
        """Verify the claimed error equals the recomputed one within ``claim_tol``."""
        row = {"measured": float(measured), "reference": float(reference)}
        if self._gate is not None:
            result = self._gate.check(self.spec.error_metric_name, float(claimed), row)
            return {
                "accepted": bool(result.accepted),
                "claimed": float(claimed),
                "recomputed": result.recomputed_value,
                "diff": result.diff,
                "tolerance": result.tolerance,
                "source": self.source,
            }
        recomputed = self._inline_eval(**row)
        diff = abs(float(claimed) - recomputed)
        return {"accepted": diff <= self.spec.claim_tol, "claimed": float(claimed),
                "recomputed": recomputed, "diff": diff,
                "tolerance": self.spec.claim_tol, "source": self.source}

    def _inline_eval(self, measured: float, reference: float) -> float:
        # same semantics as the default error_expression, but only usable for that
        # one expression -- recorded as gate_source="inline".
        if reference == 0:
            raise ZeroDivisionError("reference is zero")
        return abs(float(measured) - float(reference)) / abs(float(reference))


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    numbers: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "numbers": self.numbers}


CHECK_NAMES: Tuple[str, ...] = ("artifact", "pytest", "report", "error", "review")


@dataclass
class ChainVerdict:
    capability_id: str
    complete: bool
    checks: List[CheckResult]
    missing: List[str] = field(default_factory=list)
    failure_kind: Optional[str] = None
    reason_code: str = ""
    chain_digest: str = ""
    spec_digest: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "complete": self.complete,
            "missing": list(self.missing),
            "failure_kind": self.failure_kind,
            "reason_code": self.reason_code,
            "chain_digest": self.chain_digest,
            "spec_digest": self.spec_digest,
            "checks": [c.as_dict() for c in self.checks],
            "evidence": self.evidence,
        }


class EvidenceChainValidator:
    """Turn discovered evidence into a machine-checkable chain verdict."""

    def __init__(self, spec: ChainSpec | None = None, error_gate: ErrorGate | None = None):
        self.spec = spec or ChainSpec()
        self.error_gate = error_gate or ErrorGate(self.spec)

    # -- individual links ----------------------------------------------------
    def _check_artifact(self, ev: DiscoveredEvidence) -> CheckResult:
        art = ev.artifact
        if art is None:
            return CheckResult("artifact", False, "artifact manifest absent")
        digest = art.get("inputs_sha256") or art.get("digest") or art.get("inputs_digest")
        if not digest or not isinstance(digest, str):
            return CheckResult("artifact", False, "artifact manifest carries no input digest")
        return CheckResult("artifact", True, "artifact manifest with input digest",
                           {"inputs_sha256": digest, "case_id": art.get("case_id", "")})

    def _check_pytest(self, ev: DiscoveredEvidence) -> CheckResult:
        summary = ev.pytest_summary
        if summary is None:
            return CheckResult("pytest", False, "no pytest summary found")
        spec = self.spec
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
        errors = int(summary.get("errors", 0))
        ok = (passed >= spec.min_pytest_passed and failed <= spec.max_pytest_failed
              and errors <= spec.max_pytest_errors)
        detail = (f"passed={passed} (>= {spec.min_pytest_passed}), "
                  f"failed={failed} (<= {spec.max_pytest_failed}), errors={errors}")
        numbers = dict(summary)
        numbers.update({"passed": passed, "failed": failed, "errors": errors,
                        "source": ev.pytest_source})
        return CheckResult("pytest", ok, detail, numbers)

    def _check_report(self, ev: DiscoveredEvidence) -> CheckResult:
        report = ev.report
        if report is None:
            return CheckResult("report", False, "report.json absent")
        missing = [k for k in self.spec.required_report_elements
                   if not _non_empty(report.get(k))]
        numbers = {"elements_present": len(self.spec.required_report_elements) - len(missing),
                   "elements_required": len(self.spec.required_report_elements),
                   "missing_elements": missing}
        if missing:
            return CheckResult("report", False,
                               "report.json missing/empty elements: " + ", ".join(missing),
                               numbers)
        return CheckResult("report", True, "all six reporting elements present", numbers)

    def _check_error(self, ev: DiscoveredEvidence) -> CheckResult:
        metrics = ev.metrics
        if metrics is None:
            return CheckResult("error", False, "metrics.json absent")
        try:
            measured = float(metrics["measured"])
            reference = float(metrics["reference"])
        except (KeyError, TypeError, ValueError):
            return CheckResult("error", False, "metrics.json lacks numeric measured/reference",
                               {"keys": sorted(metrics)})
        claimed = metrics.get("rel_error")
        try:
            recomputed = self.error_gate.evaluate(measured, reference)
        except ZeroDivisionError:
            return CheckResult("error", False, "reference value is zero",
                               {"measured": measured, "reference": reference})
        value = float(recomputed["value"])
        numbers: Dict[str, Any] = {
            "measured": measured, "reference": reference,
            "rel_error": value, "max_rel_error": self.spec.max_rel_error,
            "gate_source": recomputed["source"],
        }
        if claimed is None:
            numbers["claim"] = None
            return CheckResult("error", False,
                               "metrics.json carries no claimed rel_error (not recomputable)",
                               numbers)
        try:
            claim = float(claimed)
        except (TypeError, ValueError):
            numbers["claim"] = claimed
            return CheckResult("error", False, "claimed rel_error is not numeric", numbers)
        claim_check = self.error_gate.check_claim(claim, measured, reference)
        numbers.update({"claim": claim, "claim_accepted": claim_check["accepted"],
                        "claim_diff": claim_check["diff"],
                        "claim_tolerance": claim_check["tolerance"]})
        if not claim_check["accepted"]:
            return CheckResult("error", False,
                               f"claimed rel_error {claim:.6g} != recomputed {value:.6g}",
                               numbers)
        if value > self.spec.max_rel_error:
            return CheckResult("error", False,
                               f"rel_error {value:.6g} > tolerance {self.spec.max_rel_error:.6g}",
                               numbers)
        return CheckResult("error", True,
                           f"rel_error {value:.6g} <= {self.spec.max_rel_error:.6g} (recomputed)",
                           numbers)

    def _check_review(self, ev: DiscoveredEvidence) -> CheckResult:
        review = ev.review
        if review is None:
            return CheckResult("review", False, "review.json absent")
        verdict = str(review.get("verdict", "")).upper()
        independent = bool(review.get("independent", False))
        numbers = {"verdict": verdict, "independent": independent,
                   "reviewer": review.get("reviewer", "")}
        allowed = {v.upper() for v in self.spec.review_pass_values}
        if self.spec.require_independent_review and not independent:
            return CheckResult("review", False, "review is not marked independent", numbers)
        if verdict not in allowed:
            return CheckResult("review", False,
                               f"review verdict {verdict or '<empty>'} not in {sorted(allowed)}",
                               numbers)
        return CheckResult("review", True, f"independent review verdict {verdict}", numbers)

    # -- aggregation ---------------------------------------------------------
    def validate(self, ev: DiscoveredEvidence) -> ChainVerdict:
        checks = [
            self._check_artifact(ev),
            self._check_pytest(ev),
            self._check_report(ev),
            self._check_error(ev),
            self._check_review(ev),
        ]
        missing = [c.name for c in checks if not c.ok]
        complete = not missing
        failure_kind = None
        reason_code = "CHAIN_COMPLETE"
        if not complete:
            reason_code = _reason_code(checks)
            failure_kind = _failure_kind(checks)
        evidence = {
            "discovered": ev.as_dict(),
            "spec": self.spec.to_dict(),
        }
        chain_digest = sha256_json({
            "capability_id": ev.capability_id,
            "complete": complete,
            "checks": [c.as_dict() for c in checks],
            "spec_digest": self.spec.digest(),
        })
        return ChainVerdict(capability_id=ev.capability_id, complete=complete,
                            checks=checks, missing=missing,
                            failure_kind=failure_kind, reason_code=reason_code,
                            chain_digest=chain_digest, spec_digest=self.spec.digest(),
                            evidence=evidence)


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


_REASON_BY_CHECK = {
    "artifact": "MISSING_ARTIFACT",
    "pytest": "PYTEST_BELOW_THRESHOLD",
    "report": "REPORT_INCOMPLETE",
    "error": "ERROR_GATE_FAILED",
    "review": "REVIEW_NOT_PASSED",
}


def _reason_code(checks: Sequence[CheckResult]) -> str:
    for check in checks:
        if not check.ok and check.name in _REASON_BY_CHECK:
            # a failing pytest run is a regression, a missing one is not
            if check.name == "pytest" and check.numbers.get("source") == "":
                return "MISSING_PYTEST_EVIDENCE"
            return _REASON_BY_CHECK[check.name]
    return "CHAIN_INCOMPLETE"


def _failure_kind(checks: Sequence[CheckResult]) -> str:
    """Regression = the numbers/tests disprove the claim; otherwise evidence gap."""
    for check in checks:
        if check.ok:
            continue
        if check.name == "pytest":
            # only *failing* tests disprove the claim; too few tests is an evidence gap
            if int(check.numbers.get("failed", 0)) > 0 or int(check.numbers.get("errors", 0)) > 0:
                return FAILURE_REGRESSION
            return FAILURE_EVIDENCE
        if check.name == "error":
            if check.numbers.get("claim") is not None:
                return FAILURE_REGRESSION
            return FAILURE_EVIDENCE
    return FAILURE_EVIDENCE