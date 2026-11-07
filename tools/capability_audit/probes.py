"""Probe engines: invariance, sensitivity, analytic and monotonicity knives.

Every check returns a :class:`Check` record with the numbers that produced the
verdict, so the audit report can be regenerated from machine data only (no
hand-copied values) and so a failed check can be argued with.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import adapters, benchmark, oracle
from .transport import TransportError

FAMILIES = ("invariance", "sensitivity", "analytic", "monotonic", "benchmark")


@dataclass
class Check:
    family: str
    name: str
    cap_id: str
    passed: Optional[bool]
    detail: str = ""
    rel_error: Optional[float] = None
    tol: Optional[float] = None
    observed: Any = None
    expected: Any = None
    fatal: bool = True
    note: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def flatten(obj: Any, prefix: str = "") -> Dict[str, float]:
    """Flatten a JSON response into {path: float} for numeric comparison."""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = f"{prefix}{key}"
            out.update(flatten(val, path + "."))
    elif isinstance(obj, (list, tuple)):
        for idx, val in enumerate(obj):
            out.update(flatten(val, f"{prefix.rstrip('.')}[{idx}]."))
    elif isinstance(obj, bool):
        out[prefix.rstrip(".")] = float(obj)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return out
        out[prefix.rstrip(".")] = float(obj)
    return out


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-12)


def _common_paths(a: Dict[str, float], b: Dict[str, float]) -> List[str]:
    return sorted(set(a) & set(b))


def _worst_diff(base: Dict[str, float], other: Dict[str, float],
                fields: Optional[Sequence[str]]) -> tuple:
    """Return (worst_rel, path, (base, variant), n_compared, missing_fields).

    ``n_compared == 0`` means the check proved nothing and must *not* be
    reported as a pass (a silently empty comparison was an early false-pass
    pathway in this tool).
    """
    paths = list(fields) if fields else _common_paths(base, other)
    worst, worst_path, worst_pair = 0.0, None, (None, None)
    compared, missing = 0, []
    for path in paths:
        if path not in base or path not in other:
            missing.append(path)
            continue
        compared += 1
        err = _rel(base[path], other[path])
        if worst_path is None or err > worst:
            worst, worst_path, worst_pair = err, path, (base[path], other[path])
    return worst, worst_path, worst_pair, compared, missing


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
def run_invariance(client, spec: adapters.CapabilitySpec, test: adapters.InvarianceTest,
                   base_response: Any) -> Check:
    base_flat = flatten(base_response)
    try:
        variant = client.post(spec.endpoint, test.transform(spec.base_input))
    except TransportError as exc:
        return Check("invariance", test.name, spec.cap_id, None, detail=str(exc), note=test.note)
    variant_flat = flatten(variant)
    if test.expected_scale and test.expected_scale != 1.0:
        variant_flat = {k: v / test.expected_scale for k, v in variant_flat.items()}
    worst, path, pair, compared, missing = _worst_diff(base_flat, variant_flat,
                                                        test.compare_fields)
    if compared == 0:
        return Check("invariance", test.name, spec.cap_id, None,
                     detail=f"no comparable numeric field (missing {missing})",
                     note=test.note, data={"missing": missing})
    passed = worst <= test.tol
    detail = (f"{compared} field(s) compared, max relative deviation {worst:.3e} on '{path}' "
              f"({pair[0]!r} vs {pair[1]!r}); tolerance {test.tol:.1e}")
    if missing:
        detail += f"; missing {missing}"
    return Check("invariance", test.name, spec.cap_id, passed, detail=detail,
                 rel_error=worst, tol=test.tol, observed=pair[1], expected=pair[0],
                 note=test.note,
                 data={"path": path, "base": pair[0], "variant": pair[1],
                       "fields_compared": compared})


def run_sensitivity(client, spec: adapters.CapabilitySpec, test: adapters.SensitivityTest,
                    base_response: Any) -> Check:
    base_flat = flatten(base_response)
    if test.field not in base_flat:
        return Check("sensitivity", test.name, spec.cap_id, None,
                     detail=f"field '{test.field}' absent from response", note=test.note)
    try:
        variant = client.post(spec.endpoint, test.transform(spec.base_input))
    except TransportError as exc:
        return Check("sensitivity", test.name, spec.cap_id, None, detail=str(exc), note=test.note)
    var_flat = flatten(variant)
    if test.field not in var_flat:
        return Check("sensitivity", test.name, spec.cap_id, None,
                     detail=f"field '{test.field}' absent from perturbed response", note=test.note)
    b, v = base_flat[test.field], var_flat[test.field]
    rel_change = (v - b) / max(abs(b), 1e-12)
    direction_ok = test.direction == 0 or (rel_change > 0) == (test.direction > 0)
    passed = bool(direction_ok and abs(rel_change) >= test.min_rel_change)
    detail = (f"'{test.field}': {b:g} -> {v:g} ({rel_change:+.3%}); "
              f"required |change| >= {test.min_rel_change:.1%} "
              f"and sign {'+' if test.direction > 0 else '-' if test.direction < 0 else 'either'}"
              + ("" if direction_ok else " (WRONG SIGN)"))
    return Check("sensitivity", test.name, spec.cap_id, passed, detail=detail,
                 rel_error=abs(rel_change), tol=test.min_rel_change,
                 observed=v, expected=b, note=test.note,
                 data={"base": b, "variant": v, "rel_change": rel_change})


def run_analytic(client, spec: adapters.CapabilitySpec, test: adapters.AnalyticTest,
                 base_response: Any) -> Check:
    try:
        payload = spec.base_input
        expected_block = test.check(payload, base_response)
    except (KeyError, TypeError, ValueError) as exc:
        return Check("analytic", test.name, spec.cap_id, None,
                     detail=f"cannot evaluate: {exc}", note=test.note)
    obs = expected_block["observed"]
    exp = expected_block["expected"]
    err = oracle.rel_error(float(obs), float(exp)) if test.tol > 0 else float(exp) - float(obs)
    if test.tol > 0:
        passed = err <= test.tol
        detail = (f"{expected_block['detail']}: observed {obs:.6g}, reference {exp:.6g}, "
                  f"relative error {err:.3e} (tol {test.tol:.1e})")
    else:  # margin-style check (positive means the bound is respected)
        passed = float(exp) - float(obs) >= 0.0
        detail = f"{expected_block['detail']}"
    return Check("analytic", test.name, spec.cap_id, passed, detail=detail,
                 rel_error=err, tol=test.tol, observed=obs, expected=exp,
                 fatal=test.fatal, note=test.note)


def run_monotonic(client, spec: adapters.CapabilitySpec, test: adapters.MonotonicTest) -> Check:
    xs: List[float] = []
    ys: List[float] = []
    for value in test.values:
        try:
            resp = client.post(spec.endpoint, test.transform(float(value)))
        except TransportError as exc:
            return Check("monotonic", test.name, spec.cap_id, None,
                         detail=str(exc), note=test.note)
        flat = flatten(resp)
        if test.field not in flat and f"[0].{test.field}" in flat:
            flat = dict(flat, **{test.field: flat[f"[0].{test.field}"]})
        if test.field not in flat:
            return Check("monotonic", test.name, spec.cap_id, None,
                         detail=f"field '{test.field}' absent", note=test.note)
        xs.append(float(value))
        ys.append(flat[test.field])
    violations: List[str] = []
    for i in range(len(ys) - 1):
        step = (ys[i + 1] - ys[i]) / max(abs(ys[i]), 1e-12)
        if test.direction > 0 and step < test.min_rel_step:
            violations.append(f"x={xs[i]:g}->{xs[i+1]:g}: {ys[i]:g}->{ys[i+1]:g} ({step:+.3%})")
        elif test.direction < 0 and -step < test.min_rel_step:
            violations.append(f"x={xs[i]:g}->{xs[i+1]:g}: {ys[i]:g}->{ys[i+1]:g} ({step:+.3%})")
        elif test.direction == 0 and abs(step) < test.min_rel_step:
            violations.append(f"x={xs[i]:g}->{xs[i+1]:g}: {ys[i]:g}->{ys[i+1]:g} ({step:+.3%})")
    passed = not violations
    span = (ys[-1] - ys[0]) / max(abs(ys[0]), 1e-12) if ys else 0.0
    detail = (f"'{test.field}' over {test.field} sweep: total change {span:+.2%}; "
              + ("monotone as required" if passed else "violations: " + "; ".join(violations[:3])))
    return Check("monotonic", test.name, spec.cap_id, passed, detail=detail,
                 rel_error=None, tol=test.min_rel_step,
                 observed=ys[-1] if ys else None, expected=ys[0] if ys else None,
                 note=test.note,
                 data={"x": xs, "y": ys, "field": test.field,
                       "direction": test.direction, "violations": violations})


# --------------------------------------------------------------------------
# capability driver
# --------------------------------------------------------------------------
BENCHMARK_TOL = 0.15          # mission standard: >15% vs a reference is unacceptable


def run_benchmark(client, spec: adapters.CapabilitySpec, case: Dict[str, Any]) -> Check:
    """Compare the endpoint against one row of backend/data/benchmark_reference.json."""
    case_id = case["case_id"]
    if not case.get("adjudicable"):
        return Check("benchmark", case_id, spec.cap_id, None,
                     detail=f"WITHHELD per corpus policy — {case['reason']}",
                     note=case.get("source", ""),
                     data={"reference_status": case.get("reference_status"),
                           "reference_ct": case.get("reference_ct"),
                           "froude_number": case.get("froude_number")})
    try:
        resp = client.post(spec.endpoint, case["payload"])
    except TransportError as exc:
        return Check("benchmark", case_id, spec.cap_id, None, detail=str(exc))
    flat = flatten(resp)
    ct_model = flat.get("ct")
    if ct_model is None:
        return Check("benchmark", case_id, spec.cap_id, None,
                     detail="endpoint returned no C_T for the reference geometry")
    ct_ref = float(case["reference_ct"])
    err = oracle.rel_error(ct_model, ct_ref)
    s_model = flat.get("wetted_surface")
    detail = (f"Fr={case['froude_number']}: C_T model {ct_model:.4g} vs corpus {ct_ref:.4g}, "
              f"relative error {err:+.2%} (tol {BENCHMARK_TOL:.0%})")
    if s_model and case.get("reference_surface_m2"):
        detail += (f"; wetted surface {s_model:.3f} vs corpus "
                   f"{case['reference_surface_m2']:.3f} m² "
                   f"({oracle.rel_error(s_model, case['reference_surface_m2']):+.1%})")
    # Non-fatal by design: a real algorithm can be inaccurate.  Mechanism
    # (REAL/MOCK) and accuracy (benchmark vs reference) are reported separately.
    return Check("benchmark", case_id, spec.cap_id, err <= BENCHMARK_TOL, detail=detail,
                 rel_error=err, tol=BENCHMARK_TOL, observed=ct_model, expected=ct_ref,
                 fatal=False, note=case.get("source", ""),
                 data={"froude_number": case.get("froude_number"),
                       "reference_status": case.get("reference_status"),
                       "reference_ct": ct_ref, "model_ct": ct_model,
                       "reference_rt_n": case.get("reference_rt_n"),
                       "reference_surface_m2": case.get("reference_surface_m2"),
                       "model_surface_m2": s_model})


def audit_capability(client, spec: adapters.CapabilitySpec) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "cap_id": spec.cap_id,
        "endpoint": spec.endpoint,
        "domain": spec.domain,
        "claimed_level": spec.claimed_level,
        "claim_source": spec.claim_source,
        "notes": list(spec.notes),
        "checks": [],
    }
    try:
        base_response = client.post(spec.endpoint, spec.base_input)
    except TransportError as exc:
        result.update(status="WITHHELD", observed_level="WITHHELD",
                      reasons=[f"endpoint unreachable: {exc}"], base_response=None)
        return result
    result["base_response"] = base_response
    checks: List[Check] = []
    for test in spec.invariances:
        checks.append(run_invariance(client, spec, test, base_response))
    for test in spec.sensitivities:
        checks.append(run_sensitivity(client, spec, test, base_response))
    for test in spec.analytics:
        checks.append(run_analytic(client, spec, test, base_response))
    for test in spec.monotonics:
        checks.append(run_monotonic(client, spec, test))
    for case in spec.benchmarks:
        checks.append(run_benchmark(client, spec, case))

    executed = [c for c in checks if c.passed is not None]
    failed = [c for c in executed if c.passed is False and c.fatal]
    counts = {fam: {"total": 0, "passed": 0, "failed": 0} for fam in FAMILIES}
    for c in checks:
        counts[c.family]["total"] += 1
        if c.passed is True:
            counts[c.family]["passed"] += 1
        elif c.passed is False:
            counts[c.family]["failed"] += 1

    if not executed:
        observed_level, status = "WITHHELD", "WITHHELD"
        reasons = ["no probe could be evaluated"]
    elif failed:
        observed_level, status = "MOCK", "FAIL"
        reasons = [f"{c.family}:{c.name} -> {c.detail}" for c in failed]
    else:
        observed_level, status = "REAL", "PASS"
        reasons = ["all fatal probes passed"]

    bench_exec = [c for c in checks if c.family == "benchmark" and c.passed is not None]
    if not bench_exec:
        accuracy_level = "NOT_TESTED" if not any(
            c.family == "benchmark" for c in checks) else "WITHHELD"
    elif any(c.passed is False for c in bench_exec):
        accuracy_level = "FAIL"
    else:
        accuracy_level = "PASS"

    result.update(
        checks=[c.to_dict() for c in checks],
        accuracy_level=accuracy_level,
        accuracy_overclaim=(spec.claimed_level == "REAL" and accuracy_level == "FAIL"),
        counts=counts,
        status=status,
        observed_level=observed_level,
        overclaim=(spec.claimed_level == "REAL" and observed_level != "REAL"),
        proven_real=(observed_level == "REAL"),
        reasons=reasons,
    )
    return result


def audit_all(specs: Sequence[adapters.CapabilitySpec], client,
              api_base: str = "", reachable: bool = True,
              reachability_note: str = "") -> Dict[str, Any]:
    caps = [audit_capability(client, spec) for spec in specs]
    criteria = {
        "capabilities_audited": len(caps),
        "invariance_covered": sum(1 for c in caps
                                  if c["counts"]["invariance"]["total"] > 0),
        "analytic_covered": sum(1 for c in caps
                                if c["counts"]["analytic"]["passed"] > 0
                                or c["counts"]["analytic"]["failed"] > 0),
        "monotonic_covered": sum(1 for c in caps
                                 if c["counts"]["monotonic"]["total"] > 0),
        "checks_total": sum(len(c["checks"]) for c in caps),
        "checks_passed": sum(1 for c in caps for ch in c["checks"] if ch["passed"] is True),
        "checks_failed": sum(1 for c in caps for ch in c["checks"] if ch["passed"] is False),
        "checks_skipped": sum(1 for c in caps for ch in c["checks"] if ch["passed"] is None),
        "caps_proven_real": sum(1 for c in caps if c["proven_real"]),
        "overclaims": [c["cap_id"] for c in caps if c["overclaim"]],
        "benchmark_compared": sum(1 for c in caps for ch in c["checks"]
                                  if ch["family"] == "benchmark" and ch["passed"] is not None),
        "benchmark_passed": sum(1 for c in caps for ch in c["checks"]
                                if ch["family"] == "benchmark" and ch["passed"] is True),
        "benchmark_failed": sum(1 for c in caps for ch in c["checks"]
                                if ch["family"] == "benchmark" and ch["passed"] is False),
        "benchmark_withheld": sum(1 for c in caps for ch in c["checks"]
                                  if ch["family"] == "benchmark" and ch["passed"] is None),
        "accuracy_verdicts": {c["cap_id"]: c["accuracy_level"] for c in caps
                              if c["accuracy_level"] != "NOT_TESTED"},
        "accuracy_overclaims": [c["cap_id"] for c in caps if c.get("accuracy_overclaim")],
        "api_reachable": reachable,
        "api_base": api_base,
        "reachability_note": reachability_note,
    }
    return {"caps": caps, "criteria": criteria}