"""Generic probe contracts (zero domain semantics).

Each probe answers one question about an *observable*:

===============  =====================================================================
kind             question
===============  =====================================================================
anchor           does the observable match an independently derived value?
scale_law        is the observable homogeneous in an input (exponent declared in data)?
monotonic        does the observable move in the declared direction?
nontriviality    does the observable actually vary across distinct inputs?
determinism      does the same input give the same observable twice?
bounded          does the observable stay inside a declared interval?
===============  =====================================================================

Only ``anchor``/``scale_law``/``bounded`` carry expectations, and all of them come from the
registry file (data). ``nontriviality``/``scale_law``/``monotonic`` are the
*variation-sensitive* probes: they are what a hardcoded or constant stub cannot fake.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional

from .model import Check, ProbeSpec

Call = Callable[[Optional[Dict[str, Any]]], Any]

DEFAULT_TOL_REL = 0.02


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rel_err(observed: Any, expected: Any, atol: float = 0.0) -> Optional[float]:
    if not _is_number(observed) or not _is_number(expected):
        return None
    denom = abs(float(expected))
    diff = abs(float(observed) - float(expected))
    if denom <= 1e-15:
        return 0.0 if diff <= max(atol, 0.0) else math.inf
    return diff / denom


def _cases(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    cases = params.get("cases")
    if isinstance(cases, list) and cases:
        out = []
        for case in cases:
            kwargs = dict(case.get("kwargs") or {})
            out.append({**case, "kwargs": kwargs})
        return out
    return [{"kwargs": dict(params.get("kwargs") or {}), **{k: params[k] for k in ("expect", "tol_rel", "atol") if k in params}}]


def probe_anchor(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    tol_default = float(params.get("tol_rel", DEFAULT_TOL_REL))
    rows: List[Dict[str, Any]] = []
    worst: Optional[float] = None
    ok = True
    for case in _cases(params):
        expect = case.get("expect")
        atol = float(case.get("atol", params.get("atol", 0.0)) or 0.0)
        tol = float(case.get("tol_rel", tol_default))
        observed = call(case.get("kwargs") or {})
        err = _rel_err(observed, expect, atol)
        if err is None:
            passed = observed == expect
            err_display = None
        else:
            passed = err <= tol
            err_display = err
            worst = err if worst is None else max(worst, err)
        ok = ok and passed
        rows.append({"kwargs": case.get("kwargs"), "observed": observed, "expected": expect,
                     "rel_error": err_display, "tol": tol, "passed": passed})
    per_case_tol = float(params.get("tol_rel", DEFAULT_TOL_REL))
    return Check(capability_id=capability_id, kind="anchor", passed=ok, observed=rows,
                 expected="registry anchors", rel_error=worst, tol=per_case_tol, fatal=spec.fatal,
                 detail=f"{sum(1 for r in rows if r['passed'])}/{len(rows)} anchor case(s) within tolerance")


def probe_scale_law(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    base = dict(params.get("base") or {})
    axis = params.get("axis")
    if not isinstance(axis, str):
        return Check(capability_id=capability_id, kind="scale_law", passed=None, detail="missing 'axis'", fatal=spec.fatal)
    factor = float(params.get("factor", 2.0))
    exponent = float(params.get("exponent", 1.0))
    tol = float(params.get("tol_rel", DEFAULT_TOL_REL))
    base_value = float(base.get(axis, 1.0))
    kwargs_hi = dict(base)
    kwargs_hi[axis] = base_value * factor
    obs_lo = call(dict(base))
    obs_hi = call(kwargs_hi)
    expected_ratio = factor ** exponent
    if not (_is_number(obs_lo) and _is_number(obs_hi)) or abs(float(obs_lo)) <= 1e-15:
        return Check(capability_id=capability_id, kind="scale_law", passed=False, observed=[obs_lo, obs_hi],
                     expected=expected_ratio, tol=tol, fatal=spec.fatal,
                     detail="non-numeric or zero baseline observable")
    ratio = float(obs_hi) / float(obs_lo)
    err = abs(ratio / expected_ratio - 1.0)
    return Check(capability_id=capability_id, kind="scale_law", passed=err <= tol,
                 observed={"ratio": ratio, "low": obs_lo, "high": obs_hi}, expected=expected_ratio,
                 rel_error=err, tol=tol, fatal=spec.fatal,
                 detail=f"homogeneity in {axis!r}: observed ratio {ratio:.6g} vs {expected_ratio:.6g}")


def probe_monotonic(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    base = dict(params.get("base") or {})
    axis = params.get("axis")
    values = params.get("values")
    direction = str(params.get("direction", "increasing")).lower()
    tol = float(params.get("tol_rel", 1e-9))
    if not isinstance(axis, str) or not isinstance(values, list) or len(values) < 2:
        return Check(capability_id=capability_id, kind="monotonic", passed=None,
                     detail="need 'axis' and >=2 'values'", fatal=spec.fatal)
    obs: List[Any] = []
    for value in values:
        kwargs = dict(base)
        kwargs[axis] = value
        obs.append(call(kwargs))
    violations = []
    if not all(_is_number(v) for v in obs):
        return Check(capability_id=capability_id, kind="monotonic", passed=False, observed=obs,
                     detail="non-numeric observable", fatal=spec.fatal)
    scale = max(abs(float(v)) for v in obs) or 1.0
    eps = tol * scale
    for i in range(len(obs) - 1):
        a, b = float(obs[i]), float(obs[i + 1])
        if direction == "increasing" and b < a - eps:
            violations.append({"index": i, "from": a, "to": b})
        if direction == "decreasing" and b > a + eps:
            violations.append({"index": i, "from": a, "to": b})
    return Check(capability_id=capability_id, kind="monotonic", passed=not violations,
                 observed={"axis": axis, "values": values, "observables": obs},
                 expected=direction, rel_error=None, tol=tol, fatal=spec.fatal,
                 detail=f"{len(violations)} violation(s) of {direction} monotonicity")


def probe_nontriviality(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    cases = _cases(params)
    min_spread = float(params.get("min_rel_spread", DEFAULT_TOL_REL))
    obs = [call(case.get("kwargs") or {}) for case in cases]
    numeric = [float(v) for v in obs if _is_number(v)]
    if len(numeric) < 2:
        return Check(capability_id=capability_id, kind="nontriviality", passed=None, observed=obs,
                     detail="need >=2 distinct cases", fatal=spec.fatal)
    lo, hi = min(numeric), max(numeric)
    spread = (hi - lo) / (abs(hi) if abs(hi) > 1e-15 else 1.0)
    return Check(capability_id=capability_id, kind="nontriviality", passed=spread >= min_spread,
                 observed={"observables": obs, "spread": spread}, expected=f">= {min_spread}",
                 rel_error=spread, tol=min_spread, fatal=spec.fatal,
                 detail=f"relative spread {spread:.6g} across {len(numeric)} case(s)")


def probe_determinism(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    cases = _cases(params)
    tol_abs = float(params.get("tol_abs", 0.0))
    mismatches = []
    pairs = []
    for case in cases:
        first = call(case.get("kwargs") or {})
        second = call(case.get("kwargs") or {})
        pairs.append({"kwargs": case.get("kwargs"), "first": first, "second": second})
        if _is_number(first) and _is_number(second):
            if abs(float(first) - float(second)) > tol_abs:
                mismatches.append(pairs[-1])
        elif first != second:
            mismatches.append(pairs[-1])
    return Check(capability_id=capability_id, kind="determinism", passed=not mismatches, observed=pairs,
                 expected="repeat runs identical", rel_error=None, tol=tol_abs, fatal=spec.fatal,
                 detail=f"{len(mismatches)} mismatch(es) over {len(cases)} repeated case(s)")


def probe_bounded(call: Call, spec: ProbeSpec, capability_id: str) -> Check:
    params = spec.params
    lo = float(params.get("lo", 0.0))
    hi = float(params.get("hi", 1.0))
    rows = []
    ok = True
    for case in _cases(params):
        observed = call(case.get("kwargs") or {})
        if not _is_number(observed):
            ok = False
            rows.append({"kwargs": case.get("kwargs"), "observed": observed, "passed": False})
            continue
        inside = lo <= float(observed) <= hi
        ok = ok and inside
        rows.append({"kwargs": case.get("kwargs"), "observed": observed, "passed": inside})
    return Check(capability_id=capability_id, kind="bounded", passed=ok, observed=rows,
                 expected={"lo": lo, "hi": hi}, fatal=spec.fatal,
                 detail=f"{sum(1 for r in rows if r['passed'])}/{len(rows)} observation(s) inside [{lo}, {hi}]")


PROBE_TABLE: Dict[str, Callable[[Call, ProbeSpec, str], Check]] = {
    "anchor": probe_anchor,
    "scale_law": probe_scale_law,
    "monotonic": probe_monotonic,
    "nontriviality": probe_nontriviality,
    "determinism": probe_determinism,
    "bounded": probe_bounded,
}


def run_probe(spec: ProbeSpec, call: Call, capability_id: str) -> Check:
    fn = PROBE_TABLE.get(spec.kind)
    if fn is None:  # pragma: no cover - registry validation rejects unknown kinds
        return Check(capability_id=capability_id, kind=spec.kind, passed=None, detail="unknown probe kind",
                     fatal=spec.fatal)
    started = time.perf_counter()
    try:
        check = fn(call, spec, capability_id)
    except Exception as exc:  # noqa: BLE001 - a broken call chain is a failed probe
        check = Check(capability_id=capability_id, kind=spec.kind, passed=False,
                      detail=f"{type(exc).__name__}: {exc}", fatal=spec.fatal)
    check.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return check