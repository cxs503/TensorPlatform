"""Monte-Carlo validation of the estimator: alpha accuracy, model fit, CI coverage.

Two methodological points that decide whether the acceptance numbers mean
anything:

1. **Identifiability.**  alpha_arch must be calibrated on workloads where both
   architectures execute the *same* code (delta = 0).  If alpha is estimated on
   the same workloads that carry the genuine implementation difference, alpha
   absorbs delta and the "normalised difference" is zero by construction.
   Injection therefore splits the grid into `calibration` and `evaluation`
   workloads (see injection.py).

2. **Intervals must contain the estimation error of alpha.**  A bootstrap that
   resamples only evaluation workloads cannot see the host-level error of the
   calibration step, so its coverage is optimistic.  The reported interval is
   the model-based one from the mixed-effects variance components:

       Var(log alpha) = sum over arms of ( sigma_u^2/n_host + sigma_e^2/(n_host*n_cells) )
       Var(log diff)  = Var(log alpha)_cal + Var(log ratio)_eval

   The workload-bootstrap interval is reported alongside as a diagnostic.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Sequence

import numpy as np

from . import alpha as alpha_mod
from . import injection, mixed_effects

ROLE_CAL = "calibration"
ROLE_EVAL = "evaluation"


def _by_role(records: Sequence[Dict[str, Any]], role: str) -> List[Dict[str, Any]]:
    return [r for r in records if r.get("meta", {}).get("role", ROLE_EVAL) == role]


def _cells(records: Sequence[Dict[str, Any]], arm: str) -> tuple:
    """(#hosts, #(workload, repetition) cells) for one arm."""
    sub = [r for r in records if r["arm"] == arm]
    hosts = {r["host"] for r in sub}
    cells = {(r["workload"], int(r["repetition"])) for r in sub}
    return len(hosts), len(cells)


def _mean_log_ratio(records: Sequence[Dict[str, Any]], arm: str, ref: str,
                    roles: Sequence[str]) -> tuple:
    sub = [r for r in records if r.get("meta", {}).get("role", ROLE_EVAL) in roles]
    per_wl = alpha_mod.per_workload_log_ratios(sub, arm, ref)
    vals = np.array([np.mean(v) for _, v in sorted(per_wl.items())])
    return float(vals.mean()), vals, sub


def assess_replicate(scenario: Dict[str, Any], ref: str, seed: int,
                     n_boot: int = 400) -> Dict[str, Any]:
    records = injection.generate(scenario, seed=seed)
    arms = sorted({r["arm"] for r in records} - {ref})
    target = scenario.get("criterion_arm") or arms[0]
    names = list(scenario["feature_names"])

    # --- (a) production alpha estimator (calibration workloads, all pairs) --
    a_hat = alpha_mod.estimate_alpha(_by_role(records, ROLE_CAL), target, ref,
                                     n_boot=n_boot, seed=seed + 1)
    a_true = injection.true_alpha(scenario, target)
    alpha_rel_err = (a_hat["alpha"] - a_true) / a_true

    # --- (b) mixed-effects fit (log scale, all records) ---------------------
    feats = [r["features"] for r in records]
    X_f, cols = mixed_effects.design_log(feats, names)
    D, dcols = mixed_effects.arm_dummies([r["arm"] for r in records], ref)
    X = np.hstack([X_f, D]) if D.size else X_f
    cols = cols + dcols
    y = np.log(np.array([r["t_seconds"] for r in records]))
    groups = np.array([r["host"] for r in records])
    fit = mixed_effects.fit_lmm(y, X, groups)

    # --- (c)(d) held-out normalised difference -----------------------------
    log_ratio_cal, vals_cal, _ = _mean_log_ratio(records, target, ref, [ROLE_CAL])
    log_ratio_eval, vals_eval, _ = _mean_log_ratio(records, target, ref, [ROLE_EVAL])
    log_diff = log_ratio_eval - log_ratio_cal          # residual difference after normalisation

    # model-based variance of log alpha and of the evaluation ratio
    def var_of_arm_ratio(role_records):
        v = 0.0
        for a in (target, ref):
            nh, nc = _cells(role_records, a)
            v += fit["sigma_u2"] / max(nh, 1) + fit["sigma_e2"] / max(nh * nc, 1)
        return v

    var_cal = var_of_arm_ratio(_by_role(records, ROLE_CAL))
    var_eval = var_of_arm_ratio(_by_role(records, ROLE_EVAL))
    sd = math.sqrt(var_cal + var_eval)
    ci_low = (math.exp(log_diff - 1.96 * sd) - 1.0) * 100.0
    ci_high = (math.exp(log_diff + 1.96 * sd) - 1.0) * 100.0

    # diagnostic: workload bootstrap (does NOT include calibration host error)
    rng = random.Random(seed + 5)
    ncal, neval = len(vals_cal), len(vals_eval)
    boots = np.array([
        vals_eval[[rng.randrange(neval) for _ in range(neval)]].mean()
        - vals_cal[[rng.randrange(ncal) for _ in range(ncal)]].mean()
        for _ in range(n_boot)])
    bs_low = (math.exp(float(np.quantile(boots, 0.025))) - 1.0) * 100.0
    bs_high = (math.exp(float(np.quantile(boots, 0.975))) - 1.0) * 100.0

    raw = alpha_mod.relative_difference(_by_role(records, ROLE_EVAL), target, ref,
                                        field="t_seconds", n_boot=n_boot, seed=seed + 3)
    delta_true = injection.true_delta_pct(scenario, target)

    # --- declared raw-scale model (eq. 1): T_norm = T_raw/alpha + beta.f + eps
    all_alphas = {a: alpha_mod.estimate_alpha(_by_role(records, ROLE_CAL), a, ref,
                                              n_boot=50, seed=seed + 4) for a in arms}
    norm = alpha_mod.normalise(records, all_alphas, ref)
    Xr, raw_cols = mixed_effects.design_raw([r["features"] for r in norm], names)
    raw_fit = mixed_effects.ols([r["t_normalized"] for r in norm], Xr)

    return {
        "seed": seed, "arm": target, "ref": ref,
        "alpha_hat": a_hat["alpha"], "alpha_true": a_true,
        "alpha_rel_err": float(alpha_rel_err),
        "alpha_ci": [a_hat["ci_low"], a_hat["ci_high"]],
        "r2_log": fit["r2_log"], "r2_log_fixed_only": fit["r2_log_fixed_only"],
        "sigma_u2_hat": fit["sigma_u2"], "sigma_e2_hat": fit["sigma_e2"], "icc": fit["icc"],
        "r2_raw_spec_model": raw_fit["r2"],
        "diff_norm_pct": (math.exp(log_diff) - 1.0) * 100.0,
        "diff_norm_ci": [ci_low, ci_high],
        "diff_norm_ci_bootstrap": [bs_low, bs_high],
        "diff_raw_pct": raw["difference_pct"],
        "delta_true_pct": delta_true,
        "true_raw_diff_pct": injection.true_difference_pct(scenario, target),
        "coverage_hit": bool(ci_low <= delta_true <= ci_high),
        "ci_width_pct": ci_high - ci_low,
        "n_calibration_workloads": int(ncal), "n_evaluation_workloads": int(neval),
        "fit": fit, "alpha_detail": a_hat, "raw_detail": raw,
        "raw_model": {"cols": raw_cols, **raw_fit}, "design_cols": cols,
    }


def _slim(rep: Dict[str, Any]) -> Dict[str, Any]:
    """Drop per-replicate arrays (fitted values, residuals) from the artifact.

    Keeping 100 x ~700 fitted values makes the JSON ~10 MB with no audit value:
    everything the report quotes is scalar.  The full fit stays available by
    re-running the study deterministically (seed is recorded).
    """
    out = {k: v for k, v in rep.items() if k not in ("fit", "raw_detail", "raw_model")}
    if "alpha_detail" in out:
        out["alpha_detail"] = {k: v for k, v in out["alpha_detail"].items()
                               if k not in ("per_workload",)}
    return out


def study(scenario: Dict[str, Any], ref: str, n_replicates: int = 100,
          base_seed: int = 2026, n_boot: int = 400) -> Dict[str, Any]:
    reps: List[Dict[str, Any]] = [assess_replicate(scenario, ref, base_seed + i, n_boot=n_boot)
                                  for i in range(n_replicates)]
    target = reps[0]["arm"]
    alpha_err = np.array([abs(r["alpha_rel_err"]) for r in reps])
    r2 = np.array([r["r2_log"] for r in reps])
    cover = np.array([1.0 if r["coverage_hit"] else 0.0 for r in reps])
    boot_cover = np.array([1.0 if r["diff_norm_ci_bootstrap"][0] <= r["delta_true_pct"]
                           <= r["diff_norm_ci_bootstrap"][1] else 0.0 for r in reps])
    dn = np.array([abs(r["diff_norm_pct"]) for r in reps])
    dr = np.array([abs(r["diff_raw_pct"]) for r in reps])

    def q(a, p):
        return float(np.quantile(a, p))

    within5 = dn < 5.0
    return {
        "scenario": scenario.get("name", "unnamed"), "arm": target, "ref": ref,
        "n_replicates": int(n_replicates), "seed": base_seed,
        "alpha_true": reps[0]["alpha_true"],
        "delta_true_pct": reps[0]["delta_true_pct"],
        "true_raw_diff_pct": reps[0]["true_raw_diff_pct"],
        "n_calibration_workloads": reps[0]["n_calibration_workloads"],
        "n_evaluation_workloads": reps[0]["n_evaluation_workloads"],
        "alpha_rel_err": {"mean_abs": float(alpha_err.mean()), "p95_abs": q(alpha_err, 0.95),
                          "max_abs": float(alpha_err.max()),
                          "scored": "|alpha_hat/alpha_true - 1| per replicate; acceptance scored on p95"},
        "r2_log": {"mean": float(r2.mean()), "min": float(r2.min()), "p05": q(r2, 0.05)},
        "r2_raw_spec_model": {"mean": float(np.mean([r["r2_raw_spec_model"] for r in reps])),
                              "min": float(np.min([r["r2_raw_spec_model"] for r in reps]))},
        "coverage": {"hits": int(cover.sum()), "rate": float(cover.mean()), "nominal": 0.95,
                     "interval": "model-based (mixed-effects variance components, 1.96 sd)",
                     "bootstrap_interval_rate": float(boot_cover.mean()),
                     "scored": "fraction of model-based 95% CIs containing delta_true"},
        "ci_width_pct": {"mean": float(np.mean([r["ci_width_pct"] for r in reps])),
                         "median": q(np.array([r["ci_width_pct"] for r in reps]), 0.5)},
        "diff_norm_pct": {"mean_abs": float(dn.mean()), "median_abs": float(np.median(dn)),
                          "p95_abs": q(dn, 0.95), "max_abs": float(dn.max()),
                          "rate_below_5pct": float(within5.mean()),
                          "n_below_5pct": int(within5.sum())},
        "diff_raw_pct": {"mean_abs": float(dr.mean()), "median_abs": float(np.median(dr)),
                         "min_abs": float(dr.min()), "max_abs": float(dr.max())},
        "target_thresholds": {
            "alpha_err_p95<0.03": bool(q(alpha_err, 0.95) < 0.03),
            "r2_mean>0.9": bool(r2.mean() > 0.9),
            "coverage>0.9": bool(cover.mean() > 0.9),
            "raw_diff>50pct": bool(np.min(dr) > 50.0),
            "norm_diff_rate_below_5pct>=0.95": bool(within5.mean() >= 0.95),
        },
        "replicates": [_slim(r) for r in reps],
    }