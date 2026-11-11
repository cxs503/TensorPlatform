"""Normalisation factor alpha_arch = T_arch / T_ref (multiplicative, log-robust).

Design notes
------------
* Timing is multiplicative: ratios, not differences, are the natural scale.
  We therefore estimate alpha on log scale (geometric mean of matched ratios),
  which is the maximum-likelihood location for log-normal timing noise and is
  insensitive to a single slow repetition.
* Pairing: observations are matched on (workload, repetition) so that slow
  drifts shared by both arms cancel.  If a pair is missing the workload is
  dropped for that arm; the estimator never silently mixes populations.
* Uncertainty: workloads are the sampling unit (cluster bootstrap).  Resampling
  repetitions alone would understate the between-workload variance of the
  architecture factor, which is the dominant term when the factor is not a
  pure constant.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np

Key = Tuple[str, int]


def _index(records: Iterable[Dict[str, Any]], arm: str) -> Dict[Key, float]:
    """(workload, repetition) -> arm-level time.

    When an arm has several hosts (e.g. two physical machines of the same
    architecture), the arm-level time of a (workload, repetition) cell is the
    geometric mean over its hosts: host-to-host offsets are nuisance, not
    architecture.  This keeps alpha an arm-level quantity while the
    mixed-effects model below keeps the per-host random intercept explicit.
    """
    acc: Dict[Key, List[float]] = defaultdict(list)
    for r in records:
        if r["arm"] != arm:
            continue
        acc[(r["workload"], int(r["repetition"]))].append(float(r["t_seconds"]))
    if not acc:
        raise ValueError(f"no records for arm {arm!r}")
    out: Dict[Key, float] = {}
    for k, vals in acc.items():
        if any(v <= 0 for v in vals):
            raise ValueError(f"non-positive timing at {k}")
        out[k] = float(math.exp(sum(math.log(v) for v in vals) / len(vals)))
    return out


def matched_log_ratios(records: Sequence[Dict[str, Any]], arm: str, ref: str) -> List[Tuple[str, float]]:
    """Log(T_arm / T_ref) matched on (workload, repetition), grouped by workload."""
    a, b = _index(records, arm), _index(records, ref)
    common = sorted(set(a) & set(b))
    if not common:
        raise ValueError(f"arms {arm!r} and {ref!r} share no (workload, repetition) pairs")
    by_wl: Dict[str, List[float]] = defaultdict(list)
    for k in common:
        if a[k] <= 0 or b[k] <= 0:
            raise ValueError(f"non-positive timing at {k}")
        by_wl[k[0]].append(math.log(a[k] / b[k]))
    return [(wl, float(np.mean(v))) for wl, v in sorted(by_wl.items())]


def estimate_alpha(records: Sequence[Dict[str, Any]], arm: str, ref: str,
                   n_boot: int = 2000, seed: int = 12345,
                   level: float = 0.95) -> Dict[str, Any]:
    """Point estimate + cluster-bootstrap CI of alpha_arch."""
    per_wl = matched_log_ratios(records, arm, ref)
    logs = np.array([v for _, v in per_wl], dtype=float)
    point = float(math.exp(logs.mean()))
    median = float(math.exp(np.median(logs)))
    rng = random.Random(seed)
    boots = []
    n = len(logs)
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        boots.append(math.exp(logs[idx].mean()))
    boots_arr = np.array(boots)
    lo = float(np.quantile(boots_arr, (1 - level) / 2))
    hi = float(np.quantile(boots_arr, 1 - (1 - level) / 2))
    # per-workload relative ratios: lets the report show whether alpha is
    # constant across the feature grid (the model assumption) at all.
    per_workload = {wl: float(math.exp(v)) for wl, v in per_wl}
    vals = np.array(list(per_workload.values()))
    spread = float(vals.std(ddof=1) / vals.mean()) if len(vals) > 1 else 0.0
    return {
        "arm": arm, "ref": ref,
        "alpha": point,
        "alpha_median": median,
        "ci_low": lo, "ci_high": hi,
        "level": level,
        "n_workloads": int(n),
        "n_pairs": int(sum(1 for r in records if r["arm"] == arm)),
        "per_workload": per_workload,
        "workload_cv": spread,
        "estimator": "geometric mean of matched log ratios, cluster bootstrap over workloads",
        "seed": seed, "n_boot": n_boot,
    }


def alpha_table(records: Sequence[Dict[str, Any]], ref: str, **kw) -> Dict[str, Dict[str, Any]]:
    arms = sorted({r["arm"] for r in records} - {ref})
    if not arms:
        raise ValueError("no non-reference arm in records")
    return {a: estimate_alpha(records, a, ref, **kw) for a in arms}


def normalise(records: Sequence[Dict[str, Any]], alphas: Dict[str, Dict[str, Any]],
              ref: str) -> List[Dict[str, Any]]:
    """T_normalized = T_raw / alpha_arch (ref arm keeps alpha = 1)."""
    scale = {ref: 1.0}
    scale.update({a: d["alpha"] for a, d in alphas.items()})
    out = []
    for r in records:
        if r["arm"] not in scale:
            raise ValueError(f"no alpha for arm {r['arm']!r}")
        q = dict(r)
        q["alpha"] = scale[r["arm"]]
        q["t_normalized"] = float(r["t_seconds"]) / scale[r["arm"]]
        out.append(q)
    return out


def relative_difference(records: Sequence[Dict[str, Any]], arm: str, ref: str,
                        field: str = "t_seconds", n_boot: int = 2000,
                        seed: int = 777, level: float = 0.95) -> Dict[str, Any]:
    """Relative performance difference (arm vs ref) in %, on log scale.

    difference = (exp(mean log ratio) - 1) * 100   (positive => arm slower)
    CI: cluster bootstrap over workloads.
    """
    per_wl = matched_log_ratios([dict(r, t_seconds=r[field]) for r in records], arm, ref)
    logs = np.array([v for _, v in per_wl], dtype=float)
    point = (math.exp(logs.mean()) - 1.0) * 100.0
    rng = random.Random(seed)
    n = len(logs)
    boots = np.array([(math.exp(logs[[rng.randrange(n) for _ in range(n)]].mean()) - 1.0) * 100.0
                      for _ in range(n_boot)])
    return {
        "arm": arm, "ref": ref, "field": field,
        "difference_pct": float(point),
        "ci_low_pct": float(np.quantile(boots, (1 - level) / 2)),
        "ci_high_pct": float(np.quantile(boots, 1 - (1 - level) / 2)),
        "level": level,
        "n_workloads": int(n),
        "per_workload_pct": {wl: (math.exp(v) - 1.0) * 100.0 for wl, v in per_wl},
    }

def per_workload_log_ratios(records: Iterable[Dict[str, Any]], arm: str, ref: str,
                            repetitions: Sequence[int] | None = None) -> Dict[str, List[float]]:
    """Log(T_arm/T_ref) per (workload, repetition), grouped by workload.

    `repetitions` restricts the fold (calibration vs evaluation) so that the
    same observation is never used both to estimate alpha and to test the
    residual difference (see coverage.py: cross-fitting).
    """
    out: Dict[str, List[float]] = defaultdict(list)
    for wl, val in matched_log_ratios(records, arm, ref):
        out[wl].append(val)
    if repetitions is not None:
        keep = set(int(r) for r in repetitions)
        a, b = _index(records, arm), _index(records, ref)
        out = defaultdict(list)
        for (wl, rep), t in a.items():
            if rep not in keep:
                continue
            k = (wl, rep)
            if k in b:
                out[wl].append(math.log(t / b[k]))
    if not out:
        raise ValueError(f"no matched pairs for {arm!r} vs {ref!r}")
    return dict(out)


# ---------------------------------------------------------------------------
# workload-resolved normalisation factor  alpha(x) = exp(gamma . x)
# ---------------------------------------------------------------------------
# The scalar estimator above assumes the architecture factor is the same for
# every workload.  On real arms that assumption is visibly violated (the same
# source built at -O0 is 4.2x slower on a small lattice and 3.8x on a large one,
# because the memory/compute mix changes with size).  The generalisation below
# keeps the definition T_norm = T_raw / alpha but lets alpha depend on the
# workload features; the scalar estimator is the special case gamma = [g0, 0, 0].

def _feature_vector(f: Dict[str, float], names: Sequence[str], cells_from_xy: bool = False) -> List[float]:
    if cells_from_xy and "cells" not in f:
        f = dict(f, cells=float(f["nx"]) * float(f["ny"]))
    return [1.0] + [math.log10(float(f[n])) for n in names]


def estimate_alpha_model(records: Iterable[Dict[str, Any]], arm: str, ref: str,
                         names: Sequence[str] = ("cells", "steps"),
                         cells_from_xy: bool = False) -> Dict[str, Any]:
    """Fit log(T_arm/T_ref) = gamma . [1, log10 features] on calibration workloads."""
    recs = list(records)
    per = per_workload_log_ratios(recs, arm, ref)
    by_wl: Dict[str, Dict[str, float]] = {}
    for r in recs:
        if r["arm"] in (arm, ref):
            by_wl.setdefault(r["workload"], r["features"])
    X, y, wls = [], [], []
    for wl, vals in sorted(per.items()):
        X.append(_feature_vector(by_wl[wl], names, cells_from_xy))
        y.append(float(np.mean(vals)))
        wls.append(wl)
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(y) < X.shape[1]:
        raise ValueError(f"need >= {X.shape[1]} calibration workloads to fit alpha(x), got {len(y)}")
    gamma, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ gamma
    return {
        "arm": arm, "ref": ref, "gamma": gamma.tolist(),
        "feature_names": list(names), "cells_from_xy": bool(cells_from_xy),
        "n_workloads": int(len(y)), "workloads": wls,
        "residual_sd_log": float(resid.std(ddof=1)) if len(y) > 1 else 0.0,
        "design_cond": float(np.linalg.cond(X)),
        "per_workload_scalar": {wl: float(math.exp(v)) for wl, v in zip(wls, y)},
    }


def alpha_at(features: Dict[str, float], model: Dict[str, Any]) -> float:
    x = _feature_vector(features, model["feature_names"], model["cells_from_xy"])
    return float(math.exp(float(np.dot(np.asarray(model["gamma"]), np.asarray(x)))))


def normalise_with_model(records: Sequence[Dict[str, Any]],
                         models: Dict[str, Dict[str, Any]], ref: str,
                         cells_from_xy: bool = False) -> List[Dict[str, Any]]:
    out = []
    for r in records:
        if r["arm"] == ref:
            a = 1.0
        elif r["arm"] in models:
            a = alpha_at(r["features"], models[r["arm"]])
        else:
            raise ValueError(f"no alpha model for arm {r['arm']!r}")
        q = dict(r)
        q["alpha"] = a
        q["t_normalized"] = float(r["t_seconds"]) / a
        out.append(q)
    return out
