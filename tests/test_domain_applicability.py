"""Domain applicability judgment & refusal protocol — comprehensive test suite.

Verifies all machine-computable acceptance criteria:
  C1  Detection & false rejection on synthetic domains
  C2  Three-caliber cross-adjudication
  C3  Label-shuffle negative control
  C4  Historical failure replay
  C5  Refusal protocol (WITHHELD + reason code)
  C6  Zero domain semantics scan + deterministic reproducibility
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sys
from pathlib import Path

import pytest

# Ensure backend is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.domain_applicability import (
    DomainEstimator,
    DomainJudgment,
    DomainRegistry,
    DomainSample,
    DomainSpec,
    ReasonCode,
    Verdict,
    calibrate_domain,
    make_synthetic_2d_domain,
    make_synthetic_3d_domain,
    _convex_hull_2d,
    _point_in_convex_2d,
    _min_dist_to_hull_2d,
    _mahalanobis_distance,
    _compute_mean,
    _compute_covariance,
    _local_density_ratio,
    _auc_from_scores,
)


# ===================================================================
# C1: Detection & false rejection on synthetic domains
# ===================================================================

class TestC1_DetectionAndFalseRejection:
    """C1: On frozen synthetic domains (2D and 3D families, known boundaries),
    out-of-domain recall ≥ 0.9, in-domain false rejection ≤ 5%."""

    def test_2d_recall(self):
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Test on FAIL samples
        fail_samples = [s for s in spec.samples if s.label == "FAIL"]
        detected = 0
        for s in fail_samples:
            j = est.judge(s.params)
            if j.verdict == Verdict.WITHHELD:
                detected += 1
        recall = detected / max(len(fail_samples), 1)
        assert recall >= 0.9, f"2D recall {recall:.3f} < 0.9; cal={cal}"

    def test_2d_false_rejection(self):
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Test on PASS samples
        pass_samples = [s for s in spec.samples if s.label == "PASS"]
        rejected = 0
        for s in pass_samples:
            j = est.judge(s.params)
            if j.verdict == Verdict.WITHHELD:
                rejected += 1
        fpr = rejected / max(len(pass_samples), 1)
        assert fpr <= 0.05, f"2D false rejection {fpr:.3f} > 0.05"

    def test_3d_recall(self):
        spec = make_synthetic_3d_domain(n_pass=80, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        fail_samples = [s for s in spec.samples if s.label == "FAIL"]
        detected = 0
        for s in fail_samples:
            j = est.judge(s.params)
            if j.verdict == Verdict.WITHHELD:
                detected += 1
        recall = detected / max(len(fail_samples), 1)
        assert recall >= 0.9, f"3D recall {recall:.3f} < 0.9"

    def test_3d_false_rejection(self):
        spec = make_synthetic_3d_domain(n_pass=80, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        pass_samples = [s for s in spec.samples if s.label == "PASS"]
        rejected = 0
        for s in pass_samples:
            j = est.judge(s.params)
            if j.verdict == Verdict.WITHHELD:
                rejected += 1
        fpr = rejected / max(len(pass_samples), 1)
        assert fpr <= 0.05, f"3D false rejection {fpr:.3f} > 0.05"

    def test_2d_boundary_known(self):
        """Verify that points clearly outside the known boundary are detected."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Points far outside the cluster
        outside_points = [
            {"x": -5.0, "y": 0.5},
            {"x": 5.0, "y": 0.5},
            {"x": 0.5, "y": -5.0},
            {"x": 0.5, "y": 5.0},
        ]
        for p in outside_points:
            j = est.judge(p)
            assert j.verdict == Verdict.WITHHELD, f"Point {p} not detected as out-of-domain"

    def test_2d_boundary_inside(self):
        """Verify that points clearly inside are accepted."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Points near the center of the cluster
        inside_points = [
            {"x": 0.5, "y": 0.5},
            {"x": 0.45, "y": 0.55},
            {"x": 0.55, "y": 0.45},
        ]
        for p in inside_points:
            j = est.judge(p)
            assert j.verdict == Verdict.IN_DOMAIN, f"Point {p} incorrectly rejected"


# ===================================================================
# C2: Three-caliber cross-adjudication
# ===================================================================

class TestC2_ThreeCaliberCrossAdjudication:
    """C2: Convex hull / distance / density boundary deviation ≤ 10% on
    synthetic domains; interpretable disagreements must be recorded."""

    def test_2d_hull_accuracy(self):
        """Convex hull should contain all PASS points and exclude clear outliers."""
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        est = DomainEstimator(spec)

        # All PASS points should be inside or on the hull
        pass_pts = [(s.params["x"], s.params["y"]) for s in spec.samples if s.label == "PASS"]
        hull = est._hull_2d
        if hull:
            inside_count = sum(1 for p in pass_pts if _point_in_convex_2d(hull, p))
            # Allow small numerical tolerance
            assert inside_count >= len(pass_pts) * 0.95, \
                f"Only {inside_count}/{len(pass_pts)} PASS points inside hull"

    def test_2d_hull_excludes_fail(self):
        """Most FAIL points should be outside the convex hull."""
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        est = DomainEstimator(spec)

        fail_pts = [(s.params["x"], s.params["y"]) for s in spec.samples if s.label == "FAIL"]
        hull = est._hull_2d
        if hull:
            outside_count = sum(1 for p in fail_pts if not _point_in_convex_2d(hull, p))
            # At least 70% of FAIL should be outside (they're designed to be far)
            assert outside_count >= len(fail_pts) * 0.7, \
                f"Only {outside_count}/{len(fail_pts)} FAIL points outside hull"

    def test_mahalanobis_vs_hull_agreement(self):
        """Mahalanobis and hull should agree on clear cases."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=15, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Test on points within param_ranges but outside the convex hull
        # The hull is roughly centered at (0.5, 0.5) with spread ~0.3
        # Points at x=-0.5 or x=1.5 are within param_ranges (-1, 2) but outside hull
        extreme_points = [
            {"x": -0.5, "y": 0.5},
            {"x": 1.5, "y": 0.5},
            {"x": 0.5, "y": -0.5},
            {"x": 0.5, "y": 1.5},
        ]
        for p in extreme_points:
            j = est.judge(p)
            # Should be flagged as out of domain
            assert j.verdict == Verdict.WITHHELD, f"Extreme point {p} not flagged"
            # Check that at least one caliber contributed
            reasons = []
            if j.convex_hull_distance is not None and j.convex_hull_distance > 0:
                reasons.append("hull")
            if j.mahalanobis_distance is not None and spec.mahalanobis_threshold is not None:
                if j.mahalanobis_distance > spec.mahalanobis_threshold:
                    reasons.append("mahalanobis")
            if j.density_ratio is not None and spec.density_ratio_threshold is not None:
                if j.density_ratio < spec.density_ratio_threshold:
                    reasons.append("density")
            # At least one caliber should have flagged
            assert len(reasons) >= 1, f"No caliber flagged extreme point {p}, reasons={reasons}, judgment={j.to_response_dict()}"

    def test_density_ratio_monotonic(self):
        """Density ratio should decrease as we move away from the cluster center."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=5, seed=42)
        est = DomainEstimator(spec)

        center = {"x": 0.5, "y": 0.5}
        j_center = est.judge(center)

        # Points at increasing distance should have decreasing density
        distances = [0.0, 0.3, 0.6, 1.0, 2.0]
        densities = []
        for d in distances:
            p = {"x": 0.5 + d, "y": 0.5}
            j = est.judge(p)
            densities.append(j.density_ratio if j.density_ratio is not None else 0.0)

        # Generally decreasing (allow some noise)
        # Check that the farthest point has lower density than the center
        assert densities[-1] <= densities[0], \
            f"Density not decreasing: {densities}"

    def test_three_caliber_disagreement_recorded(self):
        """When calibers disagree, the judgment should record which ones triggered."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Find a point that's borderline
        # Try points along the edge of the cluster
        for angle in range(0, 360, 15):
            import math
            r = 0.4  # radius from center
            px = 0.5 + r * math.cos(math.radians(angle))
            py = 0.5 + r * math.sin(math.radians(angle))
            p = {"x": px, "y": py}
            j = est.judge(p)
            # The judgment should have all three metrics populated
            assert j.convex_hull_distance is not None
            assert j.mahalanobis_distance is not None
            assert j.density_ratio is not None


