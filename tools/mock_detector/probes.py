"""The three structural probes: determinism, idempotency, monotonicity.

Design contract
---------------
*probe* modules only *measure*; they never assign an evidence level.  All
verdicts come from :mod:`tools.mock_detector.classify`, which sees only the
recorded numbers — so a verdict can always be re-derived from ``scan.json`` and
argued with.

Why these three and not accuracy
--------------------------------
A hardcoded constant, a ``random()`` or a stateful counter can all be caught
without knowing *what* the endpoint should compute:

* **determinism** — same request twice must give the same numeric vector
  (fingerprint = SHA-256 of the flattened numeric outputs, so request ids and
  timestamps in the payload cannot fake a failure);
* **idempotency** — same request repeated must give the same answer, *including
  when a different request has been interleaved in between* (catches hidden
  module state: a counter, a cached "last input", a grown list);
* **monotonicity** — sweeping one numeric input while holding the others must
  move at least one numeric output, and must move it in a monotone or smooth
  way rather than in a few quantized steps (catches invariance = hardcoded
  output, and lookup-table degeneracy).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .canonical import (compare_vectors, distinct_count, flatten_numeric,
                        jump_ratio, numeric_sha256, payload_sha256, rel_diff,
                        shape_of_series, vector_span)
from .httpc import Response
from .model import ProbeReport, Target


@dataclass
class ProbeConfig:
    """Tunables — all recorded in the scan artefact so the run is reproducible."""
    determinism_tol: float = 1e-9      # relative tolerance for "same answer"
    idempotency_tol: float = 1e-9
    invariance_tol: float = 1e-9       # relative span below which a field is constant
    sweep_ladder: Sequence[float] = (0.80, 0.90, 1.00, 1.10, 1.20)
    max_axes: int = 6                  # numeric inputs swept per endpoint
    min_axis_points: int = 3           # accepted sweep points needed to judge an axis
    request_budget_per_target: int = 40
    move_tol: float = 1e-6             # relative span that counts as "responds"
    #: hard wall-clock cap per target.  A slow or hanging endpoint must not be able
    #: to stall the scan: whatever evidence was collected is then judged as-is and
    #: the shortfall is flagged (PROBE_DEADLINE) rather than silently padded.
    target_deadline_s: float = 30.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "determinism_tol": self.determinism_tol,
            "idempotency_tol": self.idempotency_tol,
            "invariance_tol": self.invariance_tol,
            "sweep_ladder": list(self.sweep_ladder),
            "max_axes": self.max_axes,
            "min_axis_points": self.min_axis_points,
            "request_budget_per_target": self.request_budget_per_target,
            "move_tol": self.move_tol,
            "target_deadline_s": self.target_deadline_s,
        }


class _Budget:
    """Request budget + wall-clock deadline for one target."""

    def __init__(self, limit: int, deadline_s: Optional[float] = None):
        self.limit = limit
        self.used = 0
        self.deadline = (time.monotonic() + deadline_s) if deadline_s else None
        self.timed_out = False

    def take(self) -> bool:
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.timed_out = True
            return False
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.timed_out or self.used >= self.limit


def _numeric_of(resp: Response) -> Dict[str, float]:
    if not resp.ok or resp.payload is None:
        return {}
    return flatten_numeric(resp.payload)


def _call(client, target: Target, payload: Dict[str, Any]) -> Response:
    return client.request(target.method, target.path, payload, target.timeout_s)


def numeric_input_fields(payload: Optional[Dict[str, Any]]) -> List[str]:
    """Top-level scalar numeric inputs, in declaration order (no domain meaning)."""
    if not payload:
        return []
    out = []
    for key, val in payload.items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out.append(key)
    return out


def _scaled(value: float, factor: float) -> float:
    """Scale a numeric input, preserving int-ness and never producing 0/negative."""
    new = value * factor
    if isinstance(value, int):
        new = int(round(new))
        if new == 0:
            new = 1
        return new
    return round(new, 10)


def _sweep_values(value: float, ladder: Sequence[float], min_points: int) -> List[float]:
    vals: List[float] = []
    for factor in ladder:
        cand = _scaled(value, factor)
        if cand not in vals:
            vals.append(cand)
    if len(vals) < min_points:                      # degenerate base (e.g. 0)
        for factor in (1.5, 2.0, 3.0):
            cand = _scaled(value, factor)
            if cand not in vals:
                vals.append(cand)
    return vals


# --------------------------------------------------------------------------
# probe 1 + 2: determinism and idempotency
# --------------------------------------------------------------------------
def probe_determinism(client, target: Target, cfg: ProbeConfig, report: ProbeReport,
                      budget: _Budget) -> Dict[str, float]:
    """Same request twice. Fills det_* fields, returns the first numeric vector."""
    if not budget.take():
        report.flags.append("BUDGET_EXHAUSTED_BEFORE_DETERMINISM")
        return {}
    first = _call(client, target, target.base_payload or {})
    report.status = first.status
    report.http_ok = first.ok
    if first.error:
        report.error = first.error
        report.error_kind = first.error_kind
    if not budget.take():
        report.flags.append("BUDGET_EXHAUSTED_BEFORE_DETERMINISM_REPEAT")
        return _numeric_of(first)

    second = _call(client, target, target.base_payload or {})
    vec1, vec2 = _numeric_of(first), _numeric_of(second)
    if vec1 and not vec2 and budget.take():
        # a transient server error must not be mistaken for non-determinism:
        # retry the partner call once and say so.
        second = _call(client, target, target.base_payload or {})
        vec2 = _numeric_of(second)
        if vec2:
            report.flags.append("DETERMINISM_SECOND_ATTEMPT")
    report.det_hash = [numeric_sha256(vec1) if vec1 else "",
                       numeric_sha256(vec2) if vec2 else ""]
    worst, path, _pair, n = compare_vectors(vec1, vec2)
    report.det_worst_rel = worst if n else None
    report.det_path = path
    if n == 0:
        report.det_ok = None
    else:
        report.det_ok = worst <= cfg.determinism_tol
    if (first.payload is not None and second.payload is not None
            and payload_sha256(first.payload) != payload_sha256(second.payload)
            and report.det_ok):
        report.volatile_metadata = True
    return vec1


def probe_idempotency(client, target: Target, cfg: ProbeConfig, report: ProbeReport,
                      budget: _Budget, first_vec: Dict[str, float]) -> None:
    """Repeat the request, and repeat it again across an interleaved different one.

    Two failure modes are separated:
    ``repeat``    — the identical request repeated immediately differs;
    ``isolation`` — it matches on immediate repeat but differs after a *different*
                    request has run in between (hidden state / order dependence).
    """
    if not first_vec:
        return
    repeats = 0
    prev_vec = first_vec
    for _ in range(2):                              # two more immediate repeats
        if not budget.take():
            report.flags.append("BUDGET_EXHAUSTED_IN_IDEMPOTENCY")
            break
        resp = _call(client, target, target.base_payload or {})
        vec = _numeric_of(resp)
        if not vec:
            report.flags.append("IDEMPOTENCY_REPEAT_NO_NUMERICS")
            break
        worst, path, _pair, n = compare_vectors(prev_vec, vec)
        repeats += 1
        if n and (report.idem_worst_rel is None or worst > report.idem_worst_rel):
            report.idem_worst_rel, report.idem_path = worst, path
        prev_vec = vec
    report.idem_repeats = repeats
    if report.idem_worst_rel is not None:
        report.idem_ok = report.idem_worst_rel <= cfg.idempotency_tol

    # order-independence: A → B → A
    perturbed = _perturbed_payload(target.base_payload, cfg)
    if perturbed is None or budget.exhausted:
        report.flags.append("ISOLATION_PROBE_SKIPPED")
        return
    if not budget.take() or not budget.take():
        report.flags.append("BUDGET_EXHAUSTED_IN_ISOLATION")
        return
    _call(client, target, perturbed)                 # interleaved different request
    resp_a2 = _call(client, target, target.base_payload or {})
    vec_a2 = _numeric_of(resp_a2)
    if not vec_a2:
        report.flags.append("ISOLATION_NO_NUMERICS")
        return
    worst, path, _pair, n = compare_vectors(first_vec, vec_a2)
    if n:
        report.idem_isolation_worst_rel = worst
        report.idem_isolation_ok = worst <= cfg.idempotency_tol
        if not report.idem_isolation_ok:
            report.detail += f"isolation drift on {path}; "


def _perturbed_payload(payload: Optional[Dict[str, Any]],
                       cfg: ProbeConfig) -> Optional[Dict[str, Any]]:
    """A payload that differs on exactly one numeric field (for isolation test)."""
    fields = numeric_input_fields(payload)
    if not fields:
        return None
    out = dict(payload or {})
    key = fields[0]
    val = out[key]
    cand = _scaled(val, 1.5 if val else 2.0)
    if cand == val:
        cand = _scaled(val, 0.5)
    out[key] = cand
    return out


# --------------------------------------------------------------------------
# probe 3: monotonicity / response shape
# --------------------------------------------------------------------------
def probe_monotonicity(client, target: Target, cfg: ProbeConfig, report: ProbeReport,
                       budget: _Budget) -> None:
    """Sweep each numeric input; classify every numeric output's response shape."""
    base = dict(target.base_payload or {})
    fields = target.sweep_fields or numeric_input_fields(base)
    fields = [f for f in fields if f in base][:cfg.max_axes]
    if not fields:
        report.flags.append("NO_SWEEPABLE_NUMERIC_INPUT")
        return

    # Reserve part of the budget for the uniform-scale fallback, otherwise a wide
    # payload exhausts the budget on single axes and the fallback never runs.
    reserve = cfg.min_axis_points + 6
    per_axis = max(cfg.min_axis_points,
                   (cfg.request_budget_per_target - reserve) // max(len(fields), 1))
    all_vectors: List[Dict[str, float]] = []
    field_shapes: Dict[str, List[str]] = {}
    for field in fields:
        values = _sweep_values(base[field], cfg.sweep_ladder, cfg.min_axis_points)[:per_axis]
        accepted: List[float] = []
        series: Dict[str, List[float]] = {}
        rejected = 0
        for value in values:
            if not budget.take():
                report.flags.append("BUDGET_EXHAUSTED_IN_SWEEP")
                break
            payload = dict(base)
            payload[field] = value
            resp = _call(client, target, payload)
            vec = _numeric_of(resp)
            if not vec:
                rejected += 1
                continue
            accepted.append(value)
            all_vectors.append(vec)
            for path, num in vec.items():
                series.setdefault(path, []).append(num)
        axis: Dict[str, Any] = {
            "field": field,
            "input_values": values,
            "accepted_values": accepted,
            "n_accepted": len(accepted),
            "n_rejected": rejected,
            "status": "ok" if len(accepted) >= cfg.min_axis_points else "insufficient",
        }
        if axis["status"] != "ok":
            report.axes.append(axis)
            report.axes_rejected += 1
            continue

        moved, shapes, worst_span, worst_jump, worst_path = 0, {}, 0.0, 1.0, None
        for path, vals in series.items():
            shape = shape_of_series(vals, cfg.invariance_tol)
            shapes[path] = shape
            field_shapes.setdefault(path, []).append(shape)
            span = vector_span(vals)
            if span > worst_span:
                worst_span, worst_path = span, path
            if shape != "constant":
                moved += 1
                worst_jump = max(worst_jump, jump_ratio(vals))
        shape_counts: Dict[str, int] = {}
        for shape in shapes.values():
            shape_counts[shape] = shape_counts.get(shape, 0) + 1
        # keep up to two representative series (largest span) for the report figure
        ranked = sorted(series.items(), key=lambda kv: -vector_span(kv[1]))[:2]
        comparable = len({len(v) for _k, v in ranked}) == 1
        axis["series_sample"] = (
            {k: [float(f"{v:.12g}") for v in vals] for k, vals in ranked}
            if comparable else {})
        axis.update({
            "moved_fields": moved,
            "constant_fields": sum(1 for s in shapes.values() if s == "constant"),
            "shape_counts": shape_counts,
            "worst_span": worst_span,
            "worst_field": worst_path,
            "max_jump_ratio": worst_jump,
        })
        report.axes.append(axis)
        report.axes_probed += 1
        if moved == 0:
            report.axes_invariant += 1
        if worst_span > cfg.move_tol:
            report.axes_respond += 1

    if report.axes_respond == 0 and report.axes_rejected:
        # Every single-axis sweep broke an internal consistency constraint (the
        # classic case: a payload whose fields must close on each other).  A
        # *uniform* scaling preserves every ratio, so it is the one sweep that
        # stays inside the admissible set — try it before giving up.
        uniform = _uniform_axis(client, target, cfg, report, budget)
        if uniform is not None:
            all_vectors.extend(uniform)
            for axis in report.axes:
                if axis.get("status") == "ok":
                    axis["kind"] = axis.get("kind", "single")
            report.axes_probed = sum(1 for a in report.axes if a.get("status") == "ok")
            report.axes_respond = sum(1 for a in report.axes
                                      if a.get("status") == "ok"
                                      and float(a.get("worst_span") or 0) > cfg.move_tol)
            report.axes_invariant = sum(1 for a in report.axes
                                        if a.get("status") == "ok"
                                        and int(a.get("moved_fields") or 0) == 0)

    # ---- response profile
    if all_vectors:
        report.n_samples = len(all_vectors)
        report.n_distinct_vectors = distinct_count(numeric_sha256(v) for v in all_vectors)
        field_union = sorted({k for v in all_vectors for k in v})
        report.n_output_fields = len(field_union)
        report.output_fields_sample = field_union[:12]
        if field_shapes:
            const = sum(1 for shapes in field_shapes.values()
                        if shapes and all(s == "constant" for s in shapes))
            report.constant_field_fraction = round(const / len(field_shapes), 6)


def _uniform_axis(client, target: Target, cfg: ProbeConfig, report: ProbeReport,
                  budget: _Budget) -> Optional[List[Dict[str, float]]]:
    """Sweep a *common* scale factor over all numeric inputs at once."""
    base = dict(target.base_payload or {})
    fields = [f for f in numeric_input_fields(base)]
    if not fields:
        return None
    accepted: List[float] = []
    series: Dict[str, List[float]] = {}
    vectors: List[Dict[str, float]] = []
    for factor in cfg.sweep_ladder:
        payload = dict(base)
        touched = False
        for f in fields:
            new = _scaled(base[f], factor)
            if new != base[f]:
                touched = True
            payload[f] = new
        if not touched:
            continue
        if not budget.take():
            report.flags.append("BUDGET_EXHAUSTED_IN_UNIFORM_SWEEP")
            break
        vec = _numeric_of(_call(client, target, payload))
        if not vec:
            continue
        accepted.append(factor)
        vectors.append(vec)
        for path, num in vec.items():
            series.setdefault(path, []).append(num)
    if len(accepted) < cfg.min_axis_points:
        return vectors
    shapes = {path: shape_of_series(vals, cfg.invariance_tol) for path, vals in series.items()}
    worst_span, worst_path, worst_jump = 0.0, None, 1.0
    moved = 0
    for path, vals in series.items():
        span = vector_span(vals)
        if span > worst_span:
            worst_span, worst_path = span, path
        if shapes[path] != "constant":
            moved += 1
            worst_jump = max(worst_jump, jump_ratio(vals))
    shape_counts: Dict[str, int] = {}
    for shape in shapes.values():
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
    ranked = sorted(series.items(), key=lambda kv: -vector_span(kv[1]))[:2]
    report.axes.append({
        "field": "*uniform*",
        "kind": "uniform",
        "input_values": accepted,
        "accepted_values": accepted,
        "n_accepted": len(accepted),
        "n_rejected": len([f for f in cfg.sweep_ladder]) - len(accepted),
        "status": "ok",
        "moved_fields": moved,
        "constant_fields": sum(1 for v in shapes.values() if v == "constant"),
        "shape_counts": shape_counts,
        "worst_span": worst_span,
        "worst_field": worst_path,
        "max_jump_ratio": worst_jump,
        "series_sample": {k: [float(f"{v:.12g}") for v in vals] for k, vals in ranked
                          if len(vals) == len(accepted)},
        "note": "all numeric inputs scaled by a common factor (constraint-preserving sweep)",
    })
    report.flags.append("UNIFORM_SWEEP_FALLBACK")
    return vectors


def probe_global_perturbation(client, target: Target, cfg: ProbeConfig,
                              report: ProbeReport, budget: _Budget,
                              base_vec: Dict[str, float]) -> None:
    """Scale *every* numeric input at once — a second chance to falsify "hardcoded".

    The per-axis sweep can miss the real driver of an endpoint (an optional field
    the service silently overrides, a nested object the schema synthesiser could
    not build).  A joint perturbation is a cheap independent control: if the
    output moves here but on no single axis, the honest verdict is "cannot
    measure", not "hardcoded".
    """
    payload = dict(target.base_payload or {})
    scaled = False
    for field in numeric_input_fields(payload):
        new = _scaled(payload[field], 2.0)
        if new != payload[field]:
            payload[field] = new
            scaled = True
    if not scaled or not budget.take():
        report.flags.append("GLOBAL_PERTURBATION_SKIPPED")
        return
    vec = _numeric_of(_call(client, target, payload))
    if not vec:
        report.flags.append("GLOBAL_PERTURBATION_NO_NUMERICS")
        return
    worst, _path, _pair, n = compare_vectors(base_vec, vec)
    if n:
        report.global_scale_worst_rel = worst
        report.global_scale_ok = worst <= cfg.invariance_tol


def run_probes(client, target: Target, cfg: Optional[ProbeConfig] = None) -> ProbeReport:
    """Determinism → idempotency → monotonicity, sharing one request budget."""
    cfg = cfg or ProbeConfig()
    report = ProbeReport(cap_id=target.cap_id, method=target.method, path=target.path)
    budget = _Budget(cfg.request_budget_per_target, cfg.target_deadline_s)
    first_vec = probe_determinism(client, target, cfg, report, budget)

    if not first_vec:
        # No numeric answer → nothing else is measurable. Distinguish *why*.
        if report.status and 200 <= report.status < 300:
            report.flags.append("NO_NUMERIC_OUTPUT")
        elif report.error_kind in ("TRANSPORT", "TIMEOUT"):
            report.flags.append("UNREACHABLE")
        elif report.error_kind == "PARSE":
            report.flags.append("NON_JSON_BODY")
        elif report.status in (400, 422):
            report.flags.append("INPUT_REJECTED")
        return report

    probe_idempotency(client, target, cfg, report, budget, first_vec)
    probe_monotonicity(client, target, cfg, report, budget)
    probe_global_perturbation(client, target, cfg, report, budget, first_vec)

    # base point participates in the sample set (helps cardinality estimate)
    report.n_samples += 2
    if budget.timed_out:
        report.flags.append("PROBE_DEADLINE")
    return report