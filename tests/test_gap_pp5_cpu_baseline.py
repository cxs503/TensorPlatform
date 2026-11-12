"""Tests for GAP-PP-5 differentiable path CPU baseline.

Covers:
- _as_float gradient preservation (TensorLBM fix)
- bench_diff_lbm_cpu.py harness structure
- analyze.py output validation
- End-to-end tau recovery on small grid
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
ARTIFACTS = HERE / "artifacts" / "gap_pp5_cpu_baseline"
TENSORLBM_ROOT = "/root/tensor-ecosystem/TensorLBM"


class TestAsFloatGradientPreservation:
    """Verify the _as_float fix preserves autograd gradients for tensors."""

    def test_as_float_returns_none_for_tensor(self):
        """_as_float must return None for torch.Tensor to preserve gradients."""
        sys.path.insert(0, str(Path(TENSORLBM_ROOT) / "src"))
        from tensorlbm.input_contract import _as_float
        import torch

        tau = torch.tensor(0.9, requires_grad=True)
        result = _as_float(tau, "tau", "test")
        assert result is None, f"_as_float should return None for tensor, got {result}"

    def test_check_relaxation_time_preserves_tensor(self):
        """check_relaxation_time must return the tensor unchanged when given a tensor."""
        sys.path.insert(0, str(Path(TENSORLBM_ROOT) / "src"))
        from tensorlbm.input_contract import check_relaxation_time
        import torch

        tau = torch.tensor(0.9, requires_grad=True)
        result = check_relaxation_time(tau)
        assert isinstance(result, torch.Tensor), f"Expected tensor, got {type(result)}"
        assert result.requires_grad, "Gradient must be preserved"

    def test_as_float_still_works_for_scalars(self):
        """_as_float must still convert plain scalars to float."""
        sys.path.insert(0, str(Path(TENSORLBM_ROOT) / "src"))
        from tensorlbm.input_contract import _as_float

        assert _as_float(0.9, "tau", "test") == pytest.approx(0.9)
        assert _as_float("0.9", "tau", "test") == pytest.approx(0.9)
        assert _as_float([1, 2], "tau", "test") is None  # list is not a scalar


class TestBenchmarkHarness:
    """Verify the benchmark harness structure and basic functionality."""

    def test_bench_script_exists(self):
        assert (ARTIFACTS / "bench_diff_lbm_cpu.py").exists()

    def test_analyze_script_exists(self):
        assert (ARTIFACTS / "analyze.py").exists()

    def test_entry_script_exists(self):
        assert (ARTIFACTS / "entry_gap_pp5.py").exists()

    def test_bench_script_syntax(self):
        """Verify bench script has valid Python syntax."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ARTIFACTS / "bench_diff_lbm_cpu.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_analyze_script_syntax(self):
        """Verify analyze script has valid Python syntax."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ARTIFACTS / "analyze.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_entry_script_syntax(self):
        """Verify entry script has valid Python syntax."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ARTIFACTS / "entry_gap_pp5.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"


class TestTauRecoverySmall:
    """End-to-end tau recovery on a small grid (fast validation)."""

    @pytest.mark.slow
    def test_tau_recovery_32_grid(self):
        """Run tau recovery on 32x32 grid; should converge to ~0.9."""
        import torch
        sys.path.insert(0, str(Path(TENSORLBM_ROOT) / "src"))

        from tensorlbm.d2q9 import equilibrium
        from tensorlbm.solver import collide_bgk, stream
        import math

        # Simple shear wave setup
        n = 32
        amplitude = 0.05
        dtype = torch.float64
        device = torch.device("cpu")
        steps = 20
        tau_star = 0.9
        tau_init = 0.55
        lr = 2000.0
        iters = 30

        TWO_PI = 2.0 * math.pi

        def shear_wave_f0():
            y, _x = torch.meshgrid(
                torch.arange(n, dtype=dtype, device=device),
                torch.arange(n, dtype=dtype, device=device),
                indexing="ij",
            )
            ux = amplitude * torch.sin(TWO_PI / n * y)
            return equilibrium(torch.ones_like(ux), ux, torch.zeros_like(ux))

        def rollout(f0, tau, n_steps):
            f = f0.clone()
            for _ in range(n_steps):
                f = stream(collide_bgk(f, tau))
            return f

        def macroscopic(f):
            rho = f.sum(dim=0)
            cx = torch.tensor([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=dtype, device=device)
            cy = torch.tensor([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=dtype, device=device)
            ux = (f * cx[:, None, None]).sum(dim=0) / rho
            uy = (f * cy[:, None, None]).sum(dim=0) / rho
            return rho, ux, uy

        # Generate target
        f0 = shear_wave_f0()
        with torch.no_grad():
            target = macroscopic(rollout(f0, torch.tensor(tau_star, device=device), steps))[1]

        # Optimize tau
        tau = torch.tensor(tau_init, device=device, requires_grad=True)

        for it in range(iters):
            tau_leaf = tau.detach().requires_grad_(True)
            rho, ux, uy = macroscopic(rollout(f0, tau_leaf, steps))
            loss = ((ux - target) ** 2).mean()
            grad = torch.autograd.grad(loss, tau_leaf)[0]
            with torch.no_grad():
                tau.copy_(tau - lr * grad)

        error_pct = abs(float(tau) - tau_star) / tau_star * 100
        assert error_pct < 15, f"Tau recovery failed: tau={float(tau):.5f}, error={error_pct:.2f}%"


class TestAnalysisPipeline:
    """Verify the analysis pipeline produces correct output."""

    def test_judge_error(self):
        """Test the error judgment logic."""
        from artifacts.gap_pp5_cpu_baseline.analyze import judge_error

        assert judge_error(0.0322) == "PASS"
        assert judge_error(14.9) == "PASS"
        assert judge_error(15.0) == "FAIL"
        assert judge_error(38.89) == "FAIL"
        assert judge_error(None) == "N/A"