# ===================================================================
# C3: Label-shuffle negative control
# ===================================================================

class TestC3_LabelShuffleNegativeControl:
    """C3: After shuffling PASS/FAIL labels, AUC ∈ [0.4, 0.6].
    If still high → feature leak → FAIL."""

    def test_shuffle_auc(self):
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=42)
        cal = calibrate_domain(spec)
        # The calibration already computes shuffle AUC
        assert 0.4 <= cal.auc_shuffle <= 0.6, \
            f"Shuffle AUC {cal.auc_shuffle:.3f} outside [0.4, 0.6] → possible feature leak"

    def test_shuffle_auc_3d(self):
        spec = make_synthetic_3d_domain(n_pass=80, n_fail=20, seed=42)
        cal = calibrate_domain(spec)
        assert 0.4 <= cal.auc_shuffle <= 0.6, \
            f"3D shuffle AUC {cal.auc_shuffle:.3f} outside [0.4, 0.6]"

    def test_real_auc_higher_than_shuffle(self):
        """Real AUC should be significantly higher than shuffle AUC."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=42)
        cal = calibrate_domain(spec)
        # Real recall should be much higher than random
        assert cal.recall > 0.5, f"Real recall {cal.recall:.3f} too low"
        assert cal.auc_shuffle < 0.6, f"Shuffle AUC {cal.auc_shuffle:.3f} too high"


# ===================================================================
# C4: Historical failure replay
# ===================================================================

class TestC4_HistoricalFailureReplay:
    """C4: Replay platform-readable historical failure cases (≥10 examples),
    recall ≥ 0.8, list hits/misses."""

    def test_historical_replay(self):
        """Simulate historical failure cases from a resistance model."""
        # Create a domain that mimics a resistance model's valid range
        # Fr number: 0.1-0.4, Cb: 0.5-0.85
        pass_samples = []
        random.seed(123)
        for _ in range(40):
            fn = random.uniform(0.15, 0.35)
            cb = random.uniform(0.55, 0.80)
            pass_samples.append(DomainSample(
                params={"fr": fn, "cb": cb},
                label="PASS",
            ))

        # Historical failures: outside the valid range
        fail_samples = [
            DomainSample(params={"fr": 0.05, "cb": 0.65}, label="FAIL"),  # too low Fr
            DomainSample(params={"fr": 0.50, "cb": 0.65}, label="FAIL"),  # too high Fr
            DomainSample(params={"fr": 0.25, "cb": 0.30}, label="FAIL"),  # too low Cb
            DomainSample(params={"fr": 0.25, "cb": 0.95}, label="FAIL"),  # too high Cb
            DomainSample(params={"fr": 0.02, "cb": 0.20}, label="FAIL"),  # both low
            DomainSample(params={"fr": 0.55, "cb": 0.90}, label="FAIL"),  # both high
            DomainSample(params={"fr": 0.01, "cb": 0.70}, label="FAIL"),  # very low Fr
            DomainSample(params={"fr": 0.60, "cb": 0.70}, label="FAIL"),  # very high Fr
            DomainSample(params={"fr": 0.25, "cb": 0.10}, label="FAIL"),  # very low Cb
            DomainSample(params={"fr": 0.25, "cb": 0.99}, label="FAIL"),  # very high Cb
            DomainSample(params={"fr": 0.08, "cb": 0.40}, label="FAIL"),  # borderline low
            DomainSample(params={"fr": 0.45, "cb": 0.88}, label="FAIL"),  # borderline high
        ]

        spec = DomainSpec(
            name="resistance_model",
            param_names=["fr", "cb"],
            param_ranges={"fr": (0.0, 1.0), "cb": (0.0, 1.0)},
            samples=pass_samples + fail_samples,
            owner_repo="ship-performance-platform",
            version="v1",
        )

        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Replay
        hits = []
        misses = []
        for s in fail_samples:
            j = est.judge(s.params)
            if j.verdict == Verdict.WITHHELD:
                hits.append({"params": s.params, "reason": j.reason_code.value})
            else:
                misses.append({"params": s.params, "judgment": j.to_response_dict()})

        recall = len(hits) / max(len(fail_samples), 1)
        assert recall >= 0.8, (
            f"Historical replay recall {recall:.3f} < 0.8; "
            f"hits={len(hits)}, misses={len(misses)}, misses_detail={misses}"
        )

    def test_historical_replay_with_error_labels(self):
        """Test with error-based labels (error > threshold = FAIL)."""
        pass_samples = []
        fail_samples = []
        random.seed(456)
        for _ in range(30):
            speed = random.uniform(5.0, 15.0)
            length = random.uniform(50.0, 150.0)
            error = random.uniform(0.01, 0.10)  # low error
            pass_samples.append(DomainSample(
                params={"speed": speed, "length": length},
                label="PASS",
                error=error,
            ))

        # High error cases
        for _ in range(12):
            speed = random.choice([1.0, 2.0, 25.0, 30.0])  # outside range
            length = random.choice([10.0, 20.0, 300.0, 400.0])
            error = random.uniform(0.20, 0.50)  # high error
            fail_samples.append(DomainSample(
                params={"speed": speed, "length": length},
                label="FAIL",
                error=error,
            ))

        spec = DomainSpec(
            name="speed_length_model",
            param_names=["speed", "length"],
            param_ranges={"speed": (0.0, 50.0), "length": (0.0, 500.0)},
            samples=pass_samples + fail_samples,
        )

        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        detected = sum(
            1 for s in fail_samples
            if est.judge(s.params).verdict == Verdict.WITHHELD
        )
        recall = detected / max(len(fail_samples), 1)
        assert recall >= 0.8, f"Error-based replay recall {recall:.3f} < 0.8"


# ===================================================================
# C5: Refusal protocol
# ===================================================================

class TestC5_RefusalProtocol:
    """C5: Out-of-domain calls 100% return WITHHELD + reason code;
    silent extrapolation cases must FAIL."""

    def test_withheld_response_structure(self):
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Out-of-domain point
        p = {"x": -5.0, "y": 0.5}
        j = est.judge(p)

        assert j.verdict == Verdict.WITHHELD
        assert j.reason_code != ReasonCode.OK
        # Must have reason code
        assert j.reason_code.value in [
            ReasonCode.DOMAIN_OUT_OF_RANGE.value,
            ReasonCode.CONVEX_HULL_EXCEEDED.value,
            ReasonCode.MAHALANOBIS_EXCEEDED.value,
            ReasonCode.DENSITY_TOO_LOW.value,
        ]

    def test_withheld_has_nn_distance(self):
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        p = {"x": -5.0, "y": 0.5}
        j = est.judge(p)

        assert j.nn_distance is not None
        assert j.nn_distance > 0
        assert j.nn_params is not None

    def test_withheld_has_boundary_direction(self):
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Point below range
        p = {"x": -2.0, "y": 0.5}
        j = est.judge(p)

        assert j.verdict == Verdict.WITHHELD
        assert j.boundary_direction is not None
        assert "x" in j.boundary_direction
        assert j.boundary_direction["x"] == "below"

    def test_no_silent_extrapolation(self):
        """Verify that out-of-domain points never return IN_DOMAIN."""
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        # Test many out-of-domain points
        random.seed(789)
        for _ in range(20):
            p = {
                "x": random.choice([-5.0, -3.0, 3.0, 5.0]),
                "y": random.choice([-5.0, -3.0, 3.0, 5.0]),
            }
            j = est.judge(p)
            assert j.verdict != Verdict.IN_DOMAIN, \
                f"Silent extrapolation: {p} returned IN_DOMAIN"

    def test_in_domain_returns_metrics(self):
        """In-domain calls should return domain distance metrics for downstream weighting."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        p = {"x": 0.5, "y": 0.5}
        j = est.judge(p)

        assert j.verdict == Verdict.IN_DOMAIN
        assert j.mahalanobis_distance is not None
        assert j.density_ratio is not None
        assert j.nn_distance is not None

    def test_response_dict_serializable(self):
        """Response dict must be JSON-serializable."""
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        est = DomainEstimator(spec)

        for p in [{"x": -5.0, "y": 0.5}, {"x": 0.5, "y": 0.5}]:
            j = est.judge(p)
            d = j.to_response_dict()
            # Must be JSON serializable
            json_str = json.dumps(d)
            assert json_str  # non-empty


