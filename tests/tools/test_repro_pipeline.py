"""Tests for P71-N10: Historical Verification Claim Re-run & Drift Accounting.

Covers:
  - Wilson interval computation
  - Stratified sampling
  - Drift classification
  - Full pipeline execution
  - Acceptance criteria validation
  - Negative control (known-bug nodes)
  - Figure generation
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.repro.repro_pipeline import (
    wilson_interval,
    stratified_sample,
    classify_drift,
    run_acceptance_test,
    load_done_nodes,
    run_pipeline,
)
from tools.repro.seed_campaign_db import create_tables, seed_data
from tools.repro.generate_figures import (
    load_summary,
    plot_repro_rate_by_repo,
    plot_drift_types_pie,
    plot_ci_forest,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with seeded campaign data."""
    db_path = str(tmp_path / "test_campaign.db")
    conn = sqlite3.connect(db_path)
    create_tables(conn)
    info = seed_data(conn, n_done=475, seed=42)
    conn.close()
    return db_path, info


@pytest.fixture
def small_db(tmp_path):
    """Small database for fast tests."""
    db_path = str(tmp_path / "small_campaign.db")
    conn = sqlite3.connect(db_path)
    create_tables(conn)
    info = seed_data(conn, n_done=60, seed=99)
    conn.close()
    return db_path, info


# ──────────────────────────────────────────────────────────────────────
# Wilson interval tests
# ──────────────────────────────────────────────────────────────────────

class TestWilsonInterval:
    def test_perfect_pass(self):
        """All pass → proportion=1.0, CI should be tight."""
        ci = wilson_interval(100, 100)
        assert ci["proportion"] == 1.0
        assert ci["lower"] < 1.0  # Wilson never gives exactly 1.0
        assert ci["upper"] == 1.0
        assert ci["width"] < 0.1  # Should be narrow for n=100

    def test_all_fail(self):
        """All fail → proportion=0.0."""
        ci = wilson_interval(0, 100)
        assert ci["proportion"] == 0.0
        assert ci["lower"] == 0.0
        assert ci["upper"] > 0.0

    def test_half_pass(self):
        """50% pass rate."""
        ci = wilson_interval(50, 100)
        assert ci["proportion"] == 0.5
        assert ci["lower"] < 0.5 < ci["upper"]
        assert abs(ci["width"] - 0.2) < 0.05  # ~20% width for n=100

    def test_zero_total(self):
        """Zero total → safe defaults."""
        ci = wilson_interval(0, 0)
        assert ci["proportion"] == 0.0
        assert ci["width"] == 0.0

    def test_six_digit_precision(self):
        """Results must have 6 significant digits."""
        ci = wilson_interval(92, 100)
        # Check rounding to 6 decimal places
        assert ci["proportion"] == round(0.92, 6)
        assert isinstance(ci["lower"], float)
        assert isinstance(ci["upper"], float)

    def test_ci_width_decreases_with_n(self):
        """Larger sample → narrower CI."""
        ci_small = wilson_interval(92, 100)
        ci_large = wilson_interval(920, 1000)
        assert ci_large["width"] < ci_small["width"]

    def test_95_ci_coverage(self):
        """Wilson interval should contain true proportion ~95% of the time."""
        import random
        random.seed(123)
        true_p = 0.85
        n = 200
        contains = 0
        trials = 1000
        for _ in range(trials):
            passes = sum(1 for _ in range(n) if random.random() < true_p)
            ci = wilson_interval(passes, n)
            if ci["lower"] <= true_p <= ci["upper"]:
                contains += 1
        coverage = contains / trials
        assert coverage > 0.90, f"Coverage {coverage:.3f} < 0.90"


# ──────────────────────────────────────────────────────────────────────
# Stratified sampling tests
# ──────────────────────────────────────────────────────────────────────

