"""End-to-end: run the cross-solver coupling benchmark on a light grid and gate it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from benchmarks.verified.cross_solver_coupling.run_benchmark import (  # noqa: E402
    THRESHOLDS,
    run,
)

BENCH_DIR = ROOT / "benchmarks" / "verified" / "cross_solver_coupling"


@pytest.fixture(scope="module")
def light_results(tmp_path_factory):
    """A light run whose artifacts go to a temp dir (never over the committed ones)."""
    outdir = tmp_path_factory.mktemp("cross_solver_coupling")
    return run(ratios=(1, 4, 10), source_cells=8, artifact_dir=outdir, make_figs=False), outdir


def test_every_criterion_passes(light_results):
    results, _ = light_results
    failed = {k: v["value"] for k, v in results["criteria"].items() if not v["passed"]}
    assert not failed, failed
    assert results["n_criteria"] == 10
    assert results["passed"] is True


def test_criteria_cover_b1_to_b10(light_results):
    results, _ = light_results
    assert set(results["criteria"]) == {f"B{i}" for i in range(1, 11)}


def test_numbers_match_their_thresholds(light_results):
    results, _ = light_results
    crit = results["criteria"]
    assert crit["B1"]["value"] <= THRESHOLDS["B1_mass_rel_error"]
    assert crit["B2"]["value"] <= THRESHOLDS["B2_momentum_rel_error"]
    assert crit["B3"]["value"] <= THRESHOLDS["B3_energy_rel_error"]
    assert crit["B4"]["value"] <= THRESHOLDS["B4_max_skew_s"]
    assert crit["B5"]["value"] <= THRESHOLDS["B5_subcycle_rel_error"]
    assert crit["B6"]["value"] <= THRESHOLDS["B6_max_lyapunov"]
    assert crit["B7"]["value"] <= THRESHOLDS["B7_identity_l2"]
    assert crit["B8"]["value"] <= THRESHOLDS["B8_zero_coupling_energy"]
    assert crit["B9"]["value"] <= THRESHOLDS["B9_grid_ratio_rel_error"]


def test_negative_controls_are_actually_wrong(light_results):
    """A gate that cannot fail proves nothing: the control arms must fail it."""
    results, _ = light_results
    b1 = results["criteria"]["B1"]["details"]
    assert b1["control_nearest_mass_rel_error"] > THRESHOLDS["B1_mass_rel_error"]
    b4 = results["criteria"]["B4"]["details"]
    assert b4["unaligned_max_skew_s"] > THRESHOLDS["B4_max_skew_s"]
    b6 = results["criteria"]["B6"]["details"]
    assert b6["unstable_control_leading"] > 0.0
    assert b6["estimator_check"]["max_abs_error"] < 1e-6


def test_artifacts_contract_files_are_written(light_results):
    results, outdir = light_results
    faces = json.loads((outdir / "failure_faces.json").read_text())
    manifest = json.loads((outdir / "run_manifest.json").read_text())
    assert faces["schema"] == "failure_faces/v2"
    assert len(faces["error_modes"]) >= THRESHOLDS["B10_min_error_modes"]
    assert len(manifest["coupling_clock"]) >= THRESHOLDS["B10_min_manifest_fields"]
    for field in ("coupling_interval_s", "master_dt_s", "sync_tolerance_s", "max_skew_s"):
        assert field in manifest["coupling_clock"]
    assert results["criteria"]["B10"]["passed"] is True


def test_committed_results_file_is_consistent_with_the_gate_definitions():
    path = BENCH_DIR / "results.json"
    if not path.exists():  # pragma: no cover - results.json is committed with the node
        pytest.skip("committed results.json not present")
    committed = json.loads(path.read_text())
    assert committed["benchmark"] == "cross_solver_coupling"
    assert set(committed["criteria"]) == {f"B{i}" for i in range(1, 11)}
    assert committed["passed"] is True
    for name, crit in committed["criteria"].items():
        assert crit["threshold"] == pytest.approx(THRESHOLDS[
            {
                "B1": "B1_mass_rel_error",
                "B2": "B2_momentum_rel_error",
                "B3": "B3_energy_rel_error",
                "B4": "B4_max_skew_s",
                "B5": "B5_subcycle_rel_error",
                "B6": "B6_max_lyapunov",
                "B7": "B7_identity_l2",
                "B8": "B8_zero_coupling_energy",
                "B9": "B9_grid_ratio_rel_error",
                "B10": "B10_min_error_modes",
            }[name]
        ]) if name != "B10" else crit["threshold"] == 1.0
    for artifact in ("failure_faces.json", "run_manifest.json"):
        assert (BENCH_DIR / artifact).exists()
    figures = committed["figures"]
    assert len(figures) >= 2
    for fig in figures:
        assert (BENCH_DIR / "figures" / fig["name"]).exists()