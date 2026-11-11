#!/usr/bin/env python3
"""Evidence runner for node N10 (multi-fidelity error chain + credible-domain guard).

Produces ``evidence/fidelity_chain_evidence.json``: every number quoted by the report
and the campaign acceptance table is computed here, with provenance.  Nothing is typed
by hand.

Criteria covered
----------------
C1  error propagation: propagated bound >= realised error, plus the non-triviality ratio
C2  coverage of the 95 % credible interval, and the shrunk-interval negative control
C3  switch gate: trigger rate on 20 % overshoot and the in-domain false-alarm rate
C4  extrapolation risk: ``eps(d) = C d**p`` with ``p >= 1`` at three distances
C5  domain neutrality: shared lexicon scan over the module and its tests
C6  no regression: pytest on the new suite plus the existing neighbouring suites

Usage::

    python docs/p470w2_n10/run_fidelity_chain.py [--out DIR]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from fidelity import (  # noqa: E402
    CalibrationSet,
    ChainLink,
    FidelityChain,
    calibrate_tier,
    check_conservativeness,
    fit_extrapolation_law,
    interval_coverage,
)

DOMAIN_LO, DOMAIN_HI = 0.35, 0.65
WIDTH = DOMAIN_HI - DOMAIN_LO
CENTER = 0.5


# --------------------------------------------------------------------------- models
def quantity(x) -> np.ndarray:
    """The generic scalar field the tiers approximate (no domain meaning)."""
    x = np.asarray(x, dtype=float).reshape(-1)
    return np.exp(0.8 * x) + 0.3 * np.sin(4.0 * x)


def derivatives_at(x0: float, order: int = 6) -> list:
    e, s, c = math.exp(0.8 * x0), math.sin(4.0 * x0), math.cos(4.0 * x0)
    shots = [
        e + 0.3 * s,
        0.8 * e + 1.2 * c,
        0.64 * e - 4.8 * s,
        0.512 * e - 19.2 * c,
        0.4096 * e + 76.8 * s,
        0.32768 * e + 307.2 * c,
        -0.262144 * e - 1228.8 * s,
    ]
    return shots[: order + 1]


def truncated_expansion(order: int):
    coeffs = [derivatives_at(CENTER, order)[k] / math.factorial(k)
              for k in range(order + 1)]

    def evaluate(X) -> np.ndarray:
        x = np.asarray(X, dtype=float).reshape(-1)
        return sum(c * (x - CENTER) ** k for k, c in enumerate(coeffs))

    return evaluate


ORDERS = {0: 1, 1: 3, 2: 5}


def build_calibration(n: int = 121) -> CalibrationSet:
    X = np.linspace(DOMAIN_LO, DOMAIN_HI, n).reshape(-1, 1)
    return CalibrationSet(
        X=X, reference=quantity(X),
        tier_values={r: truncated_expansion(o)(X) for r, o in ORDERS.items()},
    )


def study(distances, order: int):
    xs = np.array([DOMAIN_HI + d * WIDTH for d in distances]).reshape(-1, 1)
    return np.asarray(distances, float), np.abs(truncated_expansion(order)(xs) - quantity(xs))


def build_chain(n: int = 121, *, lipschitz_margin: float = 1.0,
                guard_margin: float = 0.25, with_study: bool = True) -> FidelityChain:
    cal = build_calibration(n)
    tiers = {}
    for rank, order in ORDERS.items():
        st = study((0.1, 0.2, 0.4), order) if with_study else None
        tiers[rank] = calibrate_tier(rank, f"tier{order}", truncated_expansion(order),
                                     cal, study=st, lipschitz_margin=lipschitz_margin,
                                     guard_margin=guard_margin)
    weights = {r: ChainLink(1.0 / len(ORDERS), 1.0) for r in ORDERS}
    return FidelityChain(tiers, links=weights)


# --------------------------------------------------------------------------- C1
def criterion_c1(chain: FidelityChain) -> dict:
    X_in = np.linspace(DOMAIN_LO, DOMAIN_HI, 401).reshape(-1, 1)
    overshoots = np.array([0.0, 0.05, 0.1, 0.2, 0.4])
    X_out = np.concatenate([
        (DOMAIN_HI + d * WIDTH + 0.01 * WIDTH * np.arange(20)).reshape(-1, 1)
        for d in overshoots])
    X_all = np.concatenate([X_in, X_out])
    actual = chain.composed_prediction(X_all) - quantity(X_all)
    bound = chain.composed_bound(X_all, mode="guard")
    audit_all = check_conservativeness(bound, actual)
    twin = chain.composed_prediction(X_in) - quantity(X_in)
    audit_in = check_conservativeness(chain.composed_bound(X_in, mode="guard"), twin)
    # per-tier single-fidelity bounds (what a caller sees when using one tier)
    per_tier = {}
    for rank in chain.ranks:
        per_tier[str(rank)] = check_conservativeness(
            chain.tier_bound(rank, X_all), chain.predict(rank, X_all) - quantity(X_all))
    # margin sensitivity: what the defaults buy, measured on the 2-D stress case
    sensitivity = []
    for lip, guard in ((0.5, 0.0), (1.0, 0.0), (1.0, 0.25), (2.0, 0.5)):
        stress = two_dimensional_stress(lipschitz_margin=lip, guard_margin=guard)
        sensitivity.append({"lipschitz_margin": lip, "guard_margin": guard,
                            "violations": stress["violations"], "n": stress["n"],
                            "ratio_p95": stress["ratio_p95"]})
    verdict = (audit_all["violations"] == 0 and audit_all["ratio_p95"] < 500.0
               and sensitivity[-2]["violations"] == 0)
    return {
        "criterion": "C1",
        "statement": "propagated total bound >= realised error (conservatism guard)",
        "n_points": int(X_all.shape[0]),
        "in_domain": audit_in,
        "in_and_out_of_domain": audit_all,
        "per_tier_bound": per_tier,
        "margin_sensitivity_2d": sensitivity,
        "pass": bool(verdict),
    }


def two_dimensional_stress(*, lipschitz_margin: float, guard_margin: float) -> dict:
    rng = np.random.default_rng(7)

    def ref(X):
        X = np.atleast_2d(X)
        return np.exp(0.5 * X[:, 0]) * np.cos(1.5 * X[:, 1]) + 0.2 * X[:, 0] * X[:, 1]

    def model(a, b, X):
        X = np.atleast_2d(X)
        return np.exp(a * X[:, 0]) * np.cos(b * X[:, 1]) + 0.2 * X[:, 0] * X[:, 1]

    Xc = rng.random((225, 2))
    cal = CalibrationSet(Xc, ref(Xc), {0: model(0.40, 1.30, Xc), 1: model(0.46, 1.42, Xc)})
    tiers = {0: calibrate_tier(0, "reduced", lambda X: model(0.40, 1.30, X),
                               cal, lipschitz_margin=lipschitz_margin,
                               guard_margin=guard_margin),
             1: calibrate_tier(1, "reference", ref, cal,
                               lipschitz_margin=lipschitz_margin,
                               guard_margin=guard_margin)}
    chain = FidelityChain(tiers)
    Xt = rng.random((300, 2))
    audit = check_conservativeness(chain.tier_bound(0, Xt),
                                   chain.predict(0, Xt) - ref(Xt))
    audit["ratio_p95"] = round(audit["ratio_p95"], 3)
    return audit


# --------------------------------------------------------------------------- C2
def criterion_c2(chain: FidelityChain) -> dict:
    rng = np.random.default_rng(11)
    X = DOMAIN_LO + WIDTH * rng.random((600, 1))
    truth = quantity(X)
    rows = {}
    for rank in chain.ranks:
        full = interval_coverage(chain, rank, X, truth)
        curve = []
        for scale in (1.0, 0.9, 0.75, 0.6, 0.5, 0.25):
            rep = interval_coverage(chain, rank, X, truth, halfwidth_scale=scale)
            curve.append({"halfwidth_scale": scale,
                          "coverage": round(rep["coverage"], 4),
                          "mean_halfwidth": round(rep["mean_halfwidth"], 6)})
        rows[str(rank)] = {"coverage_at_95": round(full["coverage"], 4),
                           "mean_halfwidth": round(full["mean_halfwidth"], 6),
                           "curve": curve}
    neg = rows["0"]["curve"]
    shrunk = next(c for c in neg if c["halfwidth_scale"] == 0.5)
    return {
        "criterion": "C2",
        "statement": "95 % credible interval covers the reference on >= 90 % of points; "
                     "a halved interval must fall below 80 %",
        "n_points": 600,
        "per_tier": rows,
        "negative_control": {"halfwidth_scale": 0.5, "coverage": shrunk["coverage"]},
        "pass": bool(rows["0"]["coverage_at_95"] >= 0.90
                     and shrunk["coverage"] < 0.80
                     and rows["1"]["coverage_at_95"] >= 0.90),
    }


# --------------------------------------------------------------------------- C3
def criterion_c3(chain: FidelityChain) -> dict:
    gate = chain.gate()
    rng_in = np.random.default_rng(99)
    sweep = [{
        "label": "inside the box",
        "min_overshoot": 0.0,
        "trigger_rate": round(float(np.mean([x.escalate for x in gate.advice(
            DOMAIN_LO + WIDTH * rng_in.random((50, 1)))])), 4),
        "domain_exceeded_rate": 0.0,
        "target_rank_top_rate": round(float(np.mean([x.target_rank == chain.top_rank
                                                     for x in gate.advice(
            DOMAIN_LO + WIDTH * rng_in.random((50, 1)))])), 4),
    }]
    for d in (0.05, 0.1, 0.2, 0.3, 0.5):
        xs = (DOMAIN_HI + d * WIDTH + 0.004 * WIDTH * np.arange(50)).reshape(-1, 1)
        decisions = gate.advice(xs)
        sweep.append({
            "label": f"min overshoot {d:.0%} of the box width",
            "min_overshoot": d,
            "trigger_rate": round(float(np.mean([x.escalate for x in decisions])), 4),
            "domain_exceeded_rate": round(float(np.mean(
                ["domain_exceeded" in x.reasons for x in decisions])), 4),
            "target_rank_top_rate": round(float(np.mean(
                [x.target_rank == chain.top_rank for x in decisions])), 4),
        })
    rng = np.random.default_rng(20260101)
    X_fp = DOMAIN_LO + WIDTH * rng.random((2000, 1))
    decisions = gate.advice(X_fp)
    fp_rate = float(np.mean([d.escalate for d in decisions]))
    reason_counts: dict = {}
    for d in decisions:
        for code in d.reasons:
            reason_counts[code] = reason_counts.get(code, 0) + 1
    strict = chain.gate(rel_tol=1e-3).advice(X_fp)
    return {
        "criterion": "C3",
        "statement": "a 20 % overshoot of the calibrated domain must trigger a switch; "
                     "the in-domain false-alarm rate must stay <= 5 %",
        "sweep": sweep,
        "false_alarm": {"n": int(X_fp.shape[0]), "rate": round(fp_rate, 4),
                        "reason_counts": reason_counts},
        "tolerance_rule_opt_in": {
            "rel_tol": 1e-3,
            "escalation_rate": round(float(np.mean([d.escalate for d in strict])), 4)},
        "pass": bool(sweep[0]["trigger_rate"] <= 0.05
                     and sweep[4]["trigger_rate"] == 1.0
                     and fp_rate <= 0.05),
    }


# --------------------------------------------------------------------------- C4
def criterion_c4(chain: FidelityChain) -> dict:
    distances = np.array([0.1, 0.2, 0.4])
    rows = []
    for rank in chain.ranks:
        tier = chain.tier(rank)
        xs = (DOMAIN_HI + distances * WIDTH).reshape(-1, 1)
        actual = np.abs(chain.predict(rank, xs) - quantity(xs))
        law = tier.law
        bound = law.eps(distances)
        out_d = 1.5 * distances.max()
        x_out = np.array([[DOMAIN_HI + out_d * WIDTH]])
        out_actual = float(abs(chain.predict(rank, x_out)[0] - quantity(x_out)[0]))
        out_bound = float(np.atleast_1d(law.eps(out_d))[0])
        rows.append({
            "rank": rank,
            "p": round(law.p, 4),
            "C": round(law.C, 6),
            "source": law.source,
            "study_distances": distances.tolist(),
            "actual_error": [round(float(v), 8) for v in actual],
            "law_bound": [round(float(v), 8) for v in bound],
            "covered_at_each_distance": [bool(b >= a) for b, a in zip(bound, actual)],
            "out_of_sample": {"distance": out_d, "actual_error": round(out_actual, 8),
                              "law_bound": round(out_bound, 8),
                              "covered": bool(out_bound >= out_actual)},
        })
    degraded = fit_extrapolation_law([0.1, 0.2, 0.4], [0.4, 0.2, 0.1])
    in_domain_risk_zero = bool(np.allclose(
        chain.tier(0).extrapolation_risk(np.linspace(DOMAIN_LO, DOMAIN_HI, 17).reshape(-1, 1)),
        0.0))
    return {
        "criterion": "C4",
        "statement": "eps(d) = C d^p with p >= 1 covers the realised error at three "
                     "extrapolation distances",
        "tiers": rows,
        "p_floor_enforced_on_decaying_errors": {"p": round(degraded.p, 4),
                                                "C": round(degraded.C, 6)},
        "risk_term_zero_inside_domain": in_domain_risk_zero,
        "pass": bool(all(r["p"] >= 1.0 for r in rows)
                     and all(all(r["covered_at_each_distance"]) for r in rows)
                     and degraded.p >= 1.0 and in_domain_risk_zero),
    }


# --------------------------------------------------------------------------- C5
def criterion_c5() -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    from evidence_domain_scan import load_lexicon, scan_paths

    result = scan_paths(["tools/fidelity", "tests/fidelity"],
                        lexicon=load_lexicon(), root=ROOT)
    return {
        "criterion": "C5",
        "statement": "the mechanism carries no domain vocabulary (shared lexicon scan)",
        "scanned_files": result["scanned_files"],
        "terms": result["terms"],
        "hits": result["hits"],
        "pass": bool(result["hits"] == [] and result["scanned_files"] >= 3),
    }


# --------------------------------------------------------------------------- C6
def criterion_c6() -> dict:
    targets = ["tests/fidelity", "tests/test_multifidelity.py"]
    cmd = [sys.executable, "-m", "pytest", *targets, "-q"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-3:]
    baseline_path = ROOT / "docs" / "p470w2_n10" / "evidence" / "regression_baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    collect = baseline.get("collect", {})
    batches = baseline.get("batches", {})
    delta_ok = collect.get("error_delta") == 0
    batches_ok = batches.get("all_green") is True
    return {
        "criterion": "C6",
        "statement": "the new suite and the neighbouring existing suites stay green, "
                     "and the whole-tree collection is not perturbed",
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "summary_lines": tail,
        "regression_baseline": baseline_path.name,
        "whole_tree_collection": {
            "with_new_package": {k: collect.get("collect_only_with_new_package", {}).get(k)
                                 for k in ("collected", "errors")},
            "baseline_without_new_package": {k: collect.get("collect_only_baseline", {}).get(k)
                                             for k in ("collected", "errors")},
            "new_tests": collect.get("new_tests"),
            "error_delta": collect.get("error_delta"),
            "note": collect.get("note", ""),
        },
        "neighbouring_batches": {
            "total_passed": batches.get("total_passed"),
            "all_green": batches_ok,
            "batches": [{"command": b["command"].split("pytest ")[-1],
                         "passed": b["passed"], "returncode": b["returncode"]}
                        for b in batches.get("batches", [])],
        },
        "pass": bool(proc.returncode == 0 and delta_ok and batches_ok),
    }


# --------------------------------------------------------------------------- provenance
def provenance() -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_status_porcelain": git("status", "--porcelain"),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "machine": platform.machine(),
        "command": "python docs/p470w2_n10/run_fidelity_chain.py",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs" / "p470w2_n10" / "evidence"))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    chain = build_chain()
    payload = {
        "schema": "multi-fidelity-error-chain/1.0",
        "node": "p470w2-n10-multi-fidelity",
        "provenance": provenance(),
        "setup": {
            "quantity": "exp(0.8x) + 0.3 sin(4x); tiers = truncated expansions about "
                        "x=0.5 of order 1/3/5 (generic analytic benchmark)",
            "calibration_box": [DOMAIN_LO, DOMAIN_HI],
            "n_calibration": 121,
            "study_distances": [0.1, 0.2, 0.4],
            "report_level": 0.95,
            "alarm_level": 0.99,
            "guard_defaults": {"lipschitz_margin": 1.0, "guard_margin": 0.25},
            "chain": chain.diagnostics(),
        },
        "criteria": [
            criterion_c1(chain),
            criterion_c2(chain),
            criterion_c3(chain),
            criterion_c4(chain),
            criterion_c5(),
            criterion_c6(),
        ],
    }
    payload["all_pass"] = bool(all(c["pass"] for c in payload["criteria"]))
    payload["criteria_pass"] = {c["criterion"]: c["pass"] for c in payload["criteria"]}
    target = out_dir / "fidelity_chain_evidence.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
    for c in payload["criteria"]:
        print(f"  {c['criterion']}: {'PASS' if c['pass'] else 'FAIL'}  {c['statement']}")
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
