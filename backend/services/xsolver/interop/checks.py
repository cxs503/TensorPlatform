"""Check kinds for the cross-solver interoperation gate.

Every check is declarative: the spec says *where* the numbers live (artifact +
JSON pointer), *what* relation must hold (tolerance / band / order set), and the
code only carries the mechanics.  No physical vocabulary appears here.

Contract of a check implementation::

    def check_x(spec: Mapping, ctx: Ctx) -> dict

returning ``{"id", "kind", "title", "status", "detail", "fractures"}`` with
``status`` in ``PASS / FAIL / WITHHELD``.  ``WITHHELD`` is the fail-closed
answer: an absent or unusable input is never silently upgraded to agreement.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .units import UnitTable, canonical_dim, dim_str

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"
RANK = {PASS: 2, WITHHELD: 1, FAIL: 0}


class ResolveError(Exception):
    """Raised when a declared pointer or expression cannot be resolved."""


# ---------------------------------------------------------------------------
# pointer / expression helpers
# ---------------------------------------------------------------------------
def pointer_get(obj: Any, pointer: Optional[str]) -> Any:
    """RFC6901-lite JSON pointer lookup."""
    if pointer in (None, "", "/"):
        return obj
    current = obj
    for raw in str(pointer).lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, Mapping):
            if part not in current:
                raise ResolveError(f"pointer {pointer!r}: key {part!r} missing")
            current = current[part]
        else:
            raise ResolveError(f"pointer {pointer!r}: cannot descend into {type(current).__name__}")
    return current


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def eval_expression(expression: str, env: Mapping[str, float]) -> float:
    """Arithmetic-only expression evaluation (``rpm * z / 60``).

    No attribute access, no calls, no names other than ``env`` keys -- the
    expression is data from a manifest, so it must not be able to do anything
    but arithmetic.
    """
    node = ast.parse(str(expression), mode="eval").body

    def walk(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise ResolveError(f"expression {expression!r}: unknown name {n.id!r}")
            return float(env[n.id])
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            return _BIN_OPS[type(n.op)](walk(n.left), walk(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY_OPS:
            return _UNARY_OPS[type(n.op)](walk(n.operand))
        raise ResolveError(f"expression {expression!r}: unsupported node {type(n).__name__}")

    return walk(node)


class Ctx:
    """Evaluation context: resolved artifacts + unit table + defaults."""

    def __init__(self, artifacts: Mapping[str, Dict[str, Any]], units: UnitTable,
                 defaults: Optional[Mapping[str, Any]] = None) -> None:
        self.artifacts = artifacts
        self.units = units
        self.defaults = dict(defaults or {})

    def artifact(self, name: str) -> Dict[str, Any]:
        if name not in self.artifacts:
            return {"name": name, "present": False, "reason": "artifact not declared in manifest", "data": None}
        return self.artifacts[name]

    def value(self, ref: Mapping[str, Any], env: Optional[Mapping[str, float]] = None) -> Tuple[Any, Dict[str, Any]]:
        """Resolve ``{"artifact","pointer"}`` or ``{"expr","bindings"}``."""
        if "expr" in ref:
            bindings: Dict[str, float] = {}
            for key, sub in (ref.get("bindings") or {}).items():
                raw, _ = self.value(sub, env)
                bindings[key] = float(raw)
            if env:
                bindings = {**env, **bindings}
            return eval_expression(ref["expr"], bindings), {"expr": ref["expr"], "bindings": bindings}
        art = self.artifact(ref["artifact"])
        if not art["present"]:
            raise ResolveError(f"artifact {ref['artifact']!r} absent ({art.get('reason')})")
        raw = pointer_get(art["data"], ref.get("pointer"))
        if "scale" in ref:
            raw = [v * float(ref["scale"]) for v in raw] if isinstance(raw, list) else raw * float(ref["scale"])
        aggregate = ref.get("aggregate")
        if isinstance(aggregate, Mapping):
            # e.g. {"sum_of": "inertia_kg_m2"} over a list of records
            key = aggregate.get("sum_of")
            if not key or not isinstance(raw, (list, tuple)):
                raise ResolveError(f"aggregate {aggregate!r} needs a list of records")
            raw = [record.get(key) for record in raw if isinstance(record, Mapping)]
            aggregate = aggregate.get("op", "sum")
        if aggregate:
            numbers = _finite(raw if isinstance(raw, (list, tuple)) else [raw])
            if aggregate == "sum":
                raw = sum(numbers)
            elif aggregate == "max":
                raw = max(numbers) if numbers else None
            elif aggregate == "min":
                raw = min(numbers) if numbers else None
            elif aggregate == "count":
                raw = float(len(numbers))
            else:
                raise ResolveError(f"unknown aggregate {aggregate!r}")
        return raw, {"artifact": ref["artifact"], "pointer": ref.get("pointer"),
                     "sha256": art.get("sha256"), "aggregate": aggregate}


# ---------------------------------------------------------------------------
# numeric helpers
# ---------------------------------------------------------------------------
def rel_diff(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0 else abs(a - b) / scale


def _finite(values: Iterable[Any]) -> List[float]:
    out = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) < 3 or len(x) != len(y):
        return None

    def ranks(seq: Sequence[float]) -> List[float]:
        order = sorted(range(len(seq)), key=lambda i: seq[i])
        out = [0.0] * len(seq)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and seq[order[j + 1]] == seq[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(x), ranks(y)
    n = float(len(rx))
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return None if den == 0 else num / den


def _direction(seq: Sequence[float]) -> str:
    diffs = [b - a for a, b in zip(seq, seq[1:]) if b != a]
    if not diffs:
        return "flat"
    if all(d > 0 for d in diffs):
        return "increasing"
    if all(d < 0 for d in diffs):
        return "decreasing"
    return "non-monotone"


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> Optional[float]:
    if not xs or not ys or len(xs) != len(ys):
        return None
    pairs = sorted(zip(xs, ys))
    if x < pairs[0][0] or x > pairs[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pairs[-1][1]


def _fracture(check_id: str, kind: str, message: str, evidence: Mapping[str, Any]) -> Dict[str, Any]:
    return {"check": check_id, "type": kind, "message": message, "evidence": dict(evidence)}


def _result(spec: Mapping[str, Any], detail: Mapping[str, Any], status: str,
            fractures: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "id": spec["id"],
        "kind": spec["kind"],
        "title": spec.get("title") or spec["id"],
        "status": status,
        "detail": dict(detail),
        "fractures": list(fractures or []),
    }


# ---------------------------------------------------------------------------
# check kinds
# ---------------------------------------------------------------------------
def check_artifact_present(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    art = ctx.artifact(spec["artifact"])
    detail = {"artifact": spec["artifact"], "path": spec.get("path"), "sha256": art.get("sha256")}
    if art["present"]:
        return _result(spec, detail, PASS)
    detail["reason"] = art.get("reason") or "artifact missing"
    return _result(spec, detail, WITHHELD,
                   [_fracture(spec["id"], "artifact_missing",
                              f"下游判据所需工件 {spec['artifact']} 缺失：{detail['reason']}", detail)])


def check_criterion_inheritance(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    """An upstream node's own machine criterion is binding on the gate."""
    ref = {"artifact": spec["artifact"], "pointer": spec["pointer"]}
    try:
        record, meta = ctx.value(ref)
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    if not isinstance(record, Mapping) or "pass" not in record:
        return _result(spec, {"record": record, "provenance": meta}, WITHHELD,
                       [_fracture(spec["id"], "malformed_criterion",
                                  "上游判据记录缺少 pass 字段", {"record": record})])
    detail = {"record": dict(record), "provenance": meta,
              "threshold": record.get("threshold")}
    if bool(record.get("pass")):
        return _result(spec, detail, PASS)
    not_delivered = record.get("cases_run") == 0 or str(record.get("note", "")).startswith("not delivered")
    kind = "upstream_not_delivered" if not_delivered else "upstream_criterion_failed"
    message = (f"上游节点判据未满足（{'未交付' if not_delivered else '判据失败'}）："
               f"{record.get('note', '')[:160]}")
    return _result(spec, detail, FAIL, [_fracture(spec["id"], kind, message, detail)])


