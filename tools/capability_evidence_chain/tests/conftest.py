"""Synthetic fixtures: two fake solver repositories and a config generator.

The fixture names are deliberately synthetic (``synth_solver_a`` …): the
package must never depend on a physical domain, and neither must its tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(root: Path, rel: str, payload) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture()
def synth_root(tmp_path: Path) -> Path:
    """Two synthetic solver repos with mixed artefact quality."""
    root = tmp_path / "ecosystem"
    a = root / "synth_solver_a"
    b = root / "synth_solver_b"
    # solver a: three valid artefacts (two carrying metrics), one invalid JSON
    _write(a, "artifacts/case_01.json", {
        "case": "alpha", "rows": [
            {"rel_error": 0.010, "mass_drift": 2.0e-12},
            {"rel_error": 0.020, "mass_drift": 4.0e-12},
            {"rel_error": 0.015, "mass_drift": 3.0e-12},
        ],
        "gate": {"status": "PASS", "value": 0.01, "threshold": 0.05, "n": 12, "sd": 0.02},
    })
    _write(a, "artifacts/case_02.json", {
        "case": "beta", "rows": [
            {"rel_error": 0.0125, "mass_drift": 2.5e-12},
            {"rel_error": 0.0300, "mass_drift": 6.0e-12},
        ],
        "gate": {"status": "PASS", "value": 0.2, "threshold": 0.05},
    })
    _write(a, "artifacts/no_metrics.json", {"case": "gamma", "iterations": 42, "elapsed": 1.5})
    _write(a, "artifacts/broken.json", "{ this is not json")
    # solver b: one artefact with a different metric family
    _write(b, "benchmarks/bench_01.json", {
        "rows": [
            {"rel_error": 0.020, "z_metric": 1.0},
            {"rel_error": 0.024, "z_metric": 2.0},
            {"rel_error": 0.018, "z_metric": 3.0},
        ],
        "gate": {"status": "FAIL", "value": 0.30, "threshold": 0.10},
    })
    # an acceptance document for the reclassification census
    _write(root, "docs/acceptance_synth.json", {
        "node": "SYNTH-1",
        "gates": {
            "adequate": {"status": "PASS", "value": 0.01, "threshold": 0.05,
                         "n": 40, "sd": 0.01},
            "underpowered": {"status": "PASS", "value": 0.04, "threshold": 0.05,
                             "n": 3, "sd": 0.05},
            "threshold_only": {"status": "PASS", "value": 0.02, "threshold": 0.05},
            "inconsistent": {"status": "PASS", "value": 0.20, "threshold": 0.05},
            "unverifiable": {"status": "PASS", "threshold": 0.05},
            "failed": {"status": "FAIL", "value": 0.30, "threshold": 0.05},
        },
        "scalar_pass": {"verdict": "OK", "observed": 0.01, "tolerance": "<= 0.05"},
    })
    return root


@pytest.fixture()
def synth_config(tmp_path: Path, synth_root: Path) -> Path:
    """Config exercising all three statistical scenarios on the synthetic repos."""
    cfg = {
        "schema_version": "1.0",
        "root_env": "SYNTH_ROOT",
        "repos": [
            {"name": "synth_solver_a", "path": "synth_solver_a",
             "include": ["artifacts/**/*.json"], "max_files": 100},
            {"name": "synth_solver_b", "path": "synth_solver_b",
             "include": ["benchmarks/**/*.json"], "max_files": 100},
        ],
        "capabilities": [
            {"id": "synth.alpha.relative_error", "scenario": "mean",
             "margin": 0.05, "unit": "relative",
             "groups": {"a": {"repo": "synth_solver_a", "include": ["artifacts/**/*.json"],
                              "metric": "^rel_error$", "value_range": [0.0, 1.0],
                              "max_samples": 100}}},
            {"id": "synth.alpha.mass_drift", "scenario": "mean",
             "margin": 1e-11, "unit": "dimensionless",
             "groups": {"a": {"repo": "synth_solver_a", "include": ["artifacts/**/*.json"],
                              "metric": "^mass_drift$", "value_range": [0.0, 1.0],
                              "max_samples": 100}}},
            {"id": "synth.ab.error_spread", "scenario": "variance",
             "theta": 2.0, "margin": 2.0, "unit": "variance ratio",
             "groups": {
                 "a": {"repo": "synth_solver_a", "include": ["artifacts/**/*.json"],
                       "metric": "^rel_error$", "max_samples": 100},
                 "b": {"repo": "synth_solver_b", "include": ["benchmarks/**/*.json"],
                       "metric": "^rel_error$", "max_samples": 100}}},
            {"id": "synth.ab.error_distribution", "scenario": "distribution",
             "margin": 0.9, "unit": "sup-CDF distance",
             "groups": {
                 "a": {"repo": "synth_solver_a", "include": ["artifacts/**/*.json"],
                       "metric": "^rel_error$", "max_samples": 100},
                 "b": {"repo": "synth_solver_b", "include": ["benchmarks/**/*.json"],
                       "metric": "^rel_error$", "max_samples": 100}}},
        ],
        "reclassification": {
            "sources": ["docs/**/*.json"],
            "margin_default": 0.05,
            "min_computable_n": 3,
        },
    }
    p = tmp_path / "synth_config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p