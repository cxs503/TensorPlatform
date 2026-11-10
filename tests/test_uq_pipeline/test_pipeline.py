"""Tests for the dual-engine UQ pipeline and Sobol sensitivity analysis."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from backend.services.pce_uq import Marginal
from backend.services.uq_pipeline import DualEnginePipeline, UQResult
from backend.services.uq_pipeline.sobol import (
    SobolResult,
    sobol_from_pce,
    sobol_mc_saltelli,
)


# --------------------------------------------------------------------------- #
# Test models (domain-free synthetic functions)
# --------------------------------------------------------------------------- #
def linear_model(a: float = 0.0, b: float = 0.0) -> float:
    """f(a, b) = 2a + 3b. Analytic: E=0, Var=4*Var(a)+9*Var(b)."""
    return 2.0 * a + 3.0 * b


def quadratic_model(x: float = 0.0) -> float:
    """f(x) = x². Analytic for x ~ U(-1,1): E=1/3, Var=8/45."""
    return x * x


def ishigami(x1: float = 0.0, x2: float = 0.0, x3: float = 0.0,
             a: float = 7.0, b: float = 0.1) -> float:
    """Ishigami & Homma (1990) test function for sensitivity analysis."""
    return math.sin(x1) + a * math.sin(x2) ** 2 + b * x3 ** 4 * math.sin(x1)


def product_model(x: float = 0.0, y: float = 0.0) -> float:
    """f(x, y) = x * y. Analytic for x,y ~ U(-1,1): E=0, Var=1/9."""
    return x * y


def shifted_linear(a: float = 0.0, b: float = 0.0) -> float:
    """f(a, b) = 2a + 3b + 10. E=10, Var=4*Var(a)+9*Var(b)."""
    return 2.0 * a + 3.0 * b + 10.0


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def uniform_marginals_2d():
    return [
        Marginal(name="a", family="uniform", lower=-1.0, upper=1.0),
        Marginal(name="b", family="uniform", lower=-1.0, upper=1.0),
    ]


@pytest.fixture
def uniform_marginals_3d():
    pi = math.pi
    return [
        Marginal(name="x1", family="uniform", lower=-pi, upper=pi),
        Marginal(name="x2", family="uniform", lower=-pi, upper=pi),
        Marginal(name="x3", family="uniform", lower=-pi, upper=pi),
    ]


# --------------------------------------------------------------------------- #
# Dual-engine pipeline tests
# --------------------------------------------------------------------------- #
class TestDualEnginePipeline:
    def test_linear_model_mean_zero(self, uniform_marginals_2d):
        """Linear model with symmetric inputs: E[y] = 0."""
        pipeline = DualEnginePipeline.__new__(DualEnginePipeline)
        pipeline._func = linear_model
        pipeline._marginals = uniform_marginals_2d
        pipeline._spec = type("FakeSpec", (), {"case_id": "test", "model": type("M", (), {"output": ""})()})()
        result = pipeline.run(pce_order=3, mc_n=5000)
        assert isinstance(result, UQResult)
        assert abs(result.pce_mean) < 0.1
        assert abs(result.mc_mean) < 0.1

    def test_quadratic_model_analytic(self):
        """f(x) = x², x ~ U(-1,1): E = 1/3, Var = 8/45."""
        marginals = [Marginal(name="x", family="uniform", lower=-1.0, upper=1.0)]
        pipeline = DualEnginePipeline.__new__(DualEnginePipeline)
        pipeline._func = quadratic_model
        pipeline._marginals = marginals
        pipeline._spec = type("FakeSpec", (), {"case_id": "quad", "model": type("M", (), {"output": ""})()})()
        result = pipeline.run(pce_order=4, mc_n=10000)
        # Analytic: E = 1/3 ≈ 0.3333
        assert abs(result.pce_mean - 1.0 / 3.0) < 0.01
        assert abs(result.mc_mean - 1.0 / 3.0) < 0.05

    def test_cross_validation_error_bounds(self, uniform_marginals_2d):
        """PCE and MC should agree within tolerance for a shifted model (non-zero mean)."""
        pipeline = DualEnginePipeline.__new__(DualEnginePipeline)
        pipeline._func = shifted_linear
        pipeline._marginals = uniform_marginals_2d
        pipeline._spec = type("FakeSpec", (), {"case_id": "cv", "model": type("M", (), {"output": ""})()})()
        result = pipeline.run(pce_order=3, mc_n=10000)
        # For a linear model, PCE should be exact
        assert result.mean_relative_error < 0.01
        assert result.std_relative_error < 0.02

    def test_speedup_reported(self, uniform_marginals_2d):
        """PCE should require fewer model evaluations than MC."""
        pipeline = DualEnginePipeline.__new__(DualEnginePipeline)
        pipeline._func = linear_model
        pipeline._marginals = uniform_marginals_2d
        pipeline._spec = type("FakeSpec", (), {"case_id": "speed", "model": type("M", (), {"output": ""})()})()
        result = pipeline.run(pce_order=3, mc_n=10000)
        assert result.speedup_model_evaluations > 1.0

    def test_as_dict_structure(self, uniform_marginals_2d):
        pipeline = DualEnginePipeline.__new__(DualEnginePipeline)
        pipeline._func = linear_model
        pipeline._marginals = uniform_marginals_2d
        pipeline._spec = type("FakeSpec", (), {"case_id": "dict", "model": type("M", (), {"output": ""})()})()
        result = pipeline.run(pce_order=3, mc_n=1000)
        d = result.as_dict()
        assert "pce" in d
        assert "mc" in d
        assert "cross_validation" in d
        assert "mean" in d["pce"]
        assert "mean" in d["mc"]


# --------------------------------------------------------------------------- #
# Sobol sensitivity tests
# --------------------------------------------------------------------------- #
class TestSobolPCE:
    def test_linear_model_sobol(self, uniform_marginals_2d):
        """f(a,b) = 2a + 3b. Var = 4*Var(a) + 9*Var(b).
        S_a = 4/(4+9) = 4/13, S_b = 9/13."""
        from backend.services.pce_uq import fit
        pce = fit(func=linear_model, marginals=uniform_marginals_2d, method="projection", order=3)
        result = sobol_from_pce(pce)
        assert isinstance(result, SobolResult)
        assert result.method == "pce_spectral"
        # S_a ≈ 4/13 ≈ 0.3077, S_b ≈ 9/13 ≈ 0.6923
        assert abs(result.first_order["a"] - 4.0 / 13.0) < 0.05
        assert abs(result.first_order["b"] - 9.0 / 13.0) < 0.05

    def test_sum_first_order_adds_up(self, uniform_marginals_2d):
        """For an additive model, sum(S_i) = 1."""
        from backend.services.pce_uq import fit
        pce = fit(func=linear_model, marginals=uniform_marginals_2d, method="projection", order=3)
        result = sobol_from_pce(pce)
        assert abs(result.sum_first_order - 1.0) < 0.01


class TestSobolMC:
    def test_ishigami_sobol_mc(self, uniform_marginals_3d):
        """Ishigami function: known analytic Sobol indices for a=7, b=0.1."""
        result = sobol_mc_saltelli(
            func=ishigami,
            marginals=uniform_marginals_3d,
            n=4096,
            seed=42,
        )
        assert isinstance(result, SobolResult)
        assert result.method == "mc_saltelli"
        assert result.n_model_evaluations > 0
        # x3 has zero first-order but non-zero total-order
        # (interaction with x1 through the sin(x1)*x3^4 term)
        # S1_x3 ≈ 0, ST_x3 > 0
        assert result.first_order["x3"] < 0.05  # near zero
        assert result.total_order["x3"] > 0.05  # non-zero due to interaction

    def test_product_model_sobol(self):
        """f(x,y) = x*y, x,y ~ U(-1,1). S_x = S_y = 0 (no main effect), S_T = 0.5 each."""
        marginals = [
            Marginal(name="x", family="uniform", lower=-1.0, upper=1.0),
            Marginal(name="y", family="uniform", lower=-1.0, upper=1.0),
        ]
        result = sobol_mc_saltelli(func=product_model, marginals=marginals, n=4096, seed=42)
        # Pure interaction: first-order should be near zero
        assert result.first_order["x"] < 0.1
        assert result.first_order["y"] < 0.1
        # Total-order should be ~0.5 each
        assert result.total_order["x"] > 0.3
        assert result.total_order["y"] > 0.3

    def test_sobol_result_ranking(self, uniform_marginals_2d):
        from backend.services.pce_uq import fit
        pce = fit(func=linear_model, marginals=uniform_marginals_2d, method="projection", order=3)
        result = sobol_from_pce(pce)
        ranking = result.ranking(by="total_order")
        assert ranking[0][0] == "b"  # b has higher sensitivity
        assert ranking[0][1] > ranking[1][1]