class TestStratifiedSample:
    def test_min_per_layer(self, tmp_db):
        """Each stratum should have at least min_per_layer samples."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)
        sampled = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)

        # Check each layer has >= 5
        from collections import defaultdict
        layers = defaultdict(int)
        for node in sampled:
            meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
            key = (meta.get("repo"), meta.get("physics_domain"))
            layers[key] += 1

        for key, count in layers.items():
            assert count >= 5, f"Layer {key} has {count} < 5 samples"

    def test_total_min(self, tmp_db):
        """Total samples should be >= total_min."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)
        sampled = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)
        assert len(sampled) >= 50

    def test_repos_covered(self, tmp_db):
        """Should cover >= 5 repos."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)
        sampled = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)

        repos = set()
        for node in sampled:
            meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
            repos.add(meta.get("repo"))
        assert len(repos) >= 5

    def test_deterministic(self, tmp_db):
        """Same seed → same sample."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)
        s1 = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)
        s2 = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)
        assert [n["id"] for n in s1] == [n["id"] for n in s2]

    def test_no_duplicates(self, tmp_db):
        """No node should appear twice in sample."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)
        sampled = stratified_sample(nodes, min_per_layer=5, total_min=50, seed=42)
        ids = [n["id"] for n in sampled]
        assert len(ids) == len(set(ids))


# ──────────────────────────────────────────────────────────────────────
# Drift classification tests
# ──────────────────────────────────────────────────────────────────────

class TestDriftClassification:
    def test_known_bug(self):
        """Known bug nodes should be classified as code_change."""
        result = {"status": "fail", "detail": "Known bug: numerical instability"}
        node = {"metadata": {"known_bug": True}}
        assert classify_drift(result, node) == "code_change"

    def test_dependency_change(self):
        """Numerical drift → dependency_change."""
        result = {"status": "fail", "detail": "Numerical drift: reference value changed"}
        node = {"metadata": {}}
        assert classify_drift(result, node) == "dependency_change"

    def test_environment_change(self):
        """Environment mismatch → environment_change."""
        result = {"status": "fail", "detail": "Environment mismatch: different solver version"}
        node = {"metadata": {}}
        assert classify_drift(result, node) == "environment_change"

    def test_original_verdict_error(self):
        """Original criteria incorrect → original_verdict_error."""
        result = {"status": "fail", "detail": "Original acceptance criteria was incorrect"}
        node = {"metadata": {}}
        assert classify_drift(result, node) == "original_verdict_error"

    def test_pre_classified(self):
        """Pre-classified drift_type should be used."""
        result = {"status": "fail", "drift_type": "environment_change"}
        node = {"metadata": {}}
        assert classify_drift(result, node) == "environment_change"


# ──────────────────────────────────────────────────────────────────────
# Acceptance test runner tests
# ──────────────────────────────────────────────────────────────────────

class TestAcceptanceTestRunner:
    def test_known_bug_fails(self, tmp_db):
        """Known-bug nodes must fail (negative control)."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)

        # Find known-bug nodes
        for node in nodes:
            meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
            if meta.get("known_bug"):
                result = run_acceptance_test(node, db_path)
                assert result["status"] == "fail", f"Known-bug node {node['id']} should fail"

    def test_non_bug_mostly_passes(self, tmp_db):
        """Non-bug nodes should mostly pass."""
        db_path, info = tmp_db
        nodes = load_done_nodes(db_path)

        pass_count = 0
        total = 0
        for node in nodes:
            meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
            if not meta.get("known_bug"):
                result = run_acceptance_test(node, db_path)
                total += 1
                if result["status"] == "pass":
                    pass_count += 1

        # Should be > 80% pass rate for non-bug nodes
        assert pass_count / total > 0.80, f"Pass rate {pass_count}/{total} < 80%"


# ──────────────────────────────────────────────────────────────────────
# Full pipeline tests
# ──────────────────────────────────────────────────────────────────────

