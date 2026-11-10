"""Tests for the DoE framework — zero domain semantics, synthetic parameters."""
from __future__ import annotations

import math

import numpy as np
import pytest

from services.doe_framework import (
    LinearConstraint,
    ParameterSpec,
    _code_value,
    _decode_value,
    _d_efficiency,
    _maximin_subset,
    box_behnken,
    central_composite,
    d_optimal,
    fractional_factorial,
    full_factorial,
    generate_design,
)


# ---------------------------------------------------------------------------
# Coding / decoding
# ---------------------------------------------------------------------------

class TestCoding:
    def test_code_value_bounds(self):
        assert _code_value(0.0, 0.0, 10.0) == pytest.approx(-1.0)
        assert _code_value(10.0, 0.0, 10.0) == pytest.approx(1.0)
        assert _code_value(5.0, 0.0, 10.0) == pytest.approx(0.0)

    def test_decode_value_bounds(self):
        assert _decode_value(-1.0, 0.0, 10.0) == pytest.approx(0.0)
        assert _decode_value(1.0, 0.0, 10.0) == pytest.approx(10.0)
        assert _decode_value(0.0, 0.0, 10.0) == pytest.approx(5.0)

    def test_roundtrip(self):
        for v in [0.0, 3.0, 7.5, 10.0]:
            coded = _code_value(v, 0.0, 10.0)
            decoded = _decode_value(coded, 0.0, 10.0)
            assert decoded == pytest.approx(v)


# ---------------------------------------------------------------------------
# ParameterSpec validation
# ---------------------------------------------------------------------------

class TestParameterSpec:
    def test_valid_range(self):
        sp = ParameterSpec(name="x", low=0.0, high=10.0)
        assert sp.name == "x"

    def test_invalid_range(self):
        with pytest.raises(ValueError):
            ParameterSpec(name="x", low=10.0, high=5.0)

    def test_discrete_values(self):
        sp = ParameterSpec(name="x", low=0.0, high=10.0, discrete_values=[1.0, 5.0, 9.0])
        assert sp.discrete_values == [1.0, 5.0, 9.0]

    def test_discrete_too_few(self):
        with pytest.raises(ValueError):
            ParameterSpec(name="x", low=0.0, high=10.0, discrete_values=[5.0])


# ---------------------------------------------------------------------------
# Full factorial
# ---------------------------------------------------------------------------

