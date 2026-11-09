"""互裁代数：轨迹/标量一致性检查 (零领域语义)。

六类检查 (由 ``data/campaigns/<c>/pairs.json`` 声明)：

* ``trace_agreement``   — 两条轨迹在共同参数网格上，A 经声明的变换后与 B 逐点比，
                           报最大相对偏差；≤tol → AGREE。
* ``endpoint_agreement``— 只比参数区间端点 (稠密/稀疏极限)，检查"极限口径"是否一致。
* ``jump_ratio``        — 相邻点比值最大值，检出分支切换造成的不连续 (advisory 常用)。
* ``metric_below``      — 某个标量指标 vs 阈值 (可声明平台侧既有约定出处对标)。
* ``profile_deviation`` — 逐点最大绝对偏差 (场/剖面一致性)。
* ``degenerate``        — 退化检测：驱动量非零而响应恒零 ⇒ 该侧只是占位实现。

相对偏差统一定义 ``|a-b| / max(|a|,|b|,eps)``；分母退化 (双零) 记 0。
所有检查在引用缺失/轨迹 error 时返回 ``WITHHELD`` 并写明缺什么，绝不当 AGREE。
"""

from __future__ import annotations

from typing import Any

from .model import CheckResult, Trace

_EPS = 1e-300


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), _EPS)


def _series_map(tr: Trace) -> dict[float, float]:
    return {round(float(p), 12): float(v) for p, v in tr.series()}


def _apply_transform(x: float, param: float, spec: dict[str, Any] | None) -> float:
    """对 A 侧施加声明变换，使两侧口径可比。"""
    spec = spec or {"op": "identity"}
    op = spec.get("op", "identity")
    if op == "identity":
        return x
    if op == "scale":
        power = float(spec.get("power", 1.0))
        coef = float(spec.get("coef", 1.0))
        return x * coef * (param ** power)
    if op == "affine":
        return float(spec.get("coef", 1.0)) * x + float(spec.get("offset", 0.0))
    raise ValueError(f"unknown transform op {op!r}")


def _get_trace(traces: dict[str, Trace], tid: str) -> tuple[Trace | None, str]:
    tr = traces.get(tid)
    if tr is None:
        return None, f"trace {tid!r} not produced"
    if tr.status != "ok":
        return None, f"trace {tid!r} status={tr.status}: {tr.error}"
    return tr, ""


def check_trace_agreement(spec: dict[str, Any], traces: dict[str, Trace]) -> CheckResult:
    ta, e1 = _get_trace(traces, spec["a"])
    tb, e2 = _get_trace(traces, spec["b"])
    sev = spec.get("severity", "blocking")
    if ta is None or tb is None:
        return CheckResult("trace_agreement", "WITHHELD", sev,
                           {"missing": [e for e in (e1, e2) if e]},
                           "一侧轨迹不可用，不得判 AGREE")
    ma, mb = _series_map(ta), _series_map(tb)
    rng = spec.get("param_subset") or {}
    lo, hi = rng.get("min", -float("inf")), rng.get("max", float("inf"))
    rows, worst = [], {"param": None, "rel_dev": 0.0}
    for p in sorted(set(ma) & set(mb)):
        if not (lo <= p <= hi):
            continue
        a = _apply_transform(ma[p], p, spec.get("transform"))
        b = mb[p]
        d = _rel(a, b)
        rows.append({"param": p, "a_transformed": a, "b": b, "rel_dev": d})
        if d > worst["rel_dev"]:
            worst = {"param": p, "rel_dev": d}
    if not rows:
        return CheckResult("trace_agreement", "WITHHELD", sev, {"reason": "no overlapping params"},
                           "两条轨迹无共同参数点")
    tol = float(spec.get("tol", 1e-9))
    ok = worst["rel_dev"] <= tol
    return CheckResult("trace_agreement", "AGREE" if ok else "DISAGREE", sev,
                       {"tol": tol, "max_rel_dev": worst["rel_dev"], "at_param": worst["param"],
                        "n_points": len(rows), "rows": rows,
                        "transform": spec.get("transform") or {"op": "identity"},
                        "a": spec["a"], "b": spec["b"]},
                       spec.get("note", ""))