class TestFullPipeline:
    def test_pipeline_runs(self, tmp_db, tmp_path):
        """Pipeline should complete without errors."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        assert summary is not None
        assert "repro_rate" in summary
        assert "counts" in summary

    def test_acceptance_criteria_sample_size(self, tmp_db, tmp_path):
        """Sample size must be >= 50."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        assert summary["acceptance_criteria"]["sample_size_ge_50"] is True
        assert summary["sample"]["sample_size"] >= 50

    def test_acceptance_criteria_repos(self, tmp_db, tmp_path):
        """Must cover >= 5 repos."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        assert summary["acceptance_criteria"]["repos_ge_5"] is True
        assert len(summary["sample"]["repos_covered"]) >= 5

    def test_acceptance_criteria_ci_width(self, tmp_db, tmp_path):
        """CI width must be <= 20%."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        assert summary["acceptance_criteria"]["ci_width_le_20pct"] is True
        assert summary["repro_rate"]["width"] <= 0.20

    def test_negative_control(self, tmp_db, tmp_path):
        """Known-bug nodes must be detected as failures."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        nc = summary["negative_control"]
        assert nc["passed"] is True, f"Negative control failed: {nc}"
        assert nc["known_bug_nodes_failed"] > 0
        assert nc["known_bug_nodes_passed"] == 0

    def test_output_files_created(self, tmp_db, tmp_path):
        """All required output files must be created."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        run_pipeline(db_path, sample_size=60, min_per_layer=5,
                     seed=42, output_dir=output_dir)

        output_path = Path(output_dir)
        assert (output_path / "repro_results.json").exists()
        assert (output_path / "drift_report.json").exists()
        assert (output_path / "summary.json").exists()

    def test_drift_report_structure(self, tmp_db, tmp_path):
        """Drift report must have correct structure."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        run_pipeline(db_path, sample_size=60, min_per_layer=5,
                     seed=42, output_dir=output_dir)

        with open(Path(output_dir) / "drift_report.json") as f:
            drift = json.load(f)

        assert "generated_at" in drift
        assert "total_failures" in drift
        assert "drift_classification" in drift
        assert "entries" in drift

        for entry in drift["entries"]:
            assert "node_id" in entry
            assert "drift_type" in entry
            assert entry["drift_type"] in ["code_change", "dependency_change",
                                            "environment_change", "original_verdict_error"]

    def test_six_digit_precision(self, tmp_db, tmp_path):
        """All numeric values must have 6 significant digits."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        # Check repro_rate precision
        rr = summary["repro_rate"]
        assert rr["proportion"] == round(rr["proportion"], 6)
        assert rr["lower"] == round(rr["lower"], 6)
        assert rr["upper"] == round(rr["upper"], 6)
        assert rr["width"] == round(rr["width"], 6)

    def test_all_acceptance_criteria_pass(self, tmp_db, tmp_path):
        """All acceptance criteria must pass."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        summary = run_pipeline(db_path, sample_size=60, min_per_layer=5,
                               seed=42, output_dir=output_dir)

        ac = summary["acceptance_criteria"]
        for key, value in ac.items():
            assert value is True, f"Acceptance criterion '{key}' failed: {value}"


# ──────────────────────────────────────────────────────────────────────
# Figure generation tests
# ──────────────────────────────────────────────────────────────────────

class TestFigureGeneration:
    def test_figures_generated(self, tmp_db, tmp_path):
        """All three figures must be generated."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        # Run pipeline first
        run_pipeline(db_path, sample_size=60, min_per_layer=5,
                     seed=42, output_dir=output_dir)

        # Generate figures
        summary_path = str(Path(output_dir) / "summary.json")
        summary = load_summary(summary_path)
        figures_dir = Path(output_dir) / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)

        plot_repro_rate_by_repo(summary, figures_dir / "repro_rate_by_repo.png")
        plot_drift_types_pie(summary, figures_dir / "drift_types_pie.png")
        plot_ci_forest(summary, figures_dir / "ci_forest_plot.png")

        assert (figures_dir / "repro_rate_by_repo.png").exists()
        assert (figures_dir / "drift_types_pie.png").exists()
        assert (figures_dir / "ci_forest_plot.png").exists()

    def test_figure_files_nonempty(self, tmp_db, tmp_path):
        """Figure files must be non-empty."""
        db_path, info = tmp_db
        output_dir = str(tmp_path / "output")

        run_pipeline(db_path, sample_size=60, min_per_layer=5,
                     seed=42, output_dir=output_dir)

        summary_path = str(Path(output_dir) / "summary.json")
        summary = load_summary(summary_path)
        figures_dir = Path(output_dir) / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)

        plot_repro_rate_by_repo(summary, figures_dir / "repro_rate_by_repo.png")
        plot_drift_types_pie(summary, figures_dir / "drift_types_pie.png")
        plot_ci_forest(summary, figures_dir / "ci_forest_plot.png")

        for fig in ["repro_rate_by_repo.png", "drift_types_pie.png", "ci_forest_plot.png"]:
            size = (figures_dir / fig).stat().st_size
            assert size > 1000, f"Figure {fig} is too small ({size} bytes)"


# ──────────────────────────────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_database(self, tmp_path):
        """Empty database should raise ValueError."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        create_tables(conn)
        conn.close()

        with pytest.raises(ValueError, match="No done nodes found"):
            run_pipeline(db_path, sample_size=60, output_dir=str(tmp_path / "output"))

    def test_small_sample(self, tmp_path):
        """Very small database should still work."""
        db_path = str(tmp_path / "tiny.db")
        conn = sqlite3.connect(db_path)
        create_tables(conn)
        seed_data(conn, n_done=10, seed=42)
        conn.close()

        # Should work but may not meet all acceptance criteria
        summary = run_pipeline(db_path, sample_size=10, min_per_layer=2,
                               seed=42, output_dir=str(tmp_path / "output"))
        assert summary["sample"]["sample_size"] <= 10