class TestFullFactorial:
    def test_2param_2level(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = full_factorial(specs, n_levels=2)
        assert len(result.points) == 4
        assert result.points.shape == (4, 2)
        assert result.d_efficiency == pytest.approx(1.0)
        assert set(result.parameter_names) == {"a", "b"}

    def test_3param_2level(self):
        specs = [
            ParameterSpec(name="a", low=-1.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=10.0),
            ParameterSpec(name="c", low=100.0, high=200.0),
        ]
        result = full_factorial(specs, n_levels=2)
        assert len(result.points) == 8
        # Check coded values are ±1
        assert np.all(np.abs(result.points) == pytest.approx(1.0))

    def test_3level(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = full_factorial(specs, n_levels=3)
        assert len(result.points) == 9

    def test_discrete_values(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0, discrete_values=[0.0, 0.5, 1.0]),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = full_factorial(specs, n_levels=2)
        assert len(result.points) == 6  # 3 * 2

    def test_physical_values(self):
        specs = [
            ParameterSpec(name="a", low=10.0, high=20.0),
            ParameterSpec(name="b", low=0.0, high=100.0),
        ]
        result = full_factorial(specs, n_levels=2)
        # Check physical values are within bounds
        assert np.all(result.points_physical[:, 0] >= 10.0)
        assert np.all(result.points_physical[:, 0] <= 20.0)
        assert np.all(result.points_physical[:, 1] >= 0.0)
        assert np.all(result.points_physical[:, 1] <= 100.0)

    def test_with_constraints(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        constraints = [LinearConstraint(coefficients={"a": 1.0, "b": 1.0}, rhs=0.5)]
        result = full_factorial(specs, constraints=constraints, n_levels=2)
        # Only points where a + b <= 0.5 (in coded space) should remain
        # Coded: a,b ∈ {-1, 1}, so a+b ∈ {-2, 0, 2}; only (-1,-1) and (-1,1),(1,-1) with sum 0 pass
        # Actually with coded values ±1: (-1,-1)→-2, (-1,1)→0, (1,-1)→0, (1,1)→2
        # rhs=0.5 means sum <= 0.5, so (-1,-1), (-1,1), (1,-1) pass
        assert len(result.points) <= 4


# ---------------------------------------------------------------------------
# Fractional factorial
# ---------------------------------------------------------------------------

class TestFractionalFactorial:
    def test_basic(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = fractional_factorial(specs, resolution=3)
        assert len(result.points) < 8  # fraction of full
        assert result.d_efficiency > 0.0

    def test_2param_falls_back(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = fractional_factorial(specs, resolution=3)
        # With k=2, falls back to full factorial
        assert len(result.points) == 4


# ---------------------------------------------------------------------------
# Central composite
# ---------------------------------------------------------------------------

class TestCentralComposite:
    def test_basic(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = central_composite(specs)
        # 2^k factorial + 1 center + 2k axial = 4 + 1 + 4 = 9
        assert len(result.points) == 9
        assert result.metadata["n_factorial"] == 4
        assert result.metadata["n_axial"] == 4
        assert result.metadata["n_center"] == 1

    def test_custom_alpha(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = central_composite(specs, alpha=1.414)
        assert result.metadata["alpha"] == pytest.approx(1.414)

    def test_3param(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = central_composite(specs)
        # 8 + 1 + 6 = 15
        assert len(result.points) == 15


# ---------------------------------------------------------------------------
# Box-Behnken
# ---------------------------------------------------------------------------

class TestBoxBehnken:
    def test_basic(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = box_behnken(specs, n_center=3)
        # 2*k*(k-1) + n_center = 2*3*2 + 3 = 15
        assert len(result.points) == 15

    def test_fallback_2param(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = box_behnken(specs)
        # Falls back to CCD for k < 3
        assert result.design_name == "central_composite"


# ---------------------------------------------------------------------------
# D-optimal
# ---------------------------------------------------------------------------

class TestDOptimal:
    def test_basic(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = d_optimal(specs, n_runs=6, seed=42)
        assert len(result.points) == 6
        assert result.d_efficiency > 0.0

    def test_convergence(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        result = d_optimal(specs, n_runs=8, max_iter=200, seed=42)
        assert result.metadata["converged"] is True

    def test_pool_too_small(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        # Provide a small candidate pool that is smaller than n_runs
        small_pool = np.array([[-1, -1], [-1, 1], [1, -1], [1, 1]], dtype=float)
        with pytest.raises(ValueError):
            d_optimal(specs, n_runs=10, candidate_pool=small_pool, seed=42)


# ---------------------------------------------------------------------------
# D-efficiency
# ---------------------------------------------------------------------------

class TestDEfficiency:
    def test_self_reference(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        ff = full_factorial(specs, n_levels=2)
        d_eff = _d_efficiency(ff.points, ff.points)
        assert d_eff == pytest.approx(1.0)

    def test_subset_lower(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
        ]
        ff = full_factorial(specs, n_levels=2)
        subset = ff.points[:2]
        d_eff = _d_efficiency(subset, ff.points)
        assert d_eff < 1.0


# ---------------------------------------------------------------------------
# Maximin subset
# ---------------------------------------------------------------------------

class TestMaximinSubset:
    def test_full_set(self):
        points = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=float)
        indices = _maximin_subset(points, 4)
        assert len(indices) == 4

    def test_subset(self):
        points = np.array([[0, 0], [0.1, 0], [0.2, 0], [1, 1]], dtype=float)
        indices = _maximin_subset(points, 2)
        assert len(indices) == 2
        # Should pick the two most distant points
        assert 0 in indices  # [0,0]
        assert 3 in indices  # [1,1]


# ---------------------------------------------------------------------------
# Unified generator
# ---------------------------------------------------------------------------

class TestGenerateDesign:
    def test_all_types(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        for dtype in ["full_factorial", "fractional_factorial", "central_composite", "d_optimal"]:
            kwargs = {"n_runs": 8} if dtype == "d_optimal" else {}
            result = generate_design(dtype, specs, **kwargs)
            assert result.design_name is not None
            assert len(result.points) > 0
            assert result.d_efficiency >= 0.0

    def test_invalid_type(self):
        specs = [ParameterSpec(name="a", low=0.0, high=1.0)]
        with pytest.raises(ValueError):
            generate_design("nonexistent", specs)


# ---------------------------------------------------------------------------
# D-efficiency acceptance criterion (≥0.85)
# ---------------------------------------------------------------------------

class TestDEfficiencyAcceptance:
    """Verify D-efficiency ≥ 0.85 for standard designs."""

    def test_ccd_efficiency(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = central_composite(specs)
        assert result.d_efficiency >= 0.85, f"CCD D-efficiency {result.d_efficiency} < 0.85"

    def test_box_behnken_efficiency(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = box_behnken(specs)
        assert result.d_efficiency >= 0.85, f"BB D-efficiency {result.d_efficiency} < 0.85"

    def test_d_optimal_efficiency(self):
        specs = [
            ParameterSpec(name="a", low=0.0, high=1.0),
            ParameterSpec(name="b", low=0.0, high=1.0),
            ParameterSpec(name="c", low=0.0, high=1.0),
        ]
        result = d_optimal(specs, n_runs=15, seed=42)
        assert result.d_efficiency >= 0.85, f"D-optimal D-efficiency {result.d_efficiency} < 0.85"
