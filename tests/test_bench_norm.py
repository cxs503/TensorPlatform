"""Tests for tools/bench_norm — cross-architecture benchmark normalisation.

Run: python3 -m pytest tests/test_bench_norm.py -q
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.bench_norm import alpha, coverage, criteria, injection, measure, mixed_effects, pipeline  # noqa: E402
from tools.bench_norm.schema import Observation, read_json, read_jsonl, validate, write_jsonl  # noqa: E402

CFG = ROOT / "tools" / "bench_norm" / "configs"


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
def _rec(arm="a", wl="w1", rep=0, t=1.0, role="calibration", host=None, isa="loongarch64"):
    return Observation(arm=arm, isa=isa, host=host or f"h-{arm}", workload=wl,
                       features={"cells": 1024.0, "steps": 100.0}, repetition=rep,
                       t_seconds=t, source="measured", meta={"role": role}).to_dict()


def test_schema_roundtrip_and_validation(tmp_path):
    recs = ([_rec("a", rep=r, t=1.0 + 0.01 * r) for r in range(3)] +
            [_rec("b", rep=r, t=2.0 + 0.01 * r) for r in range(3)])
    validate(recs)
    p = write_jsonl(recs, tmp_path / "recs.jsonl")
    back = read_jsonl(p)
    assert len(back) == 6 and back[0]["arm"] == "a"
    assert json.loads(json.dumps(back[0]))["schema"].startswith("bench_norm")


def test_schema_rejects_bad_records():
    bad = _rec()
    bad["t_seconds"] = 0.0
    with pytest.raises(ValueError):
        validate([bad, _rec("b")])
    with pytest.raises(ValueError):
        validate([_rec()])                       # single arm cannot be normalised
    with pytest.raises(ValueError):
        validate([{k: v for k, v in _rec().items() if k != "arm"}, _rec("b")])


# --------------------------------------------------------------------------
# alpha
# --------------------------------------------------------------------------
def _constant_ratio_records(ratio, n_wl=6, reps=5, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    recs = []
    for rep in range(reps):
        for i in range(n_wl):
            base = 0.1 * (1.5 ** i)
            recs.append(_rec("ref", wl=f"w{i}", rep=rep, t=base))
            recs.append(_rec("arm", wl=f"w{i}", rep=rep,
                             t=base * ratio * math.exp(float(rng.normal(scale=noise)))))
    return recs


def test_alpha_exact_recovery_without_noise():
    recs = _constant_ratio_records(2.5)
    est = alpha.estimate_alpha(recs, "arm", "ref", n_boot=200)
    assert est["alpha"] == pytest.approx(2.5, rel=1e-12)
    assert est["ci_low"] <= est["alpha"] <= est["ci_high"]


def test_alpha_ci_covers_truth_and_shrinks_with_more_workloads():
    recs = _constant_ratio_records(1.7, n_wl=4, reps=5, noise=0.05, seed=1)
    few = alpha.estimate_alpha(recs, "arm", "ref", n_boot=400)
    many = alpha.estimate_alpha(_constant_ratio_records(1.7, n_wl=24, reps=5, noise=0.05, seed=1),
                                "arm", "ref", n_boot=400)
    assert few["ci_low"] < 1.7 < few["ci_high"]
    assert many["ci_low"] < 1.7 < many["ci_high"]
    assert (many["ci_high"] - many["ci_low"]) < (few["ci_high"] - few["ci_low"])


def test_alpha_aggregates_multiple_hosts_per_arm():
    recs = _constant_ratio_records(2.0)
    extra = []
    for r in recs:
        if r["arm"] == "arm":
            q = dict(r, host="arm-host-2", t_seconds=r["t_seconds"] * 1.5)
            extra.append(q)
    est = alpha.estimate_alpha(recs + extra, "arm", "ref", n_boot=200)
    # geometric mean of 1.0x and 1.5x host factors = sqrt(1.5) = 1.2247
    assert est["alpha"] == pytest.approx(2.0 * math.sqrt(1.5), rel=1e-12)


def test_normalise_and_unknown_arm():
    recs = _constant_ratio_records(2.0)
    est = alpha.estimate_alpha(recs, "arm", "ref")
    norm = alpha.normalise(recs, {"arm": est}, "ref")
    for r in norm:
        assert r["t_normalized"] == pytest.approx(r["t_seconds"] / r["alpha"])
    with pytest.raises(ValueError):
        alpha.normalise(recs + [_rec("stranger", wl="w0")], {"arm": est}, "ref")


def test_relative_difference_sign_and_ci():
    recs = _constant_ratio_records(1.5, n_wl=6, reps=5, noise=0.02, seed=3)
    d = alpha.relative_difference(recs, "arm", "ref")
    assert d["difference_pct"] == pytest.approx(50.0, rel=0.05)
    assert d["ci_low_pct"] < d["difference_pct"] < d["ci_high_pct"]


# --------------------------------------------------------------------------
# mixed effects
# --------------------------------------------------------------------------
def _synthetic_lmm(n_groups=8, per_group=40, seed=0):
    rng = np.random.default_rng(seed)
    X = np.c_[np.ones(n_groups * per_group), rng.normal(size=(n_groups * per_group, 2))]
    beta = np.array([1.0, 0.7, -0.4])
    groups = np.repeat([f"g{i}" for i in range(n_groups)], per_group)
    u_g = rng.normal(scale=0.25, size=n_groups)
    u_g -= u_g.mean()          # intercept and mean(random effect) are confounded
    u = np.repeat(u_g, per_group)
    y = X @ beta + u + rng.normal(scale=0.1, size=X.shape[0])
    return y, X, groups


def test_lmm_recovers_fixed_effects():
    y, X, groups = _synthetic_lmm()
    fit = mixed_effects.fit_lmm(y, X, groups)
    assert np.allclose(fit["beta"], [1.0, 0.7, -0.4], atol=0.05)
    assert fit["sigma_u2"] > 0.01 and fit["sigma_e2"] > 0.0
    assert 0.0 <= fit["icc"] <= 1.0
    assert fit["r2_log"] > 0.95
    assert fit["n"] == len(y) and fit["n_groups"] == 8


def test_lmm_without_random_effect_degenerates_to_ols():
    rng = np.random.default_rng(4)
    n = 120
    X = np.c_[np.ones(n), rng.normal(size=n)]
    y = X @ np.array([0.5, 1.5]) + rng.normal(scale=0.1, size=n)
    fit = mixed_effects.fit_lmm(y, X, np.zeros(n, dtype=int))
    ols = mixed_effects.ols(y, X)
    # with a single group the random intercept is collinear with the fixed
    # intercept; the slopes must still coincide with OLS
    assert np.allclose(fit["beta"][1:], ols["beta"][1:], atol=0.05)


def test_design_matrices_shapes():
    feats = [{"cells": 1024.0, "steps": 100.0}, {"cells": 4096.0, "steps": 200.0}]
    Xl, lcols = mixed_effects.design_log(feats)
    Xr, rcols = mixed_effects.design_raw(feats)
    assert Xl.shape == (2, 3) and lcols == ["intercept", "log10_cells", "log10_steps"]
    assert Xr.shape == (2, 4) and rcols[-1] == "cells*steps"
    assert Xl[0, 1] == pytest.approx(math.log10(1024.0))


# --------------------------------------------------------------------------
# injection + coverage
# --------------------------------------------------------------------------
def test_injection_delta_applies_only_to_evaluation_workloads():
    sc = read_json(CFG / "injection_scenarios.json")["scenarios"][0]
    recs = injection.generate(sc, seed=11)
    roles = {r["meta"]["role"] for r in recs}
    assert roles == {"calibration", "evaluation"}
    for r in recs:
        expect = sc["delta_true"].get(r["arm"], 0.0) if r["meta"]["role"] == "evaluation" else 0.0
        assert r["meta"]["delta_true_log"] == pytest.approx(expect)
    assert all(r["source"] == "injected" for r in recs)


def test_injection_alpha_is_recoverable_in_the_mean():
    sc = read_json(CFG / "injection_scenarios.json")["scenarios"][0]
    recs = injection.generate(sc, seed=12)
    est = alpha.estimate_alpha([r for r in recs if r["meta"]["role"] == "calibration"],
                               sc["criterion_arm"], sc["ref"], n_boot=100)
    assert est["alpha"] == pytest.approx(2.2, rel=0.03)


def test_coverage_study_thresholds_are_present():
    sc = json.loads(json.dumps(read_json(CFG / "injection_scenarios.json")["scenarios"][0]))
    sc["reps"] = 4
    st = coverage.study(sc, sc["ref"], n_replicates=4, base_seed=5, n_boot=120)
    assert 0.0 <= st["coverage"]["rate"] <= 1.0
    assert st["alpha_rel_err"]["p95_abs"] >= 0.0
    assert set(st["target_thresholds"]) == {
        "alpha_err_p95<0.03", "r2_mean>0.9", "coverage>0.9",
        "raw_diff>50pct", "norm_diff_rate_below_5pct>=0.95"}
    assert st["diff_raw_pct"]["min_abs"] > 50.0      # negative control holds


# --------------------------------------------------------------------------
# criteria
# --------------------------------------------------------------------------
def test_criteria_injection_five_items():
    sc = json.loads(json.dumps(read_json(CFG / "injection_scenarios.json")["scenarios"][0]))
    sc["reps"] = 4
    st = coverage.study(sc, sc["ref"], n_replicates=3, base_seed=9, n_boot=100)
    items = criteria.from_injection(st, sc["name"])
    assert [i["id"] for i in items] == ["a", "b", "c", "d", "e"]
    assert all(i["verdict"] in ("PASS", "FAIL") for i in items)
    assert all(i["source"].startswith("artifacts/") for i in items)


def test_criteria_measurement_withholds_x86_claims():
    real = {"arms": {"loongarch64@X:h1": {"alpha": 1.0}, "loongarch64@X:h2": {"alpha": 1.2}},
            "lmm": {"r2_log": 0.95}}
    items = {i["id"]: i for i in criteria.from_measurement(real)}
    assert items["c"]["verdict"] == "WITHHELD"
    assert items["e"]["verdict"] == "WITHHELD"
    assert "no x86_64 host" in items["c"]["note"]


# --------------------------------------------------------------------------
# measurement layer / physics
# --------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required to build the timing kernel")
def test_kernel_physics_check(tmp_path):
    cfg = read_json(CFG / "workloads.json")
    built = measure.build_kernel(tmp_path)
    res = pipeline.physics_check(built["binary"], cfg)
    assert res["l2_pass"], res
    assert res["u_max_rel_err"] < 0.01
    assert res["mass_pass"], res


def test_noise_summary(tmp_path):
    recs = ([_rec("a", wl=f"w{i}", rep=r, t=1.0 + 0.02 * r) for r in range(5) for i in range(3)] +
            [_rec("b", wl=f"w{i}", rep=r, t=2.0 + 0.02 * r) for r in range(5) for i in range(3)])
    noise = measure.summarise_noise(recs)
    assert noise["n_groups"] == 6
    assert 0.0 < noise["median_cv"] < 0.1


def test_real_analysis_end_to_end_on_synthetic_records():
    rng = np.random.default_rng(21)
    recs = []
    base = {"c1": (1024.0, 100.0), "c2": (4096.0, 200.0), "c3": (2048.0, 400.0),
            "e1": (2048.0, 300.0)}
    for arm, scale in (("ref", 1.0), ("arm", 2.0)):
        for wl, role in (("c1", "calibration"), ("c2", "calibration"),
                         ("c3", "calibration"), ("e1", "evaluation")):
            cells, steps = base[wl]
            for rep in range(4):
                cells_rep = cells * (1.0 + 0.05 * rep)
                t = scale * (cells_rep * steps) ** 0.5 * math.exp(float(rng.normal(scale=0.02)))
                r = _rec(arm, wl=wl, rep=rep, t=t, role=role)
                r["features"] = {"cells": cells_rep, "steps": steps}
                recs.append(r)
    real = pipeline.real_analysis(recs, "ref")
    assert real["n_calibration_workloads"] == 3 and real["n_evaluation_workloads"] == 1
    assert real["arms"]["arm"]["alpha"] == pytest.approx(2.0, rel=0.05)
    assert real["diffs"]["arm"]["raw"]["difference_pct"] == pytest.approx(100.0, rel=0.1)
    assert abs(real["diffs"]["arm"]["normalised"]["difference_pct"]) < 10.0
    assert real["lmm"]["r2_log"] > 0.9


def test_zero_domain_semantics_scan():
    """Platform rule: no ship-domain vocabulary may be hard-coded in this tool."""
    terms = {"ship", "hull", "resistance", "propulsion", "seakeeping", "manoeuvring",
             "rudder", "froude", "wigley", "kcs", "suboff", "ice", "cavitation", "draft"}
    offenders = {}
    for path in (ROOT / "tools" / "bench_norm").rglob("*"):
        if path.suffix not in {".py", ".c"}:
            continue
        words = set(path.read_text(encoding="utf-8", errors="replace").lower()
                    .replace("-", " ").replace("_", " ").split())
        hit = terms & words
        if hit:
            offenders[str(path.relative_to(ROOT))] = sorted(hit)
    assert not offenders, offenders

# --------------------------------------------------------------------------
# workload-resolved alpha(x)
# --------------------------------------------------------------------------
def _feature_dependent_records(gamma=(0.3, 0.2, 0.0), seed=0):
    """alpha depends on log10(cells): scalar alpha cannot normalise it."""
    rng = np.random.default_rng(seed)
    grid = [(2048, 200, "calibration"), (2048, 400, "calibration"),
            (8192, 200, "calibration"), (8192, 400, "calibration"),
            (32768, 200, "evaluation"), (32768, 400, "evaluation")]
    g = np.asarray(gamma, dtype=float)
    recs = []
    for cells, steps, role in grid:
        base = cells * steps * 1e-7
        x = np.array([1.0, math.log10(cells), math.log10(steps)])
        for rep in range(6):
            base_ref = _rec("ref", wl=f"c{cells}x{steps}", rep=rep, t=base, role=role)
            base_ref["features"] = {"cells": float(cells), "steps": float(steps)}
            recs.append(base_ref)
            t = base * math.exp(float(x @ g) + float(rng.normal(scale=0.01)))
            r = _rec("arm", wl=f"c{cells}x{steps}", rep=rep, t=t, role=role)
            r["features"] = {"cells": float(cells), "steps": float(steps)}
            recs.append(r)
    return recs


def test_alpha_model_recovers_gamma_and_beats_scalar_on_held_out():
    recs = _feature_dependent_records()
    cal = [r for r in recs if r["meta"]["role"] == "calibration"]
    ev = [r for r in recs if r["meta"]["role"] == "evaluation"]
    model = alpha.estimate_alpha_model(cal, "arm", "ref")
    # gamma is identified with 1 residual degree of freedom; check the
    # dominant (cells) slope and the predictions rather than exact coefficients
    assert model["gamma"][1] == pytest.approx(0.2, abs=0.08)
    assert alpha.alpha_at({"cells": 8192.0, "steps": 200.0}, model) == pytest.approx(
        math.exp(0.3 + 0.2 * math.log10(8192.0)), rel=0.05)
    norm = alpha.normalise_with_model(ev, {"arm": model}, "ref")
    resid = alpha.relative_difference(norm, "arm", "ref", field="t_normalized")
    scalar = alpha.estimate_alpha(cal, "arm", "ref", n_boot=100)
    norm_s = alpha.normalise(ev, {"arm": scalar}, "ref")
    resid_s = alpha.relative_difference(norm_s, "arm", "ref", field="t_normalized")
    assert abs(resid["difference_pct"]) < 2.0
    assert abs(resid_s["difference_pct"]) > 5.0          # constant alpha is inadequate
    assert 0.0 <= model["residual_sd_log"] < 0.05


def test_alpha_model_requires_enough_workloads():
    recs = [r for r in _feature_dependent_records() if r["workload"] in {"c2048x200", "c8192x200"}
            and r["meta"]["role"] == "calibration"]
    with pytest.raises(ValueError):
        alpha.estimate_alpha_model(recs, "arm", "ref")


def test_normalise_with_model_rejects_unknown_arm():
    recs = _feature_dependent_records()
    cal = [r for r in recs if r["meta"]["role"] == "calibration"]
    model = alpha.estimate_alpha_model(cal, "arm", "ref")
    with pytest.raises(ValueError):
        alpha.normalise_with_model(recs + [_rec("stranger", wl="c2048x200")], {"arm": model}, "ref")
