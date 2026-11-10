"""Lyapunov spectrum: estimator validated on a linear map, then applied to the coupling."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tensorphysics.clock_sync import (  # noqa: E402
    CoupledOscillatorConfig,
    LyapunovError,
    coupled_window_map,
    linear_map_probe,
    lyapunov_spectrum,
)


def _spectrum(damping: float, n_steps: int = 1500) -> "object":
    cfg = CoupledOscillatorConfig(
        coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=1.0, damping=damping
    )
    return lyapunov_spectrum(
        coupled_window_map(cfg),
        [1.0, 0.0, 0.0, 0.0],
        n_steps=n_steps,
        dt=cfg.coupling_interval,
        transient_steps=200,
    )


def test_estimator_reproduces_the_exact_spectrum_of_a_linear_map():
    matrix = np.array([[0.9, 0.05, 0.0, 0.0], [0.0, 0.8, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.95]])
    result = lyapunov_spectrum(lambda x: matrix @ x, [1.0, 1.0, 1.0, 1.0], n_steps=400, dt=0.1)
    exact = linear_map_probe(matrix, 0.1)
    np.testing.assert_allclose(np.sort(result.exponents)[::-1], exact, atol=1e-6)
    assert result.leading <= 1e-9  # a maximal eigenvalue of 1.0 is the marginal case


def test_damped_coupled_map_is_stable():
    result = _spectrum(0.02)
    assert result.leading < 0.0
    assert result.stable()


def test_negative_damping_control_is_detected_as_unstable():
    result = _spectrum(-0.02)
    assert result.leading > 0.05
    assert not result.stable()


def test_conservative_case_is_marginal_within_the_estimator_band():
    result = _spectrum(0.0)
    assert abs(result.leading) <= 1e-2  # theory: 0; the band is the finite-difference noise


def test_series_converges_and_length_matches_the_run():
    result = _spectrum(0.02, n_steps=600)
    assert result.series.size == 600
    tail = result.series[-50:]
    assert result.series[-1] == pytest.approx(result.leading)  # last sample is the estimate
    assert float(np.mean(np.abs(tail - result.leading))) < 2e-2
    assert float(np.max(np.abs(tail - result.leading))) < 5e-2
    assert result.to_dict()["leading"] == pytest.approx(result.leading)


def test_invalid_arguments_fail_closed():
    with pytest.raises(LyapunovError):
        lyapunov_spectrum(lambda x: x, [1.0, 0.0], n_steps=0)
    with pytest.raises(LyapunovError):
        lyapunov_spectrum(lambda x: x, [1.0, 0.0], n_exponents=5)
    with pytest.raises(LyapunovError):
        lyapunov_spectrum(lambda x: x[:1], [1.0, 0.0], n_steps=10)  # dimension change