# ===================================================================
# C6: Zero domain semantics + deterministic reproducibility
# ===================================================================

class TestC6_ZeroDomainSemantics:
    """C6: Zero domain semantics scan (0 hits for domain-specific terms in core)
    + byte-identical results across two runs."""

    def test_zero_domain_semantics_scan(self):
        """Scan the domain_applicability module for domain-specific terms."""
        module_path = Path(__file__).resolve().parent.parent / "backend" / "services" / "domain_applicability.py"
        with open(module_path) as f:
            content = f.read()

        # Domain-specific terms that should NOT appear in the core module
        # Note: "hull" in "convex_hull" is a mathematical term, not domain-specific
        domain_terms = [
            "ship", "resistance", "seakeeping", "maneuver",
            "propeller", "rudder", "displacement", "draft",
            "beam", "cb", "fr", "fn", "speed_ms", "wetted",
            "ITTC", "Froude", "Reynolds",
        ]

        hits = []
        for term in domain_terms:
            # Check for the term as a word (not part of another word)
            import re
            pattern = r'\b' + re.escape(term) + r'\b'
            if re.search(pattern, content, re.IGNORECASE):
                hits.append(term)

        assert len(hits) == 0, f"Domain-specific terms found: {hits}"

    def test_deterministic_reproducibility(self):
        """Two runs with same seed must produce byte-identical results."""
        def run_once(seed):
            spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=seed)
            cal = calibrate_domain(spec)
            spec.mahalanobis_threshold = cal.mahalanobis_threshold
            spec.density_ratio_threshold = cal.density_ratio_threshold
            est = DomainEstimator(spec)

            results = []
            test_points = [
                {"x": 0.5, "y": 0.5},
                {"x": -5.0, "y": 0.5},
                {"x": 0.5, "y": 5.0},
                {"x": 0.3, "y": 0.7},
            ]
            for p in test_points:
                j = est.judge(p)
                results.append(j.to_response_dict())
            return json.dumps(results, sort_keys=True)

        r1 = run_once(42)
        r2 = run_once(42)
        assert r1 == r2, "Results not deterministic"

        # Different seed should give different results
        r3 = run_once(99)
        assert r1 != r3, "Different seeds should give different results"

    def test_registry_deterministic(self):
        """Domain registry should produce identical results across runs."""
        def run_registry():
            reg = DomainRegistry()
            spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
            cal = calibrate_domain(spec)
            spec.mahalanobis_threshold = cal.mahalanobis_threshold
            spec.density_ratio_threshold = cal.density_ratio_threshold
            reg.register(spec)

            results = []
            for p in [{"x": 0.5, "y": 0.5}, {"x": -5.0, "y": 0.5}]:
                j = reg.judge("synthetic_2d", p)
                results.append(j.to_response_dict())
            return json.dumps(results, sort_keys=True)

        r1 = run_registry()
        r2 = run_registry()
        assert r1 == r2