def check_series_compare(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    tol = float(spec.get("tolerance", ctx.defaults.get("series_rel", 0.10)))
    window = spec.get("window") or {}
    try:
        lx, lmeta = ctx.value(spec["left"]["x"])
        ly, _ = ctx.value(spec["left"]["y"])
        rx, rmeta = ctx.value(spec["right"]["x"])
        ry, _ = ctx.value(spec["right"]["y"])
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    xs, ys = _finite(lx), _finite(ly)
    xr, yr = _finite(rx), _finite(ry)
    if not xs or not xr or len(xs) != len(lx) or len(xr) != len(rx):
        return _result(spec, {"left_points": len(xs), "right_points": len(xr)}, WITHHELD,
                       [_fracture(spec["id"], "empty_series", "互裁曲线缺数据", {})])
    lo, hi = float(window.get("min", -math.inf)), float(window.get("max", math.inf))
    points = []
    for x, y in zip(xr, yr):
        if not (lo <= x <= hi):
            continue
        yi = _interp(xs, ys, x)
        if yi is None:
            continue
        points.append({"x": x, "left": yi, "right": y, "rel_diff": rel_diff(yi, y)})
    detail = {
        "tolerance": tol, "window": [window.get("min"), window.get("max")],
        "points": points, "n_points": len(points),
        "left_provenance": lmeta, "right_provenance": rmeta,
    }
    min_points = int(spec.get("min_points", 5))
    if len(points) < min_points:
        detail["reason"] = f"overlap {len(points)} < min_points {min_points}"
        return _result(spec, detail, WITHHELD,
                       [_fracture(spec["id"], "insufficient_overlap", detail["reason"], detail)])
    worst = max(points, key=lambda p: p["rel_diff"])
    detail["max_rel_diff"] = worst["rel_diff"]
    detail["mean_rel_diff"] = sum(p["rel_diff"] for p in points) / len(points)
    detail["worst_point"] = worst
    if worst["rel_diff"] <= tol:
        return _result(spec, detail, PASS)
    return _result(spec, detail, FAIL,
                   [_fracture(spec["id"], "tolerance_exceeded",
                              f"点 {worst['x']:.4g} 相对误差 {worst['rel_diff'] * 100:.2f}% > {tol * 100:.2f}%",
                              {"worst_point": worst, "tolerance": tol})])


def check_reference_band(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    band = dict(spec["band"])
    try:
        for key in ("lo", "hi"):
            if isinstance(band.get(key), Mapping):
                band[key], _ = ctx.value(band[key])
        lo, hi = float(band["lo"]), float(band["hi"])
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    rows, metas = [], []
    try:
        for point in spec["points"]:
            value, meta = ctx.value(point["ref"])
            rows.append({"label": point.get("label"), "value": float(value),
                         "inside": lo <= float(value) <= hi,
                         "factor_below_lo": (lo / float(value)) if float(value) > 0 else None})
            metas.append(meta)
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    detail = {"band": [lo, hi], "points": rows, "provenance": metas, "unit": spec.get("unit")}
    outside = [r for r in rows if not r["inside"]]
    if not outside:
        return _result(spec, detail, PASS)
    worst = max(outside, key=lambda r: (r["factor_below_lo"] or 0.0))
    msg = (f"{len(outside)}/{len(rows)} 点落在参考带 [{lo:g}, {hi:g}] 外；"
           f"最远点 {worst['label']}={worst['value']:.4g}（参考下限的 1/{worst['factor_below_lo']:.1f}）")
    return _result(spec, detail, FAIL, [_fracture(spec["id"], "outside_reference_band", msg, detail)])


def check_trend_agreement(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    try:
        computed, cmeta = ctx.value(spec["computed"])
        if isinstance(spec.get("reference"), Mapping):
            reference, rmeta = ctx.value(spec["reference"])
        else:
            reference, rmeta = spec.get("inline"), {"inline": True}
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    c = _finite(computed or [])
    r = _finite(reference or [])
    if len(c) < 2 or len(r) < 2:
        return _result(spec, {"computed_points": len(c), "reference_points": len(r)}, WITHHELD,
                       [_fracture(spec["id"], "empty_series", "趋势比较缺数据", {})])
    n = min(len(c), len(r))
    rho = _spearman(c[:n], r[:n])
    dir_c, dir_r = _direction(c[:n]), _direction(r[:n])
    min_rho = float(spec.get("min_spearman", 0.8))
    require_same = bool(spec.get("require_same_direction", True))
    detail = {"computed": c[:n], "reference": r[:n], "spearman": rho,
              "direction_computed": dir_c, "direction_reference": dir_r,
              "provenance": {"computed": cmeta, "reference": rmeta}}
    if rho is None:
        return _result(spec, detail, WITHHELD,
                       [_fracture(spec["id"], "degenerate_series", "秩相关不可判定（零方差）", detail)])
    same = (dir_c == dir_r) and dir_c in ("increasing", "decreasing")
    ok = rho >= min_rho and (same or not require_same)
    detail["ok"] = ok
    if ok:
        return _result(spec, detail, PASS)
    return _result(spec, detail, FAIL,
                   [_fracture(spec["id"], "trend_mismatch",
                              f"趋势不一致：Spearman={rho:.3f} (需≥{min_rho}), 方向 {dir_c} vs {dir_r}", detail)])


def _unit_of_field(ctx: Ctx, contract_field: Mapping, name: str) -> Tuple[Optional[str], str]:
    explicit = contract_field.get("unit")
    if explicit:
        return str(explicit), "declared"
    if contract_field.get("unit_from") == "field_suffix" or not explicit:
        suffix = ctx.units.field_suffix_unit(contract_field.get("field_name") or name)
        if suffix:
            return suffix, "field_suffix"
    return None, "undetermined"


def check_field_contract(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    art = ctx.artifact(spec["artifact"])
    if not art["present"]:
        return _result(spec, {"artifact": spec["artifact"], "reason": art.get("reason")}, WITHHELD,
                       [_fracture(spec["id"], "artifact_missing",
                                  f"接口契约校验对象 {spec['artifact']} 缺失", {"artifact": spec["artifact"]})])
    contract = spec["contract"]
    violations: List[Dict[str, Any]] = []
    fields_detail: List[Dict[str, Any]] = []
    unit_scales: Dict[str, float] = {}

    for field in contract.get("fields") or []:
        name, pointer = field["name"], field.get("pointer")
        try:
            value = pointer_get(art["data"], pointer)
        except ResolveError:
            violations.append({"type": "missing_field", "field": name, "pointer": pointer})
            fields_detail.append({"field": name, "present": False})
            continue
        unit_token, unit_src = _unit_of_field(ctx, field, name)
        parsed = ctx.units.parse(unit_token) if unit_token else None
        expected = ctx.units.quantity_dim(field.get("quantity"))
        row = {"field": name, "present": True, "unit": unit_token, "unit_source": unit_src,
               "quantity": field.get("quantity"),
               "dimension": dim_str(parsed["dims"]) if parsed else None,
               "expected_dimension": dim_str(expected) if expected is not None else None}
        if parsed is None:
            violations.append({"type": "unit_unparseable", "field": name, "unit": unit_token})
        else:
            unit_scales[name] = parsed["scale"]
            if expected is not None and parsed["dims"] != canonical_dim(dict(expected)):
                violations.append({"type": "dimension_mismatch", "field": name,
                                   "unit": unit_token, "dimension": dim_str(parsed["dims"]),
                                   "expected_dimension": dim_str(expected)})
        values = _finite(value if isinstance(value, list) else [value])
        if not values:
            violations.append({"type": "value_not_numeric", "field": name})
        fields_detail.append(row)

    for declaration in contract.get("declarations") or []:
        pointers = declaration.get("any_of_pointers") or []
        found = None
        for pointer in pointers:
            try:
                candidate = pointer_get(art["data"], pointer)
            except ResolveError:
                continue
            if candidate not in (None, ""):
                found = {"pointer": pointer, "value": candidate}
                break
        if found is None:
            violations.append({"type": "missing_declaration", "declaration": declaration["name"],
                               "reason": declaration.get("reason", ""),
                               "searched": pointers})
        else:
            fields_detail.append({"declaration": declaration["name"], "found": found})

    detail = {"artifact": spec["artifact"], "sha256": art.get("sha256"),
              "fields": fields_detail, "violations": violations,
              "n_violations": len(violations), "unit_scales": unit_scales}
    if not violations:
        return _result(spec, detail, PASS)
    kinds = sorted({v["type"] for v in violations})
    return _result(spec, detail, FAIL,
                   [_fracture(spec["id"], "contract_violation",
                              f"{len(violations)} 处接口契约违约（{', '.join(kinds)}）", detail)])


def check_shared_parameter(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    tol = float(spec.get("tolerance", ctx.defaults.get("shared_param_rel", 0.02)))
    rows, missing = [], []
    for entry in spec["entries"]:
        label = entry.get("label") or entry.get("artifact")
        try:
            value, meta = ctx.value(entry)
        except ResolveError as exc:
            missing.append({"label": label, "reason": str(exc)})
            continue
        values = _finite(value if isinstance(value, (list, tuple)) else [value])
        rows.append({"label": label, "value": values[0] if values else None,
                     "values": values, "provenance": meta, "unit": entry.get("unit")})
    detail = {"entries": rows, "missing": missing, "tolerance": tol,
              "parameter": spec.get("parameter")}
    present = [r for r in rows if r["value"] is not None]
    if len(present) < 2:
        return _result(spec, detail, WITHHELD,
                       [_fracture(spec["id"], "insufficient_sides",
                                  f"共享参数 {spec.get('parameter')} 仅 {len(present)} 侧可解析，无法互裁", detail)])
    ref = present[0]
    spread = max(rel_diff(ref["value"], r["value"]) for r in present[1:])
    detail["reference"] = {"label": ref["label"], "value": ref["value"]}
    detail["max_rel_diff"] = spread
    if spread <= tol:
        return _result(spec, detail, PASS)
    worst = max(present[1:], key=lambda r: rel_diff(ref["value"], r["value"]))
    msg = (f"共享参数 {spec.get('parameter')} 跨节点不一致：{ref['label']}={ref['value']:.6g} vs "
           f"{worst['label']}={worst['value']:.6g}（相对差 {spread * 100:.2f}% > {tol * 100:.2f}%）")
    return _result(spec, detail, FAIL, [_fracture(spec["id"], "shared_parameter_mismatch", msg, detail)])


def check_order_coverage(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    try:
        required, rmeta = ctx.value(spec["required"])
        provided, pmeta = ctx.value(spec["provided"])
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    req = [int(round(float(v))) for v in _finite(required if isinstance(required, (list, tuple)) else [required])]
    pro = [int(round(float(v))) for v in _finite(provided if isinstance(provided, (list, tuple)) else [provided])]
    missing = sorted(set(req) - set(pro))
    detail = {"required_orders": req, "provided_orders": pro, "missing_orders": missing,
              "provenance": {"required": rmeta, "provided": pmeta},
              "label": spec.get("label")}
    if not req:
        return _result(spec, detail, WITHHELD,
                       [_fracture(spec["id"], "empty_requirement", "激励阶次需求为空", detail)])
    if not missing:
        return _result(spec, detail, PASS)
    return _result(spec, detail, FAIL,
                   [_fracture(spec["id"], "excitation_order_missing",
                              f"下游受迫响应缺激励阶次 {missing}（已含 {pro}）", detail)])


def check_band_overlap(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    try:
        a_lo, a_meta = ctx.value(spec["band_a"]["low"])
        a_hi, _ = ctx.value(spec["band_a"]["high"])
        b_lo, b_meta = ctx.value(spec["band_b"]["low"])
        b_hi, _ = ctx.value(spec["band_b"]["high"])
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    a = sorted([float(a_lo), float(a_hi)])
    b = sorted([float(b_lo), float(b_hi)])
    overlap = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    narrower = min(a[1] - a[0], b[1] - b[0])
    fraction = 1.0 if narrower == 0 and overlap >= 0 else (overlap / narrower if narrower else 0.0)
    expect = spec.get("expect", "overlap")
    detail = {"band_a": a, "band_b": b, "overlap": overlap, "overlap_fraction": fraction,
              "expect": expect, "provenance": {"band_a": a_meta, "band_b": b_meta},
              "unit": spec.get("unit")}
    min_fraction = float(spec.get("min_fraction", 0.0))
    if expect == "overlap":
        ok = fraction > 0.0 and fraction >= min_fraction
        msg = f"工作带 {a} 与禁带 {b} 重叠比例 {fraction * 100:.1f}%（需 >0）"
    else:
        ok = fraction == 0.0
        msg = f"工作带 {a} 与禁带 {b} 重叠比例 {fraction * 100:.1f}%（要求完全不重叠）"
    if ok:
        return _result(spec, detail, PASS)
    return _result(spec, detail, FAIL, [_fracture(spec["id"], "band_expectation_violated", msg, detail)])


def check_additivity(spec: Mapping, ctx: Ctx) -> Dict[str, Any]:
    tol = float(spec.get("tolerance", 0.03))
    try:
        components, cmeta = ctx.value(spec["components"])
        total, tmeta = ctx.value(spec["total"])
    except ResolveError as exc:
        return _result(spec, {"error": str(exc)}, WITHHELD,
                       [_fracture(spec["id"], "unresolvable", str(exc), {})])
    comp = _finite(components if isinstance(components, (list, tuple)) else [components])
    tot = _finite(total if isinstance(total, (list, tuple)) else [total])
    if not comp or not tot:
        return _result(spec, {"components": comp, "total": tot}, WITHHELD,
                       [_fracture(spec["id"], "empty_series", "叠加账本缺分量或合计", {})])
    total_value = tot[0]
    residual = abs(total_value - sum(comp)) / max(abs(total_value), 1e-30)
    detail = {"components": comp, "component_sum": sum(comp), "total": total_value,
              "residual": residual, "tolerance": tol,
              "provenance": {"components": cmeta, "total": tmeta}}
    if residual <= tol:
        return _result(spec, detail, PASS)
    return _result(spec, detail, FAIL,
                   [_fracture(spec["id"], "additivity_residual",
                              f"叠加残差 {residual * 100:.2f}% > {tol * 100:.2f}%", detail)])


CHECKS = {
    "artifact_present": check_artifact_present,
    "criterion_inheritance": check_criterion_inheritance,
    "series_compare": check_series_compare,
    "reference_band": check_reference_band,
    "trend_agreement": check_trend_agreement,
    "field_contract": check_field_contract,
    "shared_parameter": check_shared_parameter,
    "order_coverage": check_order_coverage,
    "band_overlap": check_band_overlap,
    "additivity": check_additivity,
}


def run_check(spec: Mapping[str, Any], ctx: Ctx) -> Dict[str, Any]:
    kind = spec.get("kind")
    if kind not in CHECKS:
        return _result(spec, {"error": f"unknown check kind {kind!r}"}, WITHHELD,
                       [_fracture(spec["id"], "unknown_kind", f"未注册的检查类型 {kind!r}", {})])
    try:
        return CHECKS[kind](spec, ctx)
    except Exception as exc:  # fail-closed: a crashing check never votes PASS
        return _result(spec, {"error": f"{type(exc).__name__}: {exc}"}, WITHHELD,
                       [_fracture(spec["id"], "check_error", f"{type(exc).__name__}: {exc}", {})])