"""Adapters: the stage implementations the harness can compose.

Every adapter follows one shape::

    run(ctx, params, inputs) -> {output_port: PortValue}

``inputs`` only contains ports that resolved to a value; a missing *required*
port never reaches the adapter (the runner fails the node first).  Adapters that
need a library in this repo import it lazily and raise
:class:`AdapterUnavailable` when it is absent, which the runner records as
``WITHHELD`` rather than a fake number.

Adapters registered here (all domain-neutral; content arrives through params):

============ ==========================================================
``objective``        declarative synthetic response (pure math)
``probe``            declarative score/label generator for calibration
``optimize``         optimisation library stage (``tools.optimizer``)
``uq``               forward uncertainty propagation (``services.uq_propagation``)
``calibrate_prior``  logistic feasibility calibration (``prior`` module)
``remap``            conservative field transfer (``tools.coupling.remap``)
``conservation_gate`` conservation gate over two fields (``tools.coupling``)
``external``         run-time probe of an external callable, with declared fallback
``audit``            criteria roll-up into one verdict (fail-closed)
============ ==========================================================
"""

from __future__ import annotations

import importlib
import math
import time
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from . import prior as prior_mod
from .registry import (
    Adapter,
    AdapterError,
    AdapterUnavailable,
    ExecutionContext,
    Port,
    PortValue,
    register,
    require,
)

# ---------------------------------------------------------------------------
# declarative response surfaces (pure math, no domain meaning)
# ---------------------------------------------------------------------------


def build_response(spec: Mapping[str, Any]) -> Callable[[np.ndarray], float]:
    """Build ``f(x) -> float`` from a declarative spec.

    ``{"kind": "quadratic", "center": [...], "scale": [...]}``
        weighted squared distance from ``center``.
    ``{"kind": "quartic", "center": [...], "scale": [...], "quartic": q}``
        adds ``q * sum((x_i - c_i)^4)`` -- still smooth, minimum at ``center``.
    ``{"kind": "linear", "coeff": [...], "offset": o}``
        ``o + sum(c_i x_i)``.
    """
    kind = str(spec.get("kind", "quadratic"))
    if kind in ("quadratic", "quartic"):
        center = np.asarray(spec.get("center", [0.0]), dtype=float).reshape(-1)
        scale = np.asarray(spec.get("scale", [1.0] * center.size), dtype=float).reshape(-1)
        if scale.size == 1 and center.size > 1:
            scale = np.repeat(scale, center.size)
        if scale.size != center.size:
            raise AdapterError("response spec: scale and center must have equal length")
        quartic = float(spec.get("quartic", 0.0)) if kind == "quartic" else 0.0

        def f(x: np.ndarray) -> float:
            d = np.asarray(x, dtype=float).reshape(-1) - center
            if d.size != center.size:
                raise AdapterError(
                    f"response expects {center.size} inputs, got {d.size}"
                )
            return float(np.sum((scale * d) ** 2) + quartic * np.sum(d ** 4))

        return f
    if kind == "linear":
        coeff = np.asarray(require(spec, "coeff"), dtype=float).reshape(-1)
        offset = float(spec.get("offset", 0.0))

        def g(x: np.ndarray) -> float:
            v = np.asarray(x, dtype=float).reshape(-1)
            if v.size != coeff.size:
                raise AdapterError(f"response expects {coeff.size} inputs, got {v.size}")
            return float(offset + np.dot(coeff, v))

        return g
    raise AdapterError(f"unknown response kind {kind!r}")


# ---------------------------------------------------------------------------
# field / mesh construction (data-driven)
# ---------------------------------------------------------------------------


def _mesh_from_spec(spec: Mapping[str, Any]):
    from tools.coupling.mesh import BoxMesh

    return BoxMesh.uniform(
        lo=require(spec, "lo"),
        hi=require(spec, "hi"),
        n=require(spec, "n"),
        name=str(spec.get("name", "mesh")),
    )


def _field_from_spec(mesh, spec: Mapping[str, Any], name: str):
    """Cell field from a declarative pattern: constant / linear / graded."""
    from tools.coupling.mesh import Field, QuantityKind

    kind = str(spec.get("kind", "constant"))
    centers = mesh.centers
    extent = mesh.extent
    if kind == "constant":
        values = np.full(mesh.n_cells, float(require(spec, "value")))
    elif kind == "linear":
        axis = int(spec.get("axis", 0))
        slope = float(require(spec, "slope"))
        offset = float(spec.get("offset", 0.0))
        if not 0 <= axis < centers.shape[1]:
            raise AdapterError(f"field axis {axis} out of range")
        values = offset + slope * (centers[:, axis] - centers[:, axis].min()) / max(
            extent[axis], 1e-300
        )
    elif kind == "graded":
        axis = int(spec.get("axis", 0))
        base = float(require(spec, "base"))
        ratio = float(spec.get("ratio", 1.0))
        n = mesh.n_cells
        values = base * ratio ** np.arange(n, dtype=float)
    else:
        raise AdapterError(f"unknown field kind {kind!r}")
    qkind = spec.get("quantity_kind", "density")
    return Field(values=values, mesh=mesh, kind=QuantityKind(qkind), name=name)


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------