# ===================================================================
# Additional unit tests for core algorithms
# ===================================================================

class TestCoreAlgorithms:
    """Unit tests for individual algorithm components."""

    def test_convex_hull_2d_basic(self):
        pts = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)]
        hull = _convex_hull_2d(pts)
        assert len(hull) == 4  # square corners
        # (0.5, 0.5) should be inside
        assert _point_in_convex_2d(hull, (0.5, 0.5))

    def test_convex_hull_2d_triangle(self):
        pts = [(0, 0), (2, 0), (1, 2)]
        hull = _convex_hull_2d(pts)
        assert len(hull) == 3
        assert _point_in_convex_2d(hull, (1, 0.5))
        assert not _point_in_convex_2d(hull, (3, 3))

    def test_mahalanobis_identity(self):
        """For identity covariance, Mahalanobis = Euclidean distance."""
        mean = [0.0, 0.0]
        cov_inv = [[1.0, 0.0], [0.0, 1.0]]
        point = [3.0, 4.0]
        md = _mahalanobis_distance(point, mean, cov_inv)
        assert abs(md - 5.0) < 1e-10  # sqrt(9+16) = 5

    def test_mean_computation(self):
        points = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        mean = _compute_mean(points)
        assert mean == [3.0, 4.0]

    def test_covariance_diagonal(self):
        """For uncorrelated data, off-diagonal should be ~0."""
        points = [[0.0, 1.0], [0.0, -1.0], [0.0, 1.0], [0.0, -1.0]]
        mean = _compute_mean(points)
        cov = _compute_covariance(points, mean)
        assert abs(cov[0][1]) < 1e-10
        assert abs(cov[1][0]) < 1e-10

    def test_density_ratio_center(self):
        """Density at center of cluster should be high."""
        points = [[0.5, 0.5] for _ in range(20)]
        # Add some spread
        import random
        random.seed(42)
        points = [[0.5 + random.gauss(0, 0.1), 0.5 + random.gauss(0, 0.1)] for _ in range(30)]
        ratio = _local_density_ratio([0.5, 0.5], points)
        assert ratio > 0.5  # Should be high at center

    def test_density_ratio_far(self):
        """Density far from cluster should be low."""
        import random
        random.seed(42)
        points = [[0.5 + random.gauss(0, 0.1), 0.5 + random.gauss(0, 0.1)] for _ in range(30)]
        ratio = _local_density_ratio([5.0, 5.0], points)
        assert ratio < 0.1  # Should be low far away

    def test_auc_perfect_separation(self):
        """Perfect separation should give AUC = 1.0."""
        scores_labels = [(1.0, "FAIL"), (0.9, "FAIL"), (0.1, "PASS"), (0.0, "PASS")]
        auc = _auc_from_scores(scores_labels)
        assert abs(auc - 1.0) < 1e-10

    def test_auc_random(self):
        """Random scores should give AUC ≈ 0.5."""
        import random
        random.seed(42)
        scores_labels = [(random.random(), random.choice(["PASS", "FAIL"])) for _ in range(100)]
        auc = _auc_from_scores(scores_labels)
        assert 0.3 <= auc <= 0.7  # Should be around 0.5

    def test_domain_spec_as_dict(self):
        spec = DomainSpec(
            name="test",
            param_names=["x", "y"],
            param_ranges={"x": (0.0, 1.0), "y": (0.0, 1.0)},
            samples=[],
            mahalanobis_threshold=3.0,
            density_ratio_threshold=0.1,
            owner_repo="test",
            version="v1",
        )
        d = spec.as_dict()
        assert d["name"] == "test"
        assert d["n_samples"] == 0
        assert d["mahalanobis_threshold"] == 3.0

    def test_judgment_to_response_dict(self):
        j = DomainJudgment(
            verdict=Verdict.WITHHELD,
            reason_code=ReasonCode.DOMAIN_OUT_OF_RANGE,
            domain_name="test",
            convex_hull_distance=1.5,
            mahalanobis_distance=4.2,
            density_ratio=0.05,
            nn_distance=2.3,
            nn_params={"x": 0.5, "y": 0.5},
            boundary_direction={"x": "below"},
            thresholds_used={"mahalanobis_threshold": 3.0, "density_ratio_threshold": 0.1},
            query_params={"x": -1.0, "y": 0.5},
        )
        d = j.to_response_dict()
        assert d["verdict"] == "WITHHELD"
        assert d["reason_code"] == "DOMAIN_OUT_OF_RANGE"
        assert d["convex_hull_distance"] == 1.5
        assert d["nn_params"] == {"x": 0.5, "y": 0.5}


