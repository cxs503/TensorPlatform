"""自动判据门（fail-closed）。

判据**是数据**，语法刻意小且无语义（后续舱位可在配置里加算例，不需改本模块）::

    {"metric": "rel_error", "op": "<=", "value": 1e-6}
    {"metric": "rel_error", "op": "in_range", "min": 0.0, "max": 1e-3}
    {"metric": "iterations", "op": ">=", "value": 1}
    {"metric": "value", "op": "==", "value": 0.0, "tol": 1e-12}
    {"metric": "trajectory", "op": "monotonic", "direction": "decreasing", "tol": 0.0}
    {"metric": "residual", "op": "finite"}

fail-closed 规则（任一命中即该 check 判 FAIL，理由写进 detail）：

* 算子未知 / 阈值缺失 / 阈值类型错；
* 指标缺失（None 或 key 不存在）；
* 指标取值非有限（NaN/inf/bool/str）；
* 算例一条判据都没声明（``no_criteria``）——**无判据不得算通过**；
* SUT 抛异常、参考解非有限。

本模块不含任何具体阈值常量：所有数字都从配置读入，保证"阈值先于结果声明"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .model import Check, ConfigError, is_finite

SUPPORTED_OPS = ("<=", "<", ">=", ">", "==", "!=", "in_range", "monotonic", "finite")
_MONOTONIC_DIRECTIONS = ("increasing", "decreasing", "non_decreasing", "non_increasing")


def validate_criterion(crit: Mapping[str, Any]) -> Dict[str, Any]:
    """校验并规范化一条判据；不合法 → ConfigError（配置错，非算例失败）。"""
    if not isinstance(crit, Mapping):
        raise ConfigError(f"criterion must be a mapping, got {type(crit).__name__}")
    metric = crit.get("metric")
    op = crit.get("op")
    if not isinstance(metric, str) or not metric:
        raise ConfigError(f"criterion missing 'metric': {crit!r}")
    if op not in SUPPORTED_OPS:
        raise ConfigError(f"unknown op {op!r} for metric {metric!r}; allowed={SUPPORTED_OPS}")

    out: Dict[str, Any] = dict(crit)
    if op in ("<=", "<", ">=", ">", "==", "!="):
        v = crit.get("value")
        if not is_finite(v):
            raise ConfigError(f"op {op!r} on {metric!r} needs finite 'value', got {v!r}")
        out["value"] = float(v)
    elif op == "in_range":
        lo, hi = crit.get("min"), crit.get("max")
        if not (is_finite(lo) and is_finite(hi)):
            raise ConfigError(f"in_range on {metric!r} needs finite 'min'/'max', got {lo!r}/{hi!r}")
        if float(lo) > float(hi):
            raise ConfigError(f"in_range on {metric!r}: min {lo} > max {hi}")
        out["min"], out["max"] = float(lo), float(hi)
    elif op == "monotonic":
        direction = crit.get("direction", "increasing")
        if direction not in _MONOTONIC_DIRECTIONS:
            raise ConfigError(f"monotonic direction {direction!r} not in {_MONOTONIC_DIRECTIONS}")
        tol = crit.get("tol", 0.0)
        if not is_finite(tol) or float(tol) < 0:
            raise ConfigError(f"monotonic tol must be finite >=0, got {tol!r}")
        out["direction"], out["tol"] = direction, float(tol)
    elif op == "finite":
        pass
    return out


def _check_scalar(metric: str, op: str, actual: Any, crit: Mapping[str, Any]) -> Check:
    if actual is None:
        return Check(metric, op, False, f"metric {metric!r} missing (None)", None, crit.get("value"))
    if not is_finite(actual):
        return Check(metric, op, False, f"metric {metric!r} is non-finite/non-numeric: {actual!r}",
                     actual, crit.get("value"))
    a = float(actual)
    thr = crit.get("value")
    tol = float(crit.get("tol", 0.0) or 0.0)
    ok, want = False, ""
    if op == "<=":
        ok, want = a <= thr, f"<= {thr:g}"
    elif op == "<":
        ok, want = a < thr, f"< {thr:g}"
    elif op == ">=":
        ok, want = a >= thr, f">= {thr:g}"
    elif op == ">":
        ok, want = a > thr, f"> {thr:g}"
    elif op == "==":
        ok, want = abs(a - thr) <= tol, f"== {thr:g} (tol {tol:g})"
    elif op == "!=":
        ok, want = abs(a - thr) > tol, f"!= {thr:g} (tol {tol:g})"
    detail = f"{metric}={a:.12g} {'satisfies' if ok else 'violates'} {want}"
    return Check(metric, op, ok, detail, a, thr)


def _check_range(metric: str, actual: Any, crit: Mapping[str, Any]) -> Check:
    lo, hi = crit["min"], crit["max"]
    if actual is None:
        return Check(metric, "in_range", False, f"metric {metric!r} missing (None)", None, [lo, hi])
    if not is_finite(actual):
        return Check(metric, "in_range", False, f"metric {metric!r} non-finite: {actual!r}",
                     actual, [lo, hi])
    a = float(actual)
    ok = lo <= a <= hi
    return Check(metric, "in_range", ok,
                 f"{metric}={a:.12g} {'inside' if ok else 'outside'} [{lo:g}, {hi:g}]", a, [lo, hi])


def _check_monotonic(metric: str, actual: Any, crit: Mapping[str, Any]) -> Check:
    direction, tol = crit["direction"], crit["tol"]
    if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
        return Check(metric, "monotonic", False,
                     f"metric {metric!r} must be a sequence, got {type(actual).__name__}",
                     None, direction)
    vals = list(actual)
    if len(vals) < 2:
        return Check(metric, "monotonic", False,
                     f"metric {metric!r} needs >=2 samples, got {len(vals)}", len(vals), direction)
    if not all(is_finite(v) for v in vals):
        return Check(metric, "monotonic", False, f"metric {metric!r} contains non-finite sample",
                     vals[:8], direction)
    f = [float(v) for v in vals]
    bad: List[int] = []
    for i in range(1, len(f)):
        d = f[i] - f[i - 1]
        ok = {
            "increasing": d > 0 or (tol > 0 and abs(d) <= tol),
            "non_decreasing": d >= -tol,
            "decreasing": d < 0 or (tol > 0 and abs(d) <= tol),
            "non_increasing": d <= tol,
        }[direction]
        if not ok:
            bad.append(i)
    detail = (f"{metric} strictly follows {direction} over {len(f)} samples"
              if not bad else
              f"{metric} breaks {direction} at indices {bad[:5]} (n={len(f)}); "
              f"pair {f[bad[0]-1]:.6g}->{f[bad[0]]:.6g}")
    return Check(metric, "monotonic", not bad, detail, {"n": len(f), "violations": bad[:5]}, direction)


def _check_finite(metric: str, actual: Any) -> Check:
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes)):
        ok = len(actual) > 0 and all(is_finite(v) for v in actual)
    else:
        ok = is_finite(actual)
    return Check(metric, "finite", ok, f"{metric} {'all finite' if ok else 'non-finite/missing'}",
                 actual if not isinstance(actual, Sequence) else str(actual)[:120], None)


def evaluate(criteria: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any],
             *, label: str = "") -> List[Check]:
    """按序评估判据；**无判据 → 返回一条 FAIL**（fail-closed，禁止空判据通过）。"""
    crits = list(criteria or [])
    if not crits:
        return [Check("__none__", "__no_criteria__", False,
                      f"{label or 'case'} declares no criteria: no-judgement is not a pass")]
    out: List[Check] = []
    for raw in crits:
        crit = validate_criterion(raw)          # 已在加载期校验，这里再校一次（幂等）
        metric, op = crit["metric"], crit["op"]
        actual = metrics.get(metric)
        if op in ("<=", "<", ">=", ">", "==", "!="):
            out.append(_check_scalar(metric, op, actual, crit))
        elif op == "in_range":
            out.append(_check_range(metric, actual, crit))
        elif op == "monotonic":
            out.append(_check_monotonic(metric, actual, crit))
        elif op == "finite":
            out.append(_check_finite(metric, actual))
    return out


def verdict_of(checks: Sequence[Check]) -> bool:
    """全 check 通过才算通过；空列表 = 不通过（fail-closed）。"""
    return bool(checks) and all(c.passed for c in checks)