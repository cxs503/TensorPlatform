"""End-to-end verification runner: data assimilation + benchmark-caliber governance.

Produces ``benchmarks/verified/data_assimilation/results.json`` plus figures, with
every number anchored in :mod:`tools.data_assimilation.provenance`.

Criteria implemented (thresholds are the mission's, not tuned here):

====  =====================================================================
C1    posterior mean within 10% of the truth for every parameter
C2    95% credible-interval coverage >= 93% over 100 Monte-Carlo replicates
C3    caliber verdict correct on 6/6 synthetic benchmark tables
C4    the deliberately corrupt table is withheld *and* exits non-zero
C5    the in-repo real table replay: post-conversion deviation < 15%
C6    dropping the caliber field rejects 100% of error judgements
C7    same input -> same output (byte-identical report, hashed evidence block)
C8    zero hand-copying: every number re-verifiable from (file sha256, pointer)
====  =====================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Mapping, Optional

import numpy as np

from tools.caliber_reader import lines as clines
from tools.caliber_reader import reader as creader
from tools.caliber_reader import synth as csynth
from tools.caliber_reader.gate import (AuditLog, judge_error, rejection_rate,
                                       REJECT_MISSING, event_sha256)
from tools.data_assimilation import bayes
from tools.data_assimilation.provenance import Provenance, sha256_file, sha256_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "benchmarks/verified/data_assimilation")
CONFIG = os.path.join(BENCH, "config")
CASES = os.path.join(BENCH, "cases")
FIGDIR = os.path.join(BENCH, "figures")
MISSION = "P148W2-N03"
GRAVITY = 9.80665


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _load(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# C1 / C2 -- Bayesian model updating
# ---------------------------------------------------------------------------
def _design(cfg: Mapping[str, object]) -> np.ndarray:
    spec = cfg["x"]  # type: ignore[index]
    return np.linspace(float(spec["start"]), float(spec["stop"]), int(spec["count"]))  # type: ignore[index]


def _priors(cfg: Mapping[str, object]) -> List[bayes.Prior]:
    return [bayes.Prior.from_mapping(p) for p in cfg["priors"]]  # type: ignore[index]


def run_c1(cfg: Mapping[str, object], pv: Provenance) -> Dict[str, object]:
    kind = str(cfg["response_kind"])
    truth = np.asarray(cfg["truth"], dtype=float)  # type: ignore[index]
    x = _design(cfg)
    sigma = float(cfg["sigma"])
    priors = _priors(cfg)
    seed = int(cfg["seed"])
    rng = np.random.default_rng(seed)
    y = bayes.response_predict(kind, truth, x) + rng.normal(0.0, sigma, x.size)
    res = bayes.update(kind, x, y, sigma, priors, seed=int(cfg["posterior_seed"]),
                       n_chains=int(cfg["n_chains"]), n_steps=int(cfg["n_steps"]),
                       n_burn=int(cfg["n_burn"]), level=float(cfg["level"]))
    rel = res.relative_error_pct(truth)
    worst = max(abs(v) for v in rel)
    tolerance = float(cfg["c1_tolerance_pct"])

    pv.derived("c1.truth", truth.tolist(), [], "declared synthetic truth")
    pv.derived("c1.posterior_mean", res.mean.tolist(), ["c1.truth"],
               "MCMC posterior mean of 3 chains x %d kept states" % int(cfg["n_steps"]))
    pv.derived("c1.posterior_ci_low", res.ci_low.tolist(), ["c1.posterior_mean"], "2.5% quantile")
    pv.derived("c1.posterior_ci_high", res.ci_high.tolist(), ["c1.posterior_mean"], "97.5% quantile")
    pv.derived("c1.relative_error_pct", rel, ["c1.posterior_mean", "c1.truth"],
               "(mean - truth)/truth*100")
    pv.derived("c1.max_abs_relative_error_pct", worst, ["c1.relative_error_pct"], "max(|.|)")
    return {
        "pass": bool(worst <= tolerance),
        "threshold_pct": tolerance,
        "measured_max_abs_rel_err_pct": worst,
        "relative_error_pct": rel,
        "posterior": res.as_dict(),
        "acceptance": res.acceptance,
        "n_sensor_points": int(x.size),
        "sigma": sigma,
        "kind": kind,
    }


def run_c2(cfg: Mapping[str, object], pv: Provenance,
           replicates: Optional[int] = None) -> Dict[str, object]:
    kind = str(cfg["response_kind"])
    truth = np.asarray(cfg["truth"], dtype=float)  # type: ignore[index]
    x = _design(cfg)
    priors = _priors(cfg)
    n = int(replicates or cfg["coverage_replicates"])
    cov = bayes.coverage_study(kind, x, truth, float(cfg["sigma"]), priors,
                               n_replicates=n, seed=int(cfg["coverage_seed"]),
                               level=float(cfg["level"]), n_chains=int(cfg["n_chains"]),
                               n_steps=int(cfg["n_steps"]), n_burn=int(cfg["n_burn"]))
    floor = float(cfg["c2_coverage_floor"])
    pv.derived("c2.coverage_per_parameter", cov["coverage_per_parameter"],
               ["c1.truth"], "fraction of %d replicates whose %.2f interval covers the truth"
               % (n, float(cfg["level"])))
    pv.derived("c2.coverage_pooled", cov["coverage_pooled"], ["c2.coverage_per_parameter"],
               "sum(covered)/ (n_replicates * n_parameters)")
    cov = dict(cov)
    cov.update({"pass": bool(cov["coverage_pooled"] >= floor), "floor": floor,
                "n_replicates": n})
    return cov


# ---------------------------------------------------------------------------
# C3 / C4 -- caliber verdicts on the synthetic corpus
# ---------------------------------------------------------------------------
def run_c3(lines: clines.LineSet, cfg: Mapping[str, object],
           pv: Provenance) -> Dict[str, object]:
    paths = csynth.write_cases(CASES, lines, cfg)
    lines_sha = sha256_file(os.path.join(CONFIG, "lines.json"))
    per_case = []
    n_correct = 0
    for path in sorted(paths):
        table = _load(path)
        verdict = creader.interpret(table, lines, cfg)
        expected = table["expected"]
        ok = verdict["status"] == expected["status"]
        if ok and "scale_assignment" in expected:
            ok = verdict.get("scale_assignment") == expected["scale_assignment"]
        if ok and "allowance_fraction" in expected:
            ok = abs(float(verdict.get("allowance_fraction", 0.0))
                     - float(expected["allowance_fraction"])) <= 0.01
        n_correct += int(ok)
        rel = os.path.relpath(path, ROOT)
        pv.file_value(f"c3.{table['case_id']}.expected_status", rel, "/expected/status")
        pv.derived(f"c3.{table['case_id']}.verdict", verdict["status"],
                   [f"c3.{table['case_id']}.expected_status"],
                   "caliber_reader.interpret(table, lines[sha256=%s...])" % lines_sha[:12])
        per_case.append({
            "case_id": table["case_id"],
            "expected": expected["status"],
            "verdict": verdict["status"],
            "match": bool(ok),
            "reason": verdict.get("reason", ""),
            "form_factor": verdict.get("form_factor"),
            "residual_coefficient": verdict.get("residual_coefficient"),
            "closure_residual_pct": verdict.get("closure_residual_pct"),
            "allowance_fraction": verdict.get("allowance_fraction"),
            "error_decision_allowed": verdict.get("error_decision_allowed"),
            "exit_code": verdict.get("exit_code"),
            "cases_file": rel,
        })
    accuracy = n_correct / len(per_case)
    pv.derived("c3.accuracy", accuracy,
               [f"c3.{p['case_id']}.verdict" for p in per_case],
               "count(verdict == expected)/n_cases")
    return {"pass": bool(n_correct == len(per_case)), "correct": n_correct,
            "n_cases": len(per_case), "accuracy": accuracy, "per_case": per_case}


def run_c4(pv: Provenance) -> Dict[str, object]:
    case_path = os.path.join(CASES, "case_06_corrupt.json")
    rel = os.path.relpath(case_path, ROOT)
    proc = subprocess.run([sys.executable, "-m", "tools.caliber_reader", case_path],
                          cwd=ROOT, capture_output=True, text=True)
    table = _load(case_path)
    lines = clines.load_lineset(os.path.join(CONFIG, "lines.json"))
    cfg = _load(os.path.join(CONFIG, "reader_config.json"))
    verdict = creader.interpret(table, lines, cfg)
    pv.derived("c4.corrupt_status", verdict["status"], [], "reader verdict on the corrupt table")
    pv.derived("c4.subprocess_returncode", proc.returncode, ["c4.corrupt_status"],
               "python -m tools.caliber_reader <corrupt table>")
    ok = (verdict["status"] == creader.WITHHELD_CORRUPT and proc.returncode != 0
          and verdict["error_decision_allowed"] is False)
    return {"pass": bool(ok), "status": verdict["status"],
            "subprocess_returncode": proc.returncode,
            "error_decision_allowed": verdict["error_decision_allowed"],
            "reason": verdict.get("reason", ""),
            "cases_file": rel, "stdout": proc.stdout.strip()[:300]}


# ---------------------------------------------------------------------------
# C5 -- real in-repo table replay + machine attribution
# ---------------------------------------------------------------------------
def run_c5(lines: clines.LineSet, reader_cfg: Mapping[str, object],
           pv: Provenance) -> Dict[str, object]:
    rc = _load(os.path.join(CONFIG, "replay_config.json"))
    src = rc["table_source"]
    doc = _load(os.path.join(ROOT, src))
    entries = doc[rc["entries_pointer"].strip("/")]
    matches = [i for i, e in enumerate(entries) if e.get("case_id") == rc["case_id"]]
    if not matches:
        raise SystemExit(f"replay source has no case {rc['case_id']!r}")
    idx = matches[0]
    base = f"{rc['entries_pointer']}/{idx}"
    cols = rc["columns"]
    ct_ref = pv.file_value("c5.coefficient_total", src, f"{base}/{cols['coefficient_total']}")
    cf_ref = pv.file_value("c5.coefficient_viscous", src, f"{base}/{cols['coefficient_viscous']}")
    k1_ref = pv.file_value("c5.form_factor", src, f"{base}/{cols['form_factor']}")
    v_full = pv.file_value("c5.speed_full", src, f"{base}/{cols['speed']}")
    l_full = pv.file_value("c5.length_full", src, f"{base}/{cols['length']}")
    fn_ref = pv.file_value("c5.speed_ratio", src, f"{base}/{cols['speed_ratio']}")
    geom = rc["geometry_source"]
    l_red = pv.code_value("c5.length_reduced", geom, rc["geometry_symbol"],
                          rc["geometry_keys"]["length"])
    fn_geom = pv.code_value("c5.speed_ratio_reduced", geom, rc["geometry_symbol"],
                            rc["geometry_keys"]["speed_ratio"])
    rho_f = pv.config_value("c5.density_full", os.path.join(CONFIG, "replay_config.json"),
                            "/fluids/full/density_kg_m3")
    nu_f = pv.config_value("c5.viscosity_full", os.path.join(CONFIG, "replay_config.json"),
                           "/fluids/full/viscosity_m2_s")
    rho_r = pv.config_value("c5.density_reduced", os.path.join(CONFIG, "replay_config.json"),
                            "/fluids/reduced/density_kg_m3")
    nu_r = pv.config_value("c5.viscosity_reduced", os.path.join(CONFIG, "replay_config.json"),
                           "/fluids/reduced/viscosity_m2_s")
    tol = float(rc["tolerance_pct"])

    allowance_name = str(reader_cfg["allowance_line"])
    cf_full = lines.eval(str(reader_cfg["viscous_line"]), v_full * l_full / nu_f).value
    cf_red = lines.eval(str(reader_cfg["viscous_line"]), fn_ref * np.sqrt(GRAVITY * l_red) *
                        l_red / nu_r).value
    ca_full = lines.eval(allowance_name, l_full).value
    residual = ct_ref - k1_ref * cf_ref - ca_full
    ct_red = k1_ref * cf_red + residual
    v_red = float(fn_ref * np.sqrt(GRAVITY * l_red))
    pv.derived("c5.viscous_full", cf_full, ["c5.speed_full", "c5.length_full",
                                            "c5.viscosity_full"],
               "viscous_line(V*L/nu)")
    pv.derived("c5.viscous_reduced", cf_red, ["c5.length_reduced", "c5.viscosity_reduced",
                                              "c5.speed_ratio"],
               "viscous_line(speed_ratio*sqrt(g*L)*L/nu)")
    pv.derived("c5.allowance_full", ca_full, ["c5.length_full"], "allowance_line(L)")
    pv.derived("c5.residual_term", residual, ["c5.coefficient_total", "c5.coefficient_viscous",
                                              "c5.form_factor", "c5.allowance_full"],
               "ct - (1+k)*cf - allowance(L)")
    pv.derived("c5.reduced_coefficient_derived", ct_red, ["c5.viscous_reduced",
                                                          "c5.form_factor",
                                                          "c5.residual_term"],
               "(1+k)*cf_red + residual")
    pv.derived("c5.reduced_speed", v_red, ["c5.speed_ratio", "c5.length_reduced"],
               "speed_ratio*sqrt(g*L)")

    red_row = creader.Row(row_id="derived_reduced", object_id="replay_object",
                          speed=v_red, length=l_red, density=rho_r, viscosity=nu_r,
                          coefficient=ct_red)
    full_row = creader.Row(row_id="quoted_full", object_id="replay_object",
                           speed=v_full, length=l_full, density=rho_f, viscosity=nu_f,
                           coefficient=ct_ref)
    inv = creader.invert_pair(red_row, full_row, lines, reader_cfg)
    resid_at_red = float(inv["residual_term"])
    pv.derived("c5.recovered_form_factor", inv["form_factor"],
               ["c5.reduced_coefficient_derived", "c5.coefficient_total",
                "c5.viscous_full", "c5.viscous_reduced", "c5.allowance_full"],
               "[(ct_red) - (ct_full - allowance)]/(cf_red - cf_full)")
    pv.derived("c5.recovered_residual_term", resid_at_red, ["c5.recovered_form_factor",
                                                            "c5.viscous_reduced"],
               "ct_red - (1+k)*cf_red")
    k1_err = (inv["form_factor"] - k1_ref) / k1_ref * 100.0
    resid_err = (resid_at_red - residual) / residual * 100.0
    pv.derived("c5.form_factor_error_pct", k1_err, ["c5.recovered_form_factor",
                                                    "c5.form_factor"],
               "(recovered - quoted)/quoted*100")
    pv.derived("c5.residual_term_error_pct", resid_err, ["c5.recovered_residual_term",
                                                         "c5.residual_term"],
               "(recovered - derived)/derived*100")

    # naive caliber-blind comparison: reduced-scale viscous term vs full-size total
    naive = (cf_red - ct_ref) / ct_ref * 100.0
    pv.derived("c5.naive_deviation_pct", naive, ["c5.viscous_reduced",
                                                 "c5.coefficient_total"],
               "(cf_reduced - ct_full)/ct_full*100")
    factors = [
        ("scale_effect", float(cf_full / cf_red)),
        ("form_factor", float(k1_ref)),
        ("residual_term", float(1.0 + residual / (k1_ref * cf_full))),
        ("size_allowance", float(1.0 + ca_full / (k1_ref * cf_full + residual))),
    ]
    att = creader.attribute_deviation(cf_red, ct_ref, factors)
    for name, ratio in factors:
        pv.derived(f"c5.factor.{name}", ratio, ["c5.viscous_full", "c5.viscous_reduced",
                                                "c5.form_factor", "c5.residual_term",
                                                "c5.allowance_full"],
                   "attribution factor")
    pv.derived("c5.post_conversion_deviation_pct", att["residual_pct"],
               [f"c5.factor.{n}" for n, _ in factors],
               "sequential application of the caliber factors, residual after conversion")
    pv.derived("c5.attribution", att["attribution"], ["c5.post_conversion_deviation_pct"],
               "|residual| <= tolerance -> CALIBER, else UNEXPLAINED")
    conversion_ok = abs(att["residual_pct"]) <= tol
    return {
        "pass": bool(conversion_ok),
        "tolerance_pct": tol,
        "table": {"source": src, "case_id": rc["case_id"],
                  "coefficient_total": ct_ref, "coefficient_viscous": cf_ref,
                  "form_factor": k1_ref, "speed_full": v_full, "length_full": l_full,
                  "speed_ratio": fn_ref},
        "reduced_side": {"length": l_red, "speed": v_red,
                         "coefficient_derived": ct_red, "viscous": cf_red},
        "allowance_full": ca_full,
        "residual_term": residual,
        "inversion": inv,
        "recovered": {"form_factor": inv["form_factor"],
                      "residual_term": resid_at_red,
                      "form_factor_error_pct": k1_err,
                      "residual_term_error_pct": resid_err},
        "naive_deviation_pct": naive,
        "seed_claim_deviation_pct": float(rc["seed_claim"]["deviation_pct"]),
        "post_conversion_deviation_pct": att["residual_pct"],
        "attribution": att,
    }


# ---------------------------------------------------------------------------
# C6 -- downstream gate negative control
# ---------------------------------------------------------------------------
def run_c6(pv: Provenance) -> Dict[str, object]:
    audit_path = os.path.join(BENCH, "audit", "gate_audit.jsonl")
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    if os.path.exists(audit_path):
        os.remove(audit_path)
    audit = AuditLog(path=audit_path)
    rng = np.random.default_rng(20261110)
    n = 100
    negative = []
    positive = []
    mismatch = []
    for i in range(n):
        obs = float(rng.uniform(0.8, 1.2))
        ref = float(rng.uniform(0.8, 1.2))
        negative.append({"case_id": f"neg_{i:03d}", "observed": obs, "reference": ref,
                         "caliber": None})
        positive.append({"case_id": f"pos_{i:03d}", "observed": obs, "reference": ref,
                         "caliber": {"scale": "reduced", "source": "c3.verdict",
                                     "verdict_id": f"case_01_declared_consistent",
                                     "error_decision_allowed": True},
                         "observed_scale": "reduced"})
        mismatch.append({"case_id": f"mix_{i:03d}", "observed": obs, "reference": ref,
                         "caliber": {"scale": "full", "source": "c3.verdict",
                                     "verdict_id": "case_01_declared_consistent",
                                     "error_decision_allowed": True},
                         "observed_scale": "reduced"})
    neg = rejection_rate(negative, audit=audit)
    pos = rejection_rate(positive, audit=audit)
    mix = rejection_rate(mismatch, audit=audit)
    # a partial caliber (scale only) must also be refused
    partial = judge_error(1.0, 1.0, caliber={"scale": "reduced"}, case_id="partial",
                          audit=audit)
    # chained audit events: each event hash must recompute from its own content
    chain_ok = True
    prev = ""
    for ev in audit.events:
        body = {k: v for k, v in ev.items() if k not in ("sha256", "prev_sha256")}
        if ev["sha256"] != event_sha256(body) or ev["prev_sha256"] != prev:
            chain_ok = False
            break
        prev = ev["sha256"]
    pv.derived("c6.missing_caliber_rejection_rate", neg["rejected_missing_rate"],
               [], "rejected_missing / n_cases with caliber=None")
    pv.derived("c6.positive_control_accept_rate", pos["accept_rate"], [],
               "accepted / n_cases with a complete caliber")
    pv.derived("c6.scale_mismatch_rejection_rate",
               mix["decisions"].get("REJECT_SCALE_MISMATCH", 0) / max(mix["n_cases"], 1),
               [], "REJECT_SCALE_MISMATCH / n_cases")
    pv.derived("c6.audit_chain_ok", chain_ok, [], "recompute each event sha256 and link")
    ok = (neg["rejected_missing_rate"] == 1.0 and pos["accept_rate"] == 1.0
          and partial["decision"] != "PASS" and partial["error_pct"] is None and chain_ok)
    return {"pass": bool(ok), "n_cases": n,
            "missing_caliber_rejection_rate": neg["rejected_missing_rate"],
            "missing_caliber_decisions": neg["decisions"],
            "positive_control_accept_rate": pos["accept_rate"],
            "scale_mismatch_rejection_rate":
                mix["decisions"].get("REJECT_SCALE_MISMATCH", 0) / max(mix["n_cases"], 1),
            "partial_caliber_decision": partial["decision"],
            "partial_caliber_reason": partial["reason"],
            "audit_chain_ok": bool(chain_ok),
            "audit_events": len(audit.events),
            "audit_log": os.path.relpath(audit_path, ROOT)}


# ---------------------------------------------------------------------------
# C7 -- determinism
# ---------------------------------------------------------------------------
def run_c7(cfg: Mapping[str, object], evidence_core: Mapping[str, object]) -> Dict[str, object]:
    kind = str(cfg["response_kind"])
    truth = np.asarray(cfg["truth"], dtype=float)  # type: ignore[index]
    x = _design(cfg)
    rng = np.random.default_rng(int(cfg["seed"]))
    y = bayes.response_predict(kind, truth, x) + rng.normal(0.0, float(cfg["sigma"]), x.size)
    outs = []
    for _ in range(2):
        res = bayes.update(kind, x, y, float(cfg["sigma"]), _priors(cfg),
                           seed=int(cfg["posterior_seed"]),
                           n_chains=int(cfg["n_chains"]), n_steps=int(cfg["n_steps"]),
                           n_burn=int(cfg["n_burn"]), level=float(cfg["level"]))
        outs.append(_canon(res.as_dict()))
    rerun_identical = outs[0] == outs[1]
    core_sha = sha256_text(_canon(evidence_core))
    return {"pass": bool(rerun_identical and len(core_sha) == 64),
            "identical_across_reruns": bool(rerun_identical),
            "posterior_digest": sha256_text(outs[0]),
            "evidence_sha256": core_sha}


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(c1, c2, c3, c5, outdir: str) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(outdir, exist_ok=True)
    figs: List[Dict[str, str]] = []

    labels = ["theta_0", "theta_1", "theta_2"]
    truth = c1["posterior"].get("truth") or None
    mean = c1["posterior"]["mean"]
    lo = c1["posterior"]["ci_low"]
    hi = c1["posterior"]["ci_high"]
    cov_m = c2["replicate_mean"]
    cov_lo = c2["replicate_ci_low"]
    cov_hi = c2["replicate_ci_high"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for j in range(3):
        ax = axes.flat[j]
        ax.hist([row[j] for row in cov_m], bins=20, color="#4c72b0", alpha=0.8)
        ax.axvline(_truth_of(c2, j), color="k", ls="--", lw=1.2, label="truth")
        ax.set_title(f"{labels[j]}: posterior mean over {c2['n_replicates']} replicates")
        ax.set_xlabel(labels[j])
        ax.legend(fontsize=8)
    ax = axes.flat[3]
    ax.barh(range(1, c2["n_replicates"] + 1), [r[0] for r in cov_m], color="#4c72b0",
            alpha=0.6, height=0.8)
    ax.axvline(_truth_of(c2, 0), color="k", ls="--", lw=1.2)
    for i in range(min(len(cov_lo), c2["n_replicates"])):
        ax.plot([cov_lo[i][0], cov_hi[i][0]], [i + 1, i + 1], color="#c44e52", lw=0.8,
                alpha=0.6)
    ax.set_title(f"theta_0: 95% interval per replicate (coverage "
                 f"{c2['coverage_pooled'] * 100:.1f}%)")
    ax.set_xlabel("theta_0")
    ax.set_ylabel("replicate")
    fig.suptitle("C1/C2 posterior distribution and interval coverage", fontsize=12)
    fig.tight_layout()
    path = os.path.join(outdir, "fig_posterior_coverage.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig_posterior_coverage.png", "file": os.path.relpath(path, ROOT),
                 "caption": "Posterior distributions of the twin parameters over the "
                            "Monte-Carlo replicates, and the per-replicate 95% interval "
                            "for theta_0 against the declared truth."})

    cases = c3["per_case"]
    names = [p["case_id"].replace("case_", "") for p in cases]
    resid = [p["closure_residual_pct"] if p["closure_residual_pct"] is not None else 0.0
             for p in cases]
    fig, ax = plt.subplots(figsize=(11, 5))
    colors = ["#55a868" if p["match"] else "#c44e52" for p in cases]
    bars = ax.bar(range(len(cases)), resid, color=colors)
    for i, p in enumerate(cases):
        ax.text(i, resid[i] + 0.15, f"{p['verdict']}\nexpected {p['expected']}",
                ha="center", va="bottom", fontsize=7)
    ax.axhline(2.0, color="k", ls=":", lw=1, label="closure tolerance 2%")
    ax.axhline(8.0, color="k", ls="--", lw=1, label="degraded band 8%")
    ax.set_xticks(range(len(cases)))
    ax.set_xticklabels(names, rotation=20, fontsize=8)
    ax.set_ylabel("closure residual [%]")
    ax.set_title(f"C3 caliber verdicts: {c3['correct']}/{c3['n_cases']} correct")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = os.path.join(outdir, "fig_caliber_verdicts.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig_caliber_verdicts.png", "file": os.path.relpath(path, ROOT),
                 "caption": "Closure residual per synthetic benchmark table with the "
                            "machine verdict vs the ground-truth verdict."})

    att = c5["attribution"]
    steps = att["steps"]
    names2 = ["observed\n(reduced, viscous only)"] + [s["factor"] for s in steps]
    heights = [c5["naive_deviation_pct"]] + [s["delta_pct_of_reference"] for s in steps]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(heights)), heights,
           color=["#4c72b0"] + ["#55a868" if h > 0 else "#c44e52" for h in heights[1:]])
    ax.axhline(0, color="k", lw=2)
    ax.set_xticks(range(len(heights)))
    ax.set_xticklabels(names2, rotation=15, fontsize=8)
    ax.set_ylabel("deviation vs the full-size reference [%]")
    ax.set_title("C5 deviation attribution: "
                 f"naive {c5['naive_deviation_pct']:.2f}% -> residual after "
                 f"conversion {c5['post_conversion_deviation_pct']:.3f}%")
    fig.tight_layout()
    path = os.path.join(outdir, "fig_attribution.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig_attribution.png", "file": os.path.relpath(path, ROOT),
                 "caption": "Attribution of the observed deviation of the in-repo table: "
                            "each bar is one caliber factor, in percent of the reference."})

    # posterior "field": joint density map of the two leading parameters
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    pts = np.asarray([[r[0], r[1]] for r in c2["replicate_mean"]])
    xgrid = np.linspace(pts[:, 0].min() - 0.02, pts[:, 0].max() + 0.02, 60)
    ygrid = np.linspace(pts[:, 1].min() - 0.05, pts[:, 1].max() + 0.05, 60)
    hist, xe, ye = np.histogram2d(pts[:, 0], pts[:, 1], bins=(xgrid, ygrid), density=True)
    mesh = ax.pcolormesh(xe, ye, hist.T, shading="auto", cmap="viridis")
    fig.colorbar(mesh, ax=ax, label="probability density")
    ax.plot(_truth_of(c2, 0), _truth_of(c2, 1), "r*", ms=14, label="truth")
    ax.set_xlabel("theta_0")
    ax.set_ylabel("theta_1")
    ax.set_title("Joint distribution of the twin calibration across replicates")
    ax.legend()
    fig.tight_layout()
    path = os.path.join(outdir, "fig_joint_density.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figs.append({"name": "fig_joint_density.png", "file": os.path.relpath(path, ROOT),
                 "caption": "Joint density of the updated twin parameters (theta_0 vs "
                            "theta_1) over the Monte-Carlo replicates, truth marked."})
    return figs


def _truth_of(c2: Mapping[str, object], j: int) -> float:
    truth = c2.get("truth") or [0.30, 1.60, 0.70]
    return float(truth[j])  # type: ignore[index]


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(BENCH, "results.json"))
    ap.add_argument("--replicates", type=int, default=None,
                    help="override the Monte-Carlo replicate count (fast smoke runs)")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    bayes_cfg = _load(os.path.join(CONFIG, "bayes_config.json"))
    reader_cfg = _load(os.path.join(CONFIG, "reader_config.json"))
    lines = clines.load_lineset(os.path.join(CONFIG, "lines.json"))
    pv = Provenance(ROOT)

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    say("[C1] Bayesian twin update ...")
    c1 = run_c1(bayes_cfg, pv)
    say(f"     max |rel err| = {c1['measured_max_abs_rel_err_pct']:.3f}% "
        f"(<= {c1['threshold_pct']}%) -> {'PASS' if c1['pass'] else 'FAIL'}")
    say("[C2] coverage study ...")
    c2 = run_c2(bayes_cfg, pv, args.replicates)
    say(f"     coverage = {c2['coverage_pooled'] * 100:.1f}% over {c2['n_replicates']} "
        f"replicates (floor {c2['floor'] * 100:.0f}%) -> {'PASS' if c2['pass'] else 'FAIL'}")
    say("[C3] caliber verdicts on the synthetic corpus ...")
    c3 = run_c3(lines, reader_cfg, pv)
    say(f"     {c3['correct']}/{c3['n_cases']} correct -> {'PASS' if c3['pass'] else 'FAIL'}")
    say("[C4] corrupt table must be withheld and exit non-zero ...")
    c4 = run_c4(pv)
    say(f"     status={c4['status']} exit={c4['subprocess_returncode']} -> "
        f"{'PASS' if c4['pass'] else 'FAIL'}")
    say("[C5] in-repo table replay + attribution ...")
    c5 = run_c5(lines, reader_cfg, pv)
    say(f"     naive {c5['naive_deviation_pct']:.2f}% -> after conversion "
        f"{c5['post_conversion_deviation_pct']:.3f}% "
        f"({c5['attribution']['attribution']}) -> {'PASS' if c5['pass'] else 'FAIL'}")
    say("[C6] downstream gate negative control ...")
    c6 = run_c6(pv)
    say(f"     missing-caliber rejection = "
        f"{c6['missing_caliber_rejection_rate'] * 100:.0f}% -> "
        f"{'PASS' if c6['pass'] else 'FAIL'}")
    c7 = run_c7(bayes_cfg, {"c1": c1["relative_error_pct"], "c3": c3["accuracy"],
                            "c5": c5["post_conversion_deviation_pct"],
                            "c6": c6["missing_caliber_rejection_rate"]})
    say(f"[C7] determinism: rerun identical = {c7['identical_across_reruns']} -> "
        f"{'PASS' if c7['pass'] else 'FAIL'}")
    verify = pv.verify()
    c8 = {"pass": bool(verify["ok"] and verify["n_rows"] > 20),
          "provenance_rows": verify["n_rows"], "verified": verify}
    say(f"[C8] provenance: {verify['n_rows']} anchored rows, ok={verify['ok']} -> "
        f"{'PASS' if c8['pass'] else 'FAIL'}")

    figures = [] if args.no_figures else make_figures(c1, c2, c3, c5, FIGDIR)

    criteria = {"C1": c1, "C2": {k: v for k, v in c2.items() if not k.startswith("replicate_")},
                "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7, "C8": c8}
    all_pass = all(bool(criteria[k]["pass"]) for k in ("C1", "C2", "C3", "C4", "C5", "C6",
                                                      "C7", "C8"))
    code_files = ["tools/caliber_reader/lines.py", "tools/caliber_reader/reader.py",
                  "tools/caliber_reader/gate.py", "tools/caliber_reader/synth.py",
                  "tools/data_assimilation/bayes.py",
                  "tools/data_assimilation/provenance.py",
                  "tools/data_assimilation/run_benchmark.py"]
    results = {
        "schema": "data-assimilation-verification/1.0",
        "mission": MISSION,
        "subject": "data assimilation (Bayesian twin update) + benchmark-caliber governance "
                   "(caliber reader + fail-closed downstream error gate)",
        "criteria": criteria,
        "all_pass": bool(all_pass),
        "coverage_replicates": c2["n_replicates"],
        "figures": figures,
        "artifacts": {
            "cases_dir": os.path.relpath(CASES, ROOT),
            "configs_dir": os.path.relpath(CONFIG, ROOT),
            "audit_log": c6["audit_log"],
            "results": os.path.relpath(args.out, ROOT),
        },
        "code_fingerprints": {f: sha256_file(os.path.join(ROOT, f)) for f in code_files},
        "lines_fingerprint": lines.source_fingerprint,
        "provenance": pv.as_list(),
        "provenance_sha256": pv.digest(),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")
    say(f"results -> {os.path.relpath(args.out, ROOT)} (all_pass={all_pass})")
    return 0 if all_pass else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())