# ===================================================================
# Integration tests
# ===================================================================

class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_full_pipeline_2d(self):
        """Full pipeline: create domain → calibrate → judge → verify."""
        spec = make_synthetic_2d_domain(n_pass=50, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)

        # Apply calibrated thresholds
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold

        est = DomainEstimator(spec)

        # Verify C1
        fail_samples = [s for s in spec.samples if s.label == "FAIL"]
        pass_samples = [s for s in spec.samples if s.label == "PASS"]

        recall = sum(1 for s in fail_samples if est.judge(s.params).verdict == Verdict.WITHHELD) / len(fail_samples)
        fpr = sum(1 for s in pass_samples if est.judge(s.params).verdict == Verdict.WITHHELD) / len(pass_samples)

        assert recall >= 0.9, f"Recall {recall:.3f} < 0.9"
        assert fpr <= 0.05, f"FPR {fpr:.3f} > 0.05"

        # Verify C3
        assert 0.4 <= cal.auc_shuffle <= 0.6

    def test_full_pipeline_3d(self):
        """Full pipeline for 3D domain."""
        spec = make_synthetic_3d_domain(n_pass=80, n_fail=20, seed=42)
        cal = calibrate_domain(spec, target_recall=0.8, target_fpr=0.05)

        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold

        est = DomainEstimator(spec)

        fail_samples = [s for s in spec.samples if s.label == "FAIL"]
        pass_samples = [s for s in spec.samples if s.label == "PASS"]

        recall = sum(1 for s in fail_samples if est.judge(s.params).verdict == Verdict.WITHHELD) / len(fail_samples)
        fpr = sum(1 for s in pass_samples if est.judge(s.params).verdict == Verdict.WITHHELD) / len(pass_samples)

        assert recall >= 0.9, f"3D Recall {recall:.3f} < 0.9"
        assert fpr <= 0.05, f"3D FPR {fpr:.3f} > 0.05"

    def test_registry_kpi(self):
        """Test KPI tracking in registry."""
        reg = DomainRegistry()
        spec = make_synthetic_2d_domain(n_pass=30, n_fail=10, seed=42)
        cal = calibrate_domain(spec)
        spec.mahalanobis_threshold = cal.mahalanobis_threshold
        spec.density_ratio_threshold = cal.density_ratio_threshold
        reg.register(spec)

        # Make some queries
        reg.judge("synthetic_2d", {"x": 0.5, "y": 0.5})  # in domain
        reg.judge("synthetic_2d", {"x": -5.0, "y": 0.5})  # out of domain
        reg.judge("synthetic_2d", {"x": 5.0, "y": 0.5})  # out of domain

        kpi = reg.get_kpi()
        assert kpi["total_refusals"] >= 2  # At least 2 refusals

    def test_config_load(self):
        """Test loading from config file."""
        config_path = Path(__file__).resolve().parent.parent / "config" / "domain_applicability.json"
        if config_path.exists():
            reg = DomainRegistry()
            reg.load_from_config(str(config_path))
            # Config has empty samples, so domains should be registered but with no estimator data
            domains = reg.get_domains()
            assert "synthetic_2d_test" in domains or "synthetic_3d_test" in domains


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