def check_endpoint_agreement(spec: dict[str, Any], traces: dict[str, Trace]) -> CheckResult:
    ta, e1 = _get_trace(traces, spec["a"])
    tb, e2 = _get_trace(traces, spec["b"])
    sev = spec.get("severity", "blocking")
    if ta is None or tb is None:
        return CheckResult("endpoint_agreement", "WITHHELD", sev,
                           {"missing": [e for e in (e1, e2) if e]}, "一侧轨迹不可用")
    ma, mb = _series_map(ta), _series_map(tb)
    common = sorted(set(ma) & set(mb))
    if not common:
        return CheckResult("endpoint_agreement", "WITHHELD", sev, {"reason": "no overlap"}, "")
    end = spec.get("end", "min")
    p = common[0] if end == "min" else common[-1]
    a = _apply_transform(ma[p], p, spec.get("transform"))
    b = mb[p]
    d = _rel(a, b)
    tol = float(spec.get("tol", 1e-9))
    ok = d <= tol
    return CheckResult("endpoint_agreement", "AGREE" if ok else "DISAGREE", sev,
                       {"end": end, "param": p, "a_transformed": a, "b": b,
                        "rel_dev": d, "tol": tol},
                       spec.get("note", ""))


def check_jump_ratio(spec: dict[str, Any], traces: dict[str, Trace]) -> CheckResult:
    tr, err = _get_trace(traces, spec["trace"])
    sev = spec.get("severity", "advisory")
    if tr is None:
        return CheckResult("jump_ratio", "WITHHELD", sev, {"missing": err}, "")
    ser = sorted(tr.series())
    jumps = []
    for (p0, v0), (p1, v1) in zip(ser, ser[1:]):
        r = max(abs(v1), _EPS) / max(abs(v0), _EPS)
        # 不连续的两个方向都要查：突升 r 与突降 1/r 同样是非物理跳变
        jumps.append({"param_lo": p0, "param_hi": p1, "ratio": r,
                      "ratio_dev": max(r, 1.0 / r)})
    if not jumps:
        return CheckResult("jump_ratio", "WITHHELD", sev, {"reason": "need >=2 points"}, "")
    worst = max(jumps, key=lambda j: j["ratio_dev"])
    thr = float(spec.get("max_ratio", 1.5))
    ok = worst["ratio_dev"] <= thr
    return CheckResult("jump_ratio", "AGREE" if ok else "DISAGREE", sev,
                       {"max_ratio": worst["ratio_dev"], "raw_ratio": worst["ratio"],
                        "between": [worst["param_lo"], worst["param_hi"]],
                        "threshold": thr, "jumps": jumps,
                        "monotonic": all(j["ratio"] >= 1.0 for j in jumps)
                                     or all(j["ratio"] <= 1.0 for j in jumps)},
                       spec.get("note", ""))


def check_metric_below(spec: dict[str, Any], traces: dict[str, Trace],
                       metrics: dict[str, float]) -> CheckResult:
    sev = spec.get("severity", "blocking")
    key = spec.get("metric")
    if key not in metrics:
        return CheckResult("metric_below", "WITHHELD", sev,
                           {"missing_metric": key, "available": sorted(metrics)},
                           "指标不可用 (上游轨迹 error 或未产出)")
    v = float(metrics[key])
    thr = float(spec["threshold"])
    ok = v <= thr
    return CheckResult("metric_below", "AGREE" if ok else "DISAGREE", sev,
                       {"metric": key, "value": v, "threshold": thr,
                        "counterpart": spec.get("counterpart", ""),
                        "ratio_to_threshold": (v / thr) if thr else None},
                       spec.get("note", ""))


def check_profile_deviation(spec: dict[str, Any], traces: dict[str, Trace]) -> CheckResult:
    ta, e1 = _get_trace(traces, spec["a"])
    tb, e2 = _get_trace(traces, spec["b"])
    sev = spec.get("severity", "blocking")
    if ta is None or tb is None:
        return CheckResult("profile_deviation", "WITHHELD", sev,
                           {"missing": [e for e in (e1, e2) if e]}, "")
    ma, mb = _series_map(ta), _series_map(tb)
    common = sorted(set(ma) & set(mb))
    if not common:
        return CheckResult("profile_deviation", "WITHHELD", sev, {"reason": "no overlap"}, "")
    devs = [{"param": p, "abs_dev": abs(ma[p] - mb[p])} for p in common]
    worst = max(devs, key=lambda d: d["abs_dev"])
    scale = max(max(abs(ma[p]), abs(mb[p])) for p in common)
    tol = float(spec.get("tol", 1e-9))
    ok = worst["abs_dev"] <= tol * max(scale, 1e-300) if spec.get("relative", True) \
        else worst["abs_dev"] <= tol
    return CheckResult("profile_deviation", "AGREE" if ok else "DISAGREE", sev,
                       {"max_abs_dev": worst["abs_dev"], "at_param": worst["param"],
                        "scale": scale, "scaled_dev": worst["abs_dev"] / max(scale, _EPS),
                        "tol": tol, "n_points": len(common)},
                       spec.get("note", ""))


def check_metric_agreement(spec: dict[str, Any], metrics: dict[str, float]) -> CheckResult:
    """两个标量指标（常来自不同实现/不同口径）的相对一致性，如能量账本两侧。"""
    sev = spec.get("severity", "blocking")
    ka, kb = spec.get("a_metric"), spec.get("b_metric")
    missing = [k for k in (ka, kb) if k not in metrics]
    if missing:
        return CheckResult("metric_agreement", "WITHHELD", sev,
                           {"missing_metrics": missing, "available": sorted(metrics)},
                           "指标不可用")
    a, b = float(metrics[ka]), float(metrics[kb])
    d = _rel(a, b)
    tol = float(spec.get("tol", 1e-9))
    return CheckResult("metric_agreement", "AGREE" if d <= tol else "DISAGREE", sev,
                       {"a_metric": ka, "a": a, "b_metric": kb, "b": b,
                        "rel_dev": d, "tol": tol},
                       spec.get("note", ""))


def check_degenerate(spec: dict[str, Any], metrics: dict[str, float]) -> CheckResult:
    """驱动量非零、响应恒零 ⇒ 该侧是占位实现 (无法与任何口径互裁)。"""
    sev = spec.get("severity", "blocking")
    dk, rk = spec.get("driver_metric"), spec.get("response_metric")
    missing = [k for k in (dk, rk) if k not in metrics]
    if missing:
        return CheckResult("degenerate", "WITHHELD", sev,
                           {"missing_metrics": missing, "available": sorted(metrics)},
                           "驱动/响应指标不可用")
    drv, rsp = float(metrics[dk]), float(metrics[rk])
    dtol = float(spec.get("driver_tol", 1e-12))
    if abs(drv) <= dtol:
        return CheckResult("degenerate", "WITHHELD", sev,
                           {"driver": drv, "driver_metric": dk},
                           "驱动量为零，退化检测不适用")
    if rsp == 0.0:
        return CheckResult("degenerate", "DEGENERATE", sev,
                           {"driver": drv, "driver_metric": dk, "response": rsp,
                            "response_metric": rk},
                           spec.get("note") or "驱动量非零而响应恒零：占位实现，无法互裁")
    return CheckResult("degenerate", "AGREE", sev,
                       {"driver": drv, "response": rsp}, spec.get("note", ""))


def run_check(spec: dict[str, Any], traces: dict[str, Trace],
              metrics: dict[str, float]) -> CheckResult:
    kind = spec["check"]
    if kind == "trace_agreement":
        return check_trace_agreement(spec, traces)
    if kind == "endpoint_agreement":
        return check_endpoint_agreement(spec, traces)
    if kind == "jump_ratio":
        return check_jump_ratio(spec, traces)
    if kind == "metric_below":
        return check_metric_below(spec, traces, metrics)
    if kind == "profile_deviation":
        return check_profile_deviation(spec, traces)
    if kind == "metric_agreement":
        return check_metric_agreement(spec, metrics)
    if kind == "degenerate":
        return check_degenerate(spec, metrics)
    raise ValueError(f"unknown check {kind!r}")  # pragma: no cover


def collect_metrics(traces: dict[str, Trace]) -> dict[str, float]:
    """把轨迹 meta 里的标量收集为 ``"<trace_id>.<key>"`` 指标命名空间。"""
    out: dict[str, float] = {}
    for tid, tr in traces.items():
        for k, v in (tr.meta or {}).items():
            if isinstance(v, bool):
                out[f"{tid}.{k}"] = float(v)
            elif isinstance(v, (int, float)):
                out[f"{tid}.{k}"] = float(v)
    return out