def _run_objective(ctx: ExecutionContext, params: Mapping[str, Any],
                   inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    f = build_response(require(params, "response"))
    return {"f": PortValue("callable", f)}


def _run_probe(ctx: ExecutionContext, params: Mapping[str, Any],
               inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    """Latent variable model: label = latent > 0, score = signal*latent + noise*eps."""
    n = int(require(params, "n"))
    signal = float(params.get("signal", 1.0))
    noise = float(params.get("noise", 1.0))
    seed = int(params.get("seed", ctx.seed))
    rng = np.random.default_rng(seed)
    latent = rng.standard_normal(n)
    labels = (latent > 0.0).astype(float)
    scores = signal * latent + noise * rng.standard_normal(n)
    return {
        "scores": PortValue("series", scores),
        "labels": PortValue("series", labels),
    }


def _run_optimize(ctx: ExecutionContext, params: Mapping[str, Any],
                  inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    try:
        from tools.optimizer.optimizer_tool import DesignVariable, OptimizerTool
    except Exception as exc:  # noqa: BLE001 - absence must degrade, not crash
        raise AdapterUnavailable(f"optimizer library unavailable: {exc}") from exc

    objective = require(inputs, "objective").value
    variables = [
        DesignVariable(
            name=str(v["name"]),
            lower=float(v["lower"]),
            upper=float(v["upper"]),
        )
        for v in require(params, "variables")
    ]
    tool = OptimizerTool(variables)
    result = tool.optimize(
        objective,
        method=str(params.get("method", "DE")),
        max_iter=int(params.get("max_iter", 60)),
        pop_size=int(params.get("pop_size", 15)),
        surrogate_doe_size=int(params.get("surrogate_doe_size", 20)),
    )
    x_best = np.array([result.best_x[v.name] for v in variables], dtype=float)
    return {
        "x_best": PortValue("series", x_best),
        "f_best": PortValue("scalar", float(result.best_fun)),
        "n_eval": PortValue("scalar", float(result.n_eval)),
        "summary": PortValue("report", result.to_dict()),
    }


def _run_uq(ctx: ExecutionContext, params: Mapping[str, Any],
            inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    try:
        from backend.services.uq_propagation import InputSpec, output_uncertainty
    except Exception as exc:  # noqa: BLE001
        raise AdapterUnavailable(f"UQ framework unavailable: {exc}") from exc

    objective = require(inputs, "objective").value
    specs = [
        InputSpec(
            name=str(s["name"]),
            kind=str(s.get("kind", "normal")),
            mean=float(s.get("mean", float("nan"))),
            std=float(s.get("std", float("nan"))),
            low=float(s.get("low", float("nan"))),
            high=float(s.get("high", float("nan"))),
            truncate_sigma=float(s.get("truncate_sigma", 4.0)),
        )
        for s in require(params, "inputs")
    ]
    anchor = inputs.get("anchor")
    out = output_uncertainty(
        objective,
        specs,
        n=int(params.get("n", 4000)),
        quantiles=tuple(params.get("quantiles", (0.05, 0.5, 0.95))),
        n_boot=int(params.get("n_boot", 400)),
        confidence=float(params.get("confidence", 0.95)),
        seed=int(params.get("seed", ctx.seed)),
        method=str(params.get("method", "qmc")),
        out_names=[str(params.get("out_name", "y"))],
    )
    col = out["columns"][str(params.get("out_name", "y"))]
    q = col["quantiles"]
    q_lo = float(q["0.05"]["estimate"])
    q_hi = float(q["0.95"]["estimate"])
    p95 = q_hi
    report = {
        "method": out["method"],
        "seed": out["seed"],
        "n_evals": out["n_evals"],
        "wall_s": out["wall_s"],
        "mean": col["mean"],
        "std": col["std"],
        "ci_mean": col["ci_mean"],
        "q05": q_lo,
        "q50": float(q["0.5"]["estimate"]),
        "q95": q_hi,
        "ci_width_bootstrap_q95": float(q["0.95"]["width_bootstrap"]),
        "ci_width_order_statistic_q95": float(q["0.95"]["width_order_statistic"]),
        "consistency_ratio_q95": float(
            q["0.95"]["width_order_statistic"] / q["0.95"]["width_bootstrap"]
        ),
        "spec_diagnostics": out.get("spec_diagnostics", []),
    }
    result = {
        "stats": PortValue("report", report),
        "mean": PortValue("scalar", float(col["mean"])),
        "p95": PortValue("scalar", p95),
        "n_evals": PortValue("scalar", float(out["n_evals"])),
    }
    if anchor is not None:
        # Cross-stage consistency between the optimisation and UQ stages, stated as
        # an invariant that can actually fail: the optimiser minimises the *same*
        # response the propagation samples, so its nominal value must not be beaten
        # by the 5 % quantile of the ensemble, and it must sit below the mean by a
        # measurable margin (reported in ensemble standard deviations).
        x_nom = np.asarray(anchor.value, dtype=float).reshape(-1)
        nominal = float(objective(x_nom))
        std = float(col["std"])
        result["nominal"] = PortValue("scalar", nominal)
        result["anchor_below_q05"] = PortValue("flag", bool(nominal <= q_lo))
        gap = float(col["mean"]) - nominal
        result["anchor_gap_sigma"] = PortValue(
            "scalar", float(gap / std) if std > 0 else 0.0
        )
        # significance of the same gap against the standard error of the ensemble
        # mean: this is the criterion that can be stated without a magic threshold.
        se = float(col.get("std_error_mean", float("nan")))
        result["anchor_gap_stderr"] = PortValue(
            "scalar", float(gap / se) if se and se > 0 else 0.0
        )
        result["band_contains_anchor"] = PortValue(
            "flag", bool(q_lo <= nominal <= q_hi)
        )
        result["band_margin"] = PortValue(
            "scalar", float(min(nominal - q_lo, q_hi - nominal))
        )
    return result


def _run_calibrate_prior(ctx: ExecutionContext, params: Mapping[str, Any],
                         inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    scores = np.asarray(require(inputs, "scores").value, dtype=float).reshape(-1)
    labels = np.asarray(require(inputs, "labels").value, dtype=float).reshape(-1)
    try:
        rep = prior_mod.calibrate(scores, labels, n_bins=int(params.get("n_bins", 10)))
    except prior_mod.CalibrationError as exc:
        raise AdapterError(f"calibration failed: {exc}") from exc
    return {
        "report": PortValue("report", rep.to_dict()),
        "ece": PortValue("scalar", rep.ece),
        "brier": PortValue("scalar", rep.brier),
        "auroc": PortValue("scalar", rep.auroc),
        "monotone": PortValue("flag", rep.monotone),
    }


def _run_remap(ctx: ExecutionContext, params: Mapping[str, Any],
               inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    try:
        from tools.coupling.remap import RemapSpec, remap
    except Exception as exc:  # noqa: BLE001
        raise AdapterUnavailable(f"remap library unavailable: {exc}") from exc

    src_mesh = _mesh_from_spec(require(params, "src_mesh"))
    dst_mesh = _mesh_from_spec(require(params, "dst_mesh"))
    src_field = _field_from_spec(src_mesh, require(params, "field"), "src")

    spec = RemapSpec(
        operator=str(params.get("operator", "overlap_l2")),
        quantity_kind=str(params.get("quantity_kind", "density")),
        apply_conservation_correction=bool(params.get("apply_conservation_correction", True)),
        positivity=bool(params.get("positivity", False)),
        enforce_bounds=bool(params.get("enforce_bounds", False)),
        outside_policy=str(params.get("outside_policy", "raise")),
        fill_value=float(params.get("fill_value", 0.0)),
    )
    res = remap(src_field, operator=spec.operator, spec=spec, dst_mesh=dst_mesh)
    dst_field, rep = res.field, res.report
    return {
        "src": PortValue("field", src_field),
        "dst": PortValue("field", dst_field),
        "total_src": PortValue("scalar", float(src_field.total)),
        "total_dst": PortValue("scalar", float(dst_field.total)),
        "rel_residual": PortValue("scalar", float(rep.rel_residual)),
        "covered_fraction": PortValue("scalar", float(rep.covered_fraction)),
        "report": PortValue("report", rep.to_dict()),
    }


def _run_conservation_gate(ctx: ExecutionContext, params: Mapping[str, Any],
                           inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    try:
        from tools.coupling.conservation import check
    except Exception as exc:  # noqa: BLE001
        raise AdapterUnavailable(f"conservation library unavailable: {exc}") from exc

    src = require(inputs, "src").value
    dst = require(inputs, "dst").value
    gate = check(
        src,
        dst,
        sum_tol=float(params.get("sum_tol", 1e-12)),
        moment_tol=float(params.get("moment_tol", 1e-10)),
        nonneg_tol=float(params.get("nonneg_tol", 1e-12)),
        overshoot_tol=float(params.get("overshoot_tol", 1e-12)),
    )
    return {
        "passed": PortValue("flag", bool(gate.passed)),
        "sum_residual": PortValue("scalar", float(gate.metrics.sum_rel_residual)),
        "moment_residual": PortValue("scalar", float(gate.metrics.moment_rel_residual)),
        "metrics": PortValue("report", gate.to_dict()),
    }


def _run_external(ctx: ExecutionContext, params: Mapping[str, Any],
                  inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    """Probe an external callable; fall back only to a *declared* substitute.

    The report says which path was taken, so a run whose stage was substituted
    can never be mistaken for a run of the real stage.
    """
    module = str(require(params, "module"))
    attr = str(require(params, "attr"))
    args = list(params.get("args", []))
    kwargs = dict(params.get("kwargs", {}))
    substitute_spec = params.get("substitute")
    substitute_evidence = str(params.get("substitute_evidence", "MOCK"))

    record: Dict[str, Any] = {
        "module": module,
        "attr": attr,
        "substituted": False,
        "substitute_evidence": substitute_evidence,
    }
    try:
        mod = importlib.import_module(module)
        fn = getattr(mod, attr)
    except Exception as exc:  # noqa: BLE001 - absence is data, not an error
        record["import_error"] = f"{type(exc).__name__}: {exc}"
        if substitute_spec is None:
            raise AdapterUnavailable(
                f"external stage {module}:{attr} unavailable and no substitute declared"
            ) from exc
        fn = build_response(substitute_spec)
        record["substituted"] = True
        record["probe"] = np.asarray(args, dtype=float).reshape(-1).tolist() \
            if args else None
        args = [np.asarray(args, dtype=float).reshape(-1)]
        kwargs = {}
        record["call"] = "substitute(args_vector)"
    t0 = time.perf_counter()
    value = fn(*args, **kwargs)
    record["wall_s"] = time.perf_counter() - t0
    try:
        scalar = float(np.asarray(value, dtype=float).reshape(-1)[0])
    except Exception as exc:  # noqa: BLE001
        raise AdapterError(f"external stage returned a non-scalar: {exc}") from exc
    record["value"] = scalar
    available = not record["substituted"]
    return {
        "value": PortValue("scalar", scalar),
        "available": PortValue("flag", available),
        "report": PortValue("report", record),
    }


_OPS = {
    "le": lambda v, t: float(v) <= float(t),
    "ge": lambda v, t: float(v) >= float(t),
    "abs_le": lambda v, t: abs(float(v)) <= float(t),
    "eq_true": lambda v, t: bool(v) is True,
    "eq_false": lambda v, t: bool(v) is False,
}


def _run_audit(ctx: ExecutionContext, params: Mapping[str, Any],
               inputs: Mapping[str, PortValue]) -> Dict[str, PortValue]:
    """Roll criteria over already-computed ports into PASS/FAIL/WITHHELD."""
    criteria = list(require(params, "criteria"))
    rows: List[Dict[str, Any]] = []
    statuses: List[str] = []
    for crit in criteria:
        name = str(require(crit, "name"))
        port = str(require(crit, "input"))
        op = str(require(crit, "op"))
        informational = bool(crit.get("informational", False))
        if op not in _OPS:
            raise AdapterError(f"criterion {name!r}: unknown op {op!r}")
        pv = inputs.get(port)
        if pv is None:
            rows.append({
                "name": name, "input": port, "op": op,
                "threshold": crit.get("threshold"),
                "status": "INFO" if informational else "WITHHELD",
                "informational": informational,
                "reason": "input port not bound",
            })
            statuses.append("INFO" if informational else "WITHHELD")
            continue
        value = pv.value
        ok = _OPS[op](value, crit.get("threshold"))
        row_status = "PASS" if ok else ("INFO" if informational else "FAIL")
        rows.append({
            "name": name, "input": port, "op": op,
            "threshold": crit.get("threshold"),
            "value": value if not isinstance(value, np.ndarray) else None,
            "status": row_status,
            "informational": informational,
        })
        statuses.append(row_status)
    if "FAIL" in statuses:
        verdict_status = "FAIL"
    elif "WITHHELD" in statuses:
        verdict_status = "WITHHELD"
    else:
        verdict_status = "PASS"
    report = {
        "verdict": verdict_status,
        "n_criteria": len(rows),
        "n_pass": statuses.count("PASS"),
        "n_fail": statuses.count("FAIL"),
        "n_withheld": statuses.count("WITHHELD"),
        "n_info": statuses.count("INFO"),
        "criteria": rows,
    }
    return {
        "verdict": PortValue("flag", verdict_status == "PASS"),
        "status": PortValue("report", report),
    }


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

register(Adapter(
    kind="objective",
    summary="declarative synthetic response surface (pure math)",
    inputs=(),
    outputs=(Port("f", "callable", doc="f(x: array) -> float"),),
    run=_run_objective,
    evidence="REAL",
    implementation="tools/toolchain_integration/adapters.py:build_response",
))

register(Adapter(
    kind="probe",
    summary="score/label generator for feasibility calibration",
    inputs=(),
    outputs=(
        Port("scores", "series"),
        Port("labels", "series"),
    ),
    run=_run_probe,
    evidence="REAL",
    implementation="tools/toolchain_integration/adapters.py:_run_probe",
))

register(Adapter(
    kind="optimize",
    summary="optimisation stage (DOE + surrogate / DE / SLSQP)",
    inputs=(Port("objective", "callable"),),
    outputs=(
        Port("x_best", "series"),
        Port("f_best", "scalar"),
        Port("n_eval", "scalar"),
        Port("summary", "report", required=False),
    ),
    run=_run_optimize,
    evidence="REAL",
    implementation="tools/optimizer/optimizer_tool.py:OptimizerTool.optimize",
))

register(Adapter(
    kind="uq",
    summary="forward uncertainty propagation with quantile intervals",
    inputs=(
        Port("objective", "callable", doc="response to propagate"),
        Port("anchor", "series", required=False, doc="nominal design point"),
    ),
    outputs=(
        Port("stats", "report"),
        Port("mean", "scalar"),
        Port("p95", "scalar"),
        Port("n_evals", "scalar"),
        Port("nominal", "scalar", required=False),
        Port("anchor_below_q05", "flag", required=False),
        Port("anchor_gap_sigma", "scalar", required=False),
        Port("anchor_gap_stderr", "scalar", required=False),
        Port("band_contains_anchor", "flag", required=False),
        Port("band_margin", "scalar", required=False),
    ),
    run=_run_uq,
    evidence="REAL",
    implementation="backend/services/uq_propagation.py:output_uncertainty",
))

register(Adapter(
    kind="calibrate_prior",
    summary="logistic feasibility prior with Brier/ECE calibration statistics",
    inputs=(
        Port("scores", "series"),
        Port("labels", "series"),
    ),
    outputs=(
        Port("report", "report"),
        Port("ece", "scalar"),
        Port("brier", "scalar"),
        Port("auroc", "scalar"),
        Port("monotone", "flag"),
    ),
    run=_run_calibrate_prior,
    evidence="REAL",
    implementation="tools/toolchain_integration/prior.py:calibrate",
))

register(Adapter(
    kind="remap",
    summary="conservative field transfer between two meshes",
    inputs=(),
    outputs=(
        Port("src", "field"),
        Port("dst", "field"),
        Port("total_src", "scalar"),
        Port("total_dst", "scalar"),
        Port("rel_residual", "scalar"),
        Port("covered_fraction", "scalar"),
        Port("report", "report"),
    ),
    run=_run_remap,
    evidence="REAL",
    implementation="tools/coupling/remap.py:remap",
))

register(Adapter(
    kind="conservation_gate",
    summary="sum/moment/positivity/overshoot gate over two fields",
    inputs=(
        Port("src", "field"),
        Port("dst", "field"),
    ),
    outputs=(
        Port("passed", "flag"),
        Port("sum_residual", "scalar"),
        Port("moment_residual", "scalar"),
        Port("metrics", "report"),
    ),
    run=_run_conservation_gate,
    evidence="REAL",
    implementation="tools/coupling/conservation.py:check",
))

register(Adapter(
    kind="external",
    summary="run-time probe of an external callable, declared fallback only",
    inputs=(),
    outputs=(
        Port("value", "scalar"),
        Port("available", "flag"),
        Port("report", "report"),
    ),
    run=_run_external,
    evidence="REAL",
    implementation="importlib probe (evidence governed by the probed module)",
))

register(Adapter(
    kind="audit",
    summary="criteria roll-up into PASS/FAIL/WITHHELD (fail-closed)",
    inputs=(),
    outputs=(
        Port("verdict", "flag"),
        Port("status", "report"),
    ),
    run=_run_audit,
    evidence="REAL",
    implementation="tools/toolchain_integration/adapters.py:_run_audit",
    accepts_any_inputs=True,
))