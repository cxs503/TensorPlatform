"""C1/C2/C7/C8 — Bayesian twin update: accuracy, coverage, determinism, provenance."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from tools.data_assimilation import bayes
from tools.data_assimilation.provenance import (Provenance, ProvenanceError,
                                                json_pointer_get, sha256_file)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "benchmarks/verified/data_assimilation")
RESULTS = os.path.join(BENCH, "results.json")


@pytest.fixture(scope="module")
def cfg():
    with open(os.path.join(BENCH, "config/bayes_config.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def results():
    if not os.path.isfile(RESULTS):
        pytest.skip("run tools.data_assimilation.run_benchmark first")
    with open(RESULTS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _priors(cfg):
    return [bayes.Prior.from_mapping(p) for p in cfg["priors"]]


def _design(cfg):
    spec = cfg["x"]
    return np.linspace(spec["start"], spec["stop"], spec["count"])


def test_prior_support_is_respected():
    normal = bayes.Prior("normal", (0.0, 1.0))
    uniform = bayes.Prior("uniform", (0.0, 2.0))
    lognormal = bayes.Prior("lognormal", (0.0, 1.0))
    assert normal.in_support(1e9) and normal.logpdf(0.0) > -10
    assert not uniform.in_support(2.5) and uniform.logpdf(2.5) == float("-inf")
    assert not lognormal.in_support(-1.0) and lognormal.logpdf(1.0) > -10


def test_update_recovers_truth_within_ten_percent(cfg):
    """C1 live check: posterior mean within 10% of the declared truth."""
    kind, truth = cfg["response_kind"], np.asarray(cfg["truth"], dtype=float)
    x = _design(cfg)
    rng = np.random.default_rng(cfg["seed"])
    y = bayes.response_predict(kind, truth, x) + rng.normal(0.0, cfg["sigma"], x.size)
    res = bayes.update(kind, x, y, cfg["sigma"], _priors(cfg), seed=cfg["posterior_seed"],
                       n_chains=cfg["n_chains"], n_steps=cfg["n_steps"],
                       n_burn=cfg["n_burn"], level=cfg["level"])
    rel = res.relative_error_pct(truth)
    assert max(abs(v) for v in rel) <= cfg["c1_tolerance_pct"], rel
    assert np.all(res.ci_low < res.mean) and np.all(res.ci_high > res.mean)


def test_posterior_diagnostics_are_sane(cfg):
    """Chains must agree (R-hat) and be usable (ESS) — otherwise the CI is a lie."""
    kind, truth = cfg["response_kind"], np.asarray(cfg["truth"], dtype=float)
    x = _design(cfg)
    rng = np.random.default_rng(cfg["seed"])
    y = bayes.response_predict(kind, truth, x) + rng.normal(0.0, cfg["sigma"], x.size)
    res = bayes.update(kind, x, y, cfg["sigma"], _priors(cfg), seed=cfg["posterior_seed"],
                       n_chains=cfg["n_chains"], n_steps=cfg["n_steps"],
                       n_burn=cfg["n_burn"], level=cfg["level"])
    assert float(np.max(res.rhat)) < 1.10
    assert float(np.min(res.ess)) > 50.0
    assert all(0.10 < a < 0.80 for a in res.acceptance)


def test_sampler_is_deterministic_and_seed_sensitive(cfg):
    """C7 live check: identical seed -> identical samples; different seed -> different."""
    kind, truth = cfg["response_kind"], np.asarray(cfg["truth"], dtype=float)
    x = _design(cfg)
    rng = np.random.default_rng(cfg["seed"])
    y = bayes.response_predict(kind, truth, x) + rng.normal(0.0, cfg["sigma"], x.size)
    kw = dict(n_chains=2, n_steps=200, n_burn=100, level=cfg["level"])
    a = bayes.update(kind, x, y, cfg["sigma"], _priors(cfg), seed=3, **kw)
    b = bayes.update(kind, x, y, cfg["sigma"], _priors(cfg), seed=3, **kw)
    c = bayes.update(kind, x, y, cfg["sigma"], _priors(cfg), seed=4, **kw)
    assert np.array_equal(a.samples, b.samples)
    assert not np.array_equal(a.samples, c.samples)


def test_small_coverage_study_reports_all_fields(cfg):
    """Mechanics of the coverage study (the 100-replicate number is recorded in results)."""
    cov = bayes.coverage_study(cfg["response_kind"], _design(cfg),
                               np.asarray(cfg["truth"], dtype=float), cfg["sigma"],
                               _priors(cfg), n_replicates=3, seed=1,
                               n_chains=2, n_steps=150, n_burn=50)
    assert 0.0 <= cov["coverage_pooled"] <= 1.0
    assert len(cov["replicate_mean"]) == 3
    assert len(cov["coverage_per_parameter"]) == len(cfg["truth"])


def test_c1_and_c2_recorded_numbers_meet_thresholds(results):
    c1, c2 = results["criteria"]["C1"], results["criteria"]["C2"]
    assert c1["pass"] and c1["measured_max_abs_rel_err_pct"] <= 10.0
    assert c2["pass"] and c2["coverage_pooled"] >= 0.93
    assert c2["n_replicates"] == 100


def test_provenance_anchors_verify(results):
    """C8: every anchored number re-reads identically from its (sha256, pointer)."""
    pv = Provenance(ROOT)
    for row in results["provenance"]:
        pv.rows.append(row)
    report = pv.verify()
    assert report["ok"], report["problems"]
    assert report["n_rows"] >= 40
    assert sum(1 for r in results["provenance"] if r["kind"] == "file") >= 10


def test_provenance_detects_drift(tmp_path):
    """A tampered source must break the anchor (the check is not decorative)."""
    src = tmp_path / "table.json"
    src.write_text(json.dumps({"rows": [{"value": 1.0}]}), encoding="utf-8")
    pv = Provenance(str(tmp_path))
    pv.file_value("v", "table.json", "/rows/0/value")
    assert pv.verify()["ok"]
    src.write_text(json.dumps({"rows": [{"value": 2.0}]}), encoding="utf-8")
    report = pv.verify()
    assert not report["ok"] and report["problems"]


def test_provenance_rejects_duplicate_names(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    pv = Provenance(str(tmp_path))
    pv.file_value("same", "t.json", "/a")
    pv.file_value("same", "t.json", "/a")
    with pytest.raises(ProvenanceError):
        pv.verify()


def test_json_pointer_lookup():
    doc = {"a": [{"b": 3}]}
    assert json_pointer_get(doc, "/a/0/b") == 3
    assert json_pointer_get(doc, "/a") == [{"b": 3}]
    with pytest.raises(KeyError):
        json_pointer_get(doc, "/a/0/c")


def test_results_file_is_timestamp_free(results):
    """Determinism (C7) needs a report whose bytes do not depend on the wall clock."""
    text = json.dumps(results)
    for token in ('"timestamp', '"generated_at', '"created_at', '"run_at'):
        assert token not in text
    assert results["all_pass"] is True


def test_full_pipeline_is_byte_reproducible(tmp_path):
    """C7 end-to-end: two independent runs of the whole pipeline give identical bytes."""
    out = tmp_path / "results.json"
    cmd = [sys.executable, "-m", "tools.data_assimilation.run_benchmark",
           "--replicates", "3", "--no-figures", "--quiet", "--out", str(out)]
    first = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr[-2000:]
    payload = out.read_bytes()
    second = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr[-2000:]
    assert out.read_bytes() == payload
    report = json.loads(payload)
    assert report["all_pass"] is True


def test_c7_evidence_digest_is_recomputable(results):
    """C7: the recorded evidence digest must be reproducible from the report itself."""
    import hashlib
    c = results["criteria"]
    core = {"c1": c["C1"]["relative_error_pct"], "c3": c["C3"]["accuracy"],
            "c5": c["C5"]["post_conversion_deviation_pct"],
            "c6": c["C6"]["missing_caliber_rejection_rate"]}
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == c["C7"]["evidence_sha256"]
    assert c["C7"]["identical_across_reruns"] is True