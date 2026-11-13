"""Harness: turn a spec + the environment into an evidence ledger.

The only thing this module will ever report is what it observed.  Three
properties are load-bearing:

1. **Fail closed.** A class whose backing tool is absent yields ``WITHHELD`` and no
   metrics; an exception inside an adapter is ``FAIL`` with the exception type
   recorded -- never silently dropped, never retried into a green.
2. **Invariants decide, adapters observe.** The adapter reports numbers; the spec
   owns the thresholds.  An adapter that says ``REAL`` while an invariant fails is
   recorded ``FAIL`` (the run's claim is the weaker of the two).
3. **Two digests.** ``digest`` is a *structural* fingerprint (class ids + status +
   ``metric:ok`` flags) so two healthy environments agree; ``value_digest`` covers
   the observed numbers rounded to 12 significant digits, so a silent numeric
   change is visible.  Timings enter neither.

``coverage`` is the KPI-shaped answer: how many classes ended at ``REAL`` -- i.e.
probed *and* smoke-called *and* invariant-clean -- against the declared target.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import adapters as adapters_mod
from . import probe as probe_mod
from .spec import LEVEL_RANK  # noqa: F401  (re-exported for callers)
from .spec import SPEC_VERSION, Suite, ToolClass, weakest

STATUS_REAL = "REAL"
STATUS_MOCK = "MOCK"
STATUS_WITHHELD = "WITHHELD"
STATUS_FAIL = "FAIL"

# Gate vocabulary for ``verdict()``. ``PARTIAL`` is *not* a soft PASS: the KPI was
# never evaluated (a subset was requested), so a consumer must not read it as
# either "met" or "unmet".
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_PARTIAL = "PARTIAL"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _round(value: Any) -> Any:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return repr(value)
        # 16 significant digits: enough that a benchmark row's |ref-got| is not
        # dominated by the ledger's own quantization; still a rounding, and the
        # invariant decision itself is taken on the *unrounded* value.
        return float(f"{value:.16g}")
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


@dataclass
class ClassRecord:
    tool_id: str
    category: str
    adapter: str
    required: bool
    status: str
    probe: Dict[str, Any]
    metrics: Dict[str, Any]
    invariants: List[Dict[str, Any]]
    call: Dict[str, Any]
    detail: str
    duration_s: float

    def to_dict(self) -> Dict[str, Any]:
        return {"tool_id": self.tool_id, "category": self.category,
                "adapter": self.adapter, "required": self.required,
                "status": self.status, "probe": self.probe,
                "metrics": self.metrics, "invariants": self.invariants,
                "call": self.call, "detail": self.detail,
                "duration_s": round(self.duration_s, 6)}


def _check_invariants(cls: ToolClass, metrics: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for inv in cls.invariants:
        if inv.metric not in metrics:
            out.append({"metric": inv.metric, "limit": inv.limit, "op": inv.op,
                        "target": inv.target, "observed": None, "ok": False,
                        "error": "metric not reported by adapter"})
            continue
        observed = float(metrics[inv.metric])
        out.append({"metric": inv.metric, "limit": inv.limit, "op": inv.op,
                    "target": inv.target, "observed": observed,
                    "ok": bool(inv.check(observed))})
    return out


def run_class(cls: ToolClass, probe_result: Optional[probe_mod.ProbeResult] = None,
              ctx: Optional[Mapping[str, Any]] = None) -> ClassRecord:
    """Probe, smoke-call, grade.  Exactly one record, always."""

    t0 = time.perf_counter()
    pres = probe_result if probe_result is not None else probe_mod.probe(dict(cls.probe))
    base = {"tool_class": cls.tool_id, "category": cls.category, "adapter": cls.adapter,
            "probe": pres.to_dict(), "status": STATUS_WITHHELD, "metrics": {},
            "invariants": [], "call": {}, "detail": "", "required": cls.required}

    if not pres.available and cls.probe.get("kind") not in (None, "none"):
        base["detail"] = f"probe: {pres.detail}"
        return ClassRecord(tool_id=cls.tool_id, category=cls.category,
                           adapter=cls.adapter, required=cls.required,
                           status=STATUS_WITHHELD, probe=pres.to_dict(), metrics={},
                           invariants=_check_invariants(cls, {}), call={},
                           detail=f"probe unavailable: {pres.detail}",
                           duration_s=time.perf_counter() - t0)

    try:
        fn = adapters_mod.get(cls.adapter)
        res = fn(dict(ctx or {}))
    except adapters_mod.AdapterUnavailable as exc:
        return ClassRecord(tool_id=cls.tool_id, category=cls.category,
                           adapter=cls.adapter, required=cls.required,
                           status=STATUS_WITHHELD, probe=pres.to_dict(), metrics={},
                           invariants=_check_invariants(cls, {}), call={},
                           detail=f"adapter unavailable: {exc}",
                           duration_s=time.perf_counter() - t0)
    except Exception as exc:  # noqa: BLE001 - honest fail, never a fake number
        return ClassRecord(tool_id=cls.tool_id, category=cls.category,
                           adapter=cls.adapter, required=cls.required,
                           status=STATUS_FAIL, probe=pres.to_dict(), metrics={},
                           invariants=[], call={},
                           detail=(f"{type(exc).__name__}: {exc}; "
                                   f"trace={traceback.format_exc(limit=3).splitlines()[-1][:200]}"),
                           duration_s=time.perf_counter() - t0)

    metrics = dict(res.get("metrics") or {})
    invs = _check_invariants(cls, metrics)
    adapter_level = str(res.get("evidence") or STATUS_REAL)
    if adapter_level not in LEVEL_RANK:
        adapter_level = STATUS_FAIL
    all_ok = all(i["ok"] for i in invs) and bool(invs)
    status = adapter_level if all_ok else STATUS_FAIL
    if status == STATUS_FAIL and adapter_level != STATUS_FAIL:
        detail = (f"invariant violated: "
                  f"{[i['metric'] for i in invs if not i['ok']]}")
    else:
        detail = str(res.get("detail") or "")
    return ClassRecord(tool_id=cls.tool_id, category=cls.category,
                       adapter=cls.adapter, required=cls.required, status=status,
                       probe=pres.to_dict(), metrics=_round(metrics), invariants=invs,
                       call=dict(res.get("call") or {}), detail=detail,
                       duration_s=time.perf_counter() - t0)


def run_suite(suite: Suite, only: Optional[Sequence[str]] = None,
              ctx: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Run every (selected) class and roll the results up into a ledger."""

    ctx = dict(ctx or {})
    ctx.setdefault("repo", ctx.get("repo") or ".")
    selected = [c for c in suite.classes if (not only or c.tool_id in set(only))]
    missing = sorted(set(only or []) - {c.tool_id for c in suite.classes})
    if missing:
        raise ValueError(f"unknown tool ids requested: {missing}")

    t0 = time.perf_counter()
    records = [run_class(c, ctx=ctx) for c in selected]
    levels = [r.status for r in records]
    real = [r for r in records if r.status == STATUS_REAL]
    by_category: Dict[str, List[str]] = {}
    for r in records:
        by_category.setdefault(r.category, []).append(r.tool_id)
    real_categories = sorted({r.category for r in real})
    coverage_ok = len(real) >= suite.target_classes

    structural = [{"tool_id": r.tool_id, "status": r.status,
                   "checks": sorted(f"{i['metric']}:{int(bool(i['ok']))}"
                                    for i in r.invariants)} for r in records]
    values = [{"tool_id": r.tool_id, "metrics": r.metrics} for r in records]

    ledger: Dict[str, Any] = {
        "suite_id": suite.suite_id,
        "spec_version": SPEC_VERSION,
        "spec_path": suite.source_path,
        "spec_digest": _digest([c.to_dict() for c in suite.classes]),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_classes": suite.target_classes,
        "measured": {
            "real_classes": len(real),
            "real_tool_ids": [r.tool_id for r in real],
            "real_categories": real_categories,
            "counts_by_status": {lv: levels.count(lv) for lv in LEVEL_RANK},
            "classes_by_category": by_category,
        },
        "coverage": {"ok": coverage_ok, "real_classes": len(real),
                     "target_classes": suite.target_classes,
                     "shortfall": max(0, suite.target_classes - len(real))},
        # A subset run can never be read as a KPI measurement: ``partial`` is part
        # of the ledger (not of the caller), so ``verdict()`` cannot miss it.
        "partial": bool(only),
        "selected_tool_ids": [r.tool_id for r in records],
        "rollup_level": weakest(levels) if levels else STATUS_WITHHELD,
        "classes": [r.to_dict() for r in records],
        "environment": {"python": sys.version.split()[0],
                        "platform": platform.platform(),
                        "executable": sys.executable},
        "duration_s": round(time.perf_counter() - t0, 3),
        "digest": _digest(structural),
        "value_digest": _digest(values),
    }
    return ledger


def verdict(ledger: Mapping[str, Any]) -> str:
    """One word for the gate: PASS / FAIL / PARTIAL.

    ``PASS``      -- the coverage target is met and no required class failed;
    ``FAIL``      -- a required class failed, or a *full* run missed the target;
    ``PARTIAL``   -- a *subset* run (``partial``) in which every class that was
    actually selected came back REAL: the target was never put to the test, so
    calling it FAIL would assert a shortfall that no evidence supports, and
    calling it PASS would launder a subset into a KPI. Consumers that only
    understand PASS/FAIL still treat PARTIAL as "not met" (fail-closed).
    """

    cov = ledger.get("coverage") or {}
    required = [c for c in ledger.get("classes", []) if c.get("required")]
    if any(c["status"] == STATUS_FAIL for c in required):
        return VERDICT_FAIL
    if cov.get("ok"):
        return VERDICT_PASS
    if ledger.get("partial") and required and all(
            c["status"] == STATUS_REAL for c in required):
        return VERDICT_PARTIAL
    return VERDICT_FAIL