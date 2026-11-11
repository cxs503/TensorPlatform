"""Pipeline: measurement -> real analysis -> injected estimator study ->
criteria -> figures -> artifacts.  `python3 -m tools.bench_norm.cli pipeline`.
"""
from __future__ import annotations

import math
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

from . import alpha as alpha_mod
from . import criteria as criteria_mod
from . import coverage as coverage_mod
from . import figures as fig_mod
from . import injection, measure, mixed_effects
from .schema import read_json, read_jsonl, write_json, write_jsonl

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "bench_norm"
CAL, EVAL = "calibration", "evaluation"


def arm_label(host: str, isa: str, model: str, tag: str = "") -> str:
    model_short = (model or "unknown").replace(" ", "")
    base = f"{isa}@{model_short}:{host}"
    return f"{base}[{tag}]" if tag else base


def role_of(r: Dict[str, Any]) -> str:
    return r.get("meta", {}).get("role", EVAL)


def physics_check(binary: str | Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    wl = cfg["physics_check"]
    expect = float(wl.get("expect_max_rel_err", 0.01))
    res = measure.run_once(binary, wl, timeout=3600)
    return {
        "workload": wl["name"], "steps": wl["steps"],
        "u_max_num": res["u_max_num"], "u_max_ana": res["u_max_ana"],
        "u_max_rel_err": abs(res["u_max_num"] - res["u_max_ana"]) / res["u_max_ana"],
        "l2_err": res["l2_err"], "mass_drift": res["mass_drift"],
        "checksum": res["checksum"], "expect_max_rel_err": expect,
        "l2_pass": bool(res["l2_err"] < expect),
        "mass_pass": bool(res["mass_drift"] < 1e-9),
    }


def _model_var_of_arm_ratio(records: Sequence[Dict[str, Any]], arms: Sequence[str],
                            sigma_u2: float, sigma_e2: float) -> float:
    """Var(log arm/ref) from the mixed model: host term + cell-mean noise term."""
    v = 0.0
    for a in arms:
        sub = [r for r in records if r["arm"] == a]
        n_host = len({r["host"] for r in sub})
        n_cell = len({(r["workload"], int(r["repetition"])) for r in sub})
        v += sigma_u2 / max(n_host, 1) + sigma_e2 / max(n_host * n_cell, 1)
    return v


def real_analysis(records: Sequence[Dict[str, Any]], ref: str) -> Dict[str, Any]:
    arms = sorted({r["arm"] for r in records})
    if len(arms) < 2:
        raise ValueError(f"real analysis needs >=2 arms, got {arms}")
    cal = [r for r in records if role_of(r) == CAL]
    ev = [r for r in records if role_of(r) == EVAL]
    if not cal or not ev:
        raise ValueError("real records must cover both calibration and evaluation workloads")

    alphas = alpha_mod.alpha_table(cal, ref, n_boot=2000)
    models = {a: alpha_mod.estimate_alpha_model(cal, a, ref, names=("cells", "steps"),
                                                cells_from_xy=True) for a in alphas}

    def _feat(r):
        f = dict(r["features"])
        if "cells" not in f:
            f["cells"] = float(f["nx"]) * float(f["ny"])
        return f

    feats = [_feat(r) for r in records]
    X_f, cols = mixed_effects.design_log(feats, ["cells", "steps"])
    D, dcols = mixed_effects.arm_dummies([r["arm"] for r in records], ref)
    X = np.hstack([X_f, D]) if D.size else X_f
    cols = cols + dcols
    y = np.log(np.array([r["t_seconds"] for r in records]))
    groups = np.array([f"{r['host']}|{r['arm']}" for r in records])
    lmm = mixed_effects.fit_lmm(y, X, groups)
    # host random effect is only identifiable when at least one arm has >= 2 hosts
    hosts_per_arm = {a: len({r["host"] for r in records if r["arm"] == a}) for a in arms}
    lmm["hosts_per_arm"] = hosts_per_arm
    lmm["variance_components_identifiable"] = bool(max(hosts_per_arm.values()) >= 2)
    lmm["sigma_u2_raw"] = lmm["sigma_u2"]
    lmm["icc_raw"] = lmm["icc"]
    if not lmm["variance_components_identifiable"]:
        lmm["sigma_u2_note"] = ("every arm is a single host: the host random intercept is "
                                "collinear with the arm fixed effect, so sigma_u^2 / ICC are "
                                "not identifiable and are reported as None (raw optimiser "
                                "output kept in sigma_u2_raw for audit only)")
        lmm["sigma_u2"] = None
        lmm["icc"] = None
    # explanatory power of the workload features ALONE (plain OLS, no random
    # effect): this is the honest "how much do the features explain" number --
    # the LMM's random intercept would otherwise absorb the arm effect.
    lmm["r2_log_features_ols"] = mixed_effects.ols(y, X_f)["r2"]
    lmm["design_cond_features"] = float(np.linalg.cond(X_f))
    if D.size:
        lmm["r2_log_arm_ols"] = mixed_effects.ols(y, X)["r2"]

    norm_scalar = alpha_mod.normalise(records, alphas, ref)
    norm_ev_scalar = [r for r in norm_scalar if role_of(r) == EVAL]
    norm_all = alpha_mod.normalise_with_model(records, models, ref, cells_from_xy=True)
    norm_ev = [r for r in norm_all if role_of(r) == EVAL]
    Xr, raw_cols = mixed_effects.design_raw([_feat(r) for r in norm_all], ["cells", "steps"])
    spec_model = mixed_effects.ols([r["t_normalized"] for r in norm_all], Xr)

    if lmm["variance_components_identifiable"]:
        var_cal = _model_var_of_arm_ratio(cal, [ref] + list(alphas), lmm["sigma_u2"], lmm["sigma_e2"])
        var_ev = _model_var_of_arm_ratio(ev, [ref] + list(alphas), lmm["sigma_u2"], lmm["sigma_e2"])
    else:
        var_cal = var_ev = None

    diffs = {}
    for a in arms:
        if a == ref:
            continue
        raw = alpha_mod.relative_difference(ev, a, ref, field="t_seconds", n_boot=2000)
        nd = alpha_mod.relative_difference(norm_ev, a, ref, field="t_normalized", n_boot=2000)
        nd_scalar = alpha_mod.relative_difference(norm_ev_scalar, a, ref,
                                                  field="t_normalized", n_boot=2000)
        log_nd = math.log1p(nd["difference_pct"] / 100.0)
        multi_host = all(len({r["host"] for r in records if r["arm"] == x}) >= 2 for x in (a, ref))
        if multi_host:
            sd = math.sqrt(var_cal + var_ev)
            model_ci = [(math.exp(log_nd - 1.96 * sd) - 1) * 100,
                        (math.exp(log_nd + 1.96 * sd) - 1) * 100]
        else:
            # one host per arm: the host random effect is collinear with the arm
            # effect, so sigma_u^2 cannot be used to build a CI for alpha.  The
            # workload bootstrap interval (which sees the workload-to-workload
            # spread of alpha) is the honest one here.
            sd = None
            model_ci = None
        diffs[a] = {
            "raw": raw, "normalised": nd,
            "normalised_scalar": nd_scalar,
            "alpha_model": models[a],
            "normalised_model_ci_pct": model_ci,
            "sigma_log_diff": sd,
            "ci_note": ("model-based CI (host random effect identified)" if multi_host else
                        "single host per arm: host and arm effects are collinear; "
                        "workload-bootstrap CI reported instead"),
        }

    return {
        "ref": ref, "arms": alphas, "alpha_models": models,
        "lmm": {k: lmm[k] for k in ("beta", "se", "ci95_low", "ci95_high", "sigma_e2",
                                    "sigma_u2", "icc", "r2_log", "r2_log_fixed_only",
                                    "design_cond", "n", "p", "n_groups", "blup", "fitted",
                                    "r2_log_features_ols", "r2_log_arm_ols",
                                    "hosts_per_arm", "variance_components_identifiable",
                                    "sigma_u2_note", "sigma_u2_raw", "icc_raw",
                                    "design_cond_features")},
        "lmm_design_cols": cols,
        "spec_model": {"cols": raw_cols, "beta": spec_model["beta"], "se": spec_model["se"],
                       "r2": spec_model["r2"], "rmse": spec_model["rmse"]},
        "diffs": diffs,
        "noise": measure.summarise_noise(records),
        "n_records": len(records),
        "n_calibration_workloads": len({r["workload"] for r in cal}),
        "n_evaluation_workloads": len({r["workload"] for r in ev}),
        "isa": sorted({r["isa"] for r in records}),
        "arm_isa": {a: sorted({r["isa"] for r in records if r["arm"] == a})[0] for a in arms},
        "hosts": sorted({r["host"] for r in records}),
    }


def run(config_dir: str | Path | None = None, reps: int | None = None,
        external: Sequence[str] = (), ref: str | None = None,
        do_measure: bool = True, n_replicates: int | None = None,
        outdir: str | Path | None = None, make_figures: bool = True,
        progress: bool = True, kernel_flags: str = "-O2", tag: str = "") -> Dict[str, Any]:
    cfg_dir = Path(config_dir or (ROOT / "tools" / "bench_norm" / "configs"))
    out = Path(outdir or ART)
    out.mkdir(parents=True, exist_ok=True)
    wl_cfg = read_json(cfg_dir / "workloads.json")
    inj_cfg = read_json(cfg_dir / "injection_scenarios.json")
    reps = int(reps or wl_cfg.get("repetitions", 9))

    built = measure.build_kernel(out / "bin", flags=kernel_flags)
    fp = measure.host_fingerprint()
    arm_local = arm_label(fp["host"], fp["isa"], fp["cpu_model"], tag)
    phys = physics_check(built["binary"], wl_cfg)

    records: List[Dict[str, Any]] = []
    local_path = out / "measurements" / f"{fp['host']}{('_' + tag) if tag else ''}.jsonl"
    if do_measure:
        got = measure.measure_grid(built["binary"], wl_cfg["workloads"], reps,
                                   arm=arm_local, host=fp["host"], isa=fp["isa"],
                                   seed=4242, progress=progress)
        write_jsonl(got, local_path)
        records.extend(got)
    elif local_path.exists():
        records.extend(read_jsonl(local_path))

    seen = {local_path.resolve()}
    for p in [Path(x) for x in external]:
        records.extend(read_jsonl(p))
        seen.add(p.resolve())
    for p in sorted((out / "measurements").glob("*.jsonl")):
        if p.resolve() in seen or p.name == "all.jsonl":
            continue
        records.extend(read_jsonl(p))
        if progress:
            print(f"[pipeline] merged {len(read_jsonl(p))} records from {p.name}", flush=True)

    ref_arm = ref or arm_local
    real = real_analysis(records, ref_arm)
    real["fingerprint"] = fp
    real["build"] = {k: v for k, v in built.items() if k != "cmd"}
    real["kernel_flags"] = kernel_flags
    real["physics_check"] = phys
    real["measurement_config"] = {"reps": reps, "workloads": wl_cfg["workloads"], "seed": 4242}
    write_json(real, out / "real_analysis.json")
    write_jsonl(records, out / "records_all.jsonl")

    # ---- injected estimator study --------------------------------------
    studies: Dict[str, Any] = {}
    roles: Dict[str, str] = {}
    for sc in inj_cfg["scenarios"]:
        roles[sc["name"]] = sc.get("role", "criterion")
        st = coverage_mod.study(sc, sc["ref"], n_replicates=int(n_replicates or sc.get("replicates", 100)),
                                base_seed=sc.get("base_seed", 2026), n_boot=400)
        studies[sc["name"]] = st
        write_json(st, out / "injection" / f"{sc['name']}.json")

    items: List[Dict[str, Any]] = []
    for name, st in studies.items():
        if roles[name] == "stress":          # robustness evidence, not scored
            continue
        items.extend(criteria_mod.from_injection(st, name))
    items.extend(criteria_mod.from_measurement(real))
    summary = criteria_mod.summarise(items)
    crit = {"criteria": items, "summary": summary, "scenario_roles": roles,
            "generated_at": time.time(), "platform": platform.platform()}
    write_json(crit, out / "criteria.json")

    # ---- figures --------------------------------------------------------
    figures_out: Dict[str, str] = {}
    if make_figures:
        figdir = out / "figs"
        alpha_sets = []
        for name, st in studies.items():
            cis = [r["alpha_ci"] for r in st["replicates"]]
            vals = [r["alpha_hat"] for r in st["replicates"]]
            alpha_sets.append({"label": "injected",
                               "items": [(st["arm"], float(np.mean(vals)),
                                          float(np.mean([c[0] for c in cis])),
                                          float(np.mean([c[1] for c in cis])))]})
        alpha_sets.append({"label": "measured",
                           "items": [(a, d["alpha"], d["ci_low"], d["ci_high"])
                                     for a, d in real["arms"].items()]})
        figures_out["alpha_bars"] = str(fig_mod.alpha_bars(alpha_sets, figdir / "fig_alpha_bars.png"))
        figures_out["fit_scatter"] = str(fig_mod.fit_scatter(
            [math.log(r["t_seconds"]) for r in records], real["lmm"]["fitted"],
            figdir / "fig_fit_scatter.png", real["lmm"]["r2_log"]))
        first = studies[inj_cfg["scenarios"][0]["name"]]
        figures_out["before_after"] = str(fig_mod.before_after_box(
            [r["diff_raw_pct"] for r in first["replicates"]],
            [r["diff_norm_pct"] for r in first["replicates"]],
            figdir / "fig_before_after.png",
            label=f"{first['arm']} vs {first['ref']} (injected, alpha_true={first['alpha_true']:.3g})"))
        for a, d in real["arms"].items():
            figures_out[f"alpha_by_workload_{a}"] = str(fig_mod.alpha_by_workload(
                d["per_workload"], figdir / f"fig_alpha_by_workload_{a.replace('/','_')}.png", a))

    result = {"criteria": crit,
              "studies": {k: {kk: vv for kk, vv in v.items() if kk != "replicates"}
                          for k, v in studies.items()},
              "real": real, "figures": figures_out}
    write_json({k: result[k] for k in ("criteria", "studies", "figures")}, out / "pipeline_summary.json")
    return result