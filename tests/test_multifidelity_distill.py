"""Distillation: teacher convergence, gradient correctness, transfer, controls."""

from __future__ import annotations

import numpy as np
import pytest

from src.multifidelity.distill import (
    DistillConfig,
    EmpiricalFormulaStudent,
    GridOperator,
    HighFidelityTeacher,
    StudentSurrogate,
    accuracy,
    build_student,
    cost_us,
    pareto_front,
    rel_rmse,
    sample_params,
    train_student,
)

PARAMS = (1.2, 0.05, 1.0, 0.8)


def test_grid_operator_is_second_order_convergent():
    coarse = GridOperator(64).solve(*PARAMS)
    mid = GridOperator(128).solve(*PARAMS)
    fine = GridOperator(256).solve(*PARAMS)
    assert abs((fine - mid) / (mid - coarse) - 0.25) < 0.1   # halving h quarters the error


def test_teacher_ensemble_agrees_with_richardson_reference():
    teacher = HighFidelityTeacher(grid_sizes=(128, 192, 256, 384), reference_sizes=(512, 1024))
    # moderate cases: the grid-refined ensemble is a teacher, not the truth. Its own
    # deviation from the reference is 0.16% on average and reaches ~1.8% in the
    # thinnest-layer corner of the parameter box (reported in the record).
    for params in (PARAMS, (0.4, 0.15, 0.7, -1.2), (1.0, 0.08, 1.1, 0.4)):
        mean, spread = teacher.evaluate(params)
        reference = teacher.reference(params)
        # the ensemble is the *teacher*, not the truth: its own discretisation error
        # is ~0.4% of the reference (reported as teacher val_rel_rmse in the record)
        assert abs(mean - reference) / abs(reference) < 2e-2
        assert spread < 0.05 * abs(reference)


def test_closed_form_baseline_recovers_an_exactly_linear_map():
    X = sample_params(60, seed=3)
    design = EmpiricalFormulaStudent._design(X)
    coef = np.array([0.3, -0.4, 0.2, 0.1, 0.05, -0.02, 0.01, 0.03])
    y = design @ coef
    model = EmpiricalFormulaStudent().fit(X, y)
    assert rel_rmse(model.predict(X), y) < 1e-10


def test_kl_shrinks_as_the_student_matches_the_teacher():
    cfg = DistillConfig(width=6, alpha=0.0, bins=41, temperature_rel=0.1)
    X = sample_params(24, seed=4)
    y = np.sin(3 * X[:, 0]) + X[:, 1]
    student = build_student(X, y, cfg)
    y_n = (y - student.y_center) / student.y_scale
    sigma_n = np.full(len(X), 1e-6)
    mask = np.array([True] * 4 + [False] * (len(X) - 4))

    far = StudentSurrogate(cfg, student.centers, student.x_mean, student.x_std,
                           student.y_center, student.y_scale)
    loss_far, _ = far.objective(X, y_n[:4], mask, y_n, sigma_n)
    assert loss_far > 0.0                      # KL >= 0, and strictly positive when off

    near = StudentSurrogate(cfg, student.centers, student.x_mean, student.x_std,
                            student.y_center, student.y_scale)
    for key in ("W1", "W2", "W3"):
        near.params[key] = near.params[key] * 0.0
    near.params["b3"] = np.array([float(np.mean(y_n))])
    loss_near, _ = near.objective(X, y_n[:4], mask, y_n, sigma_n)
    assert loss_near < loss_far


@pytest.mark.parametrize("alpha", [0.0, 0.5, 1.0])
def test_gradients_match_finite_differences(alpha):
    cfg = DistillConfig(width=6, alpha=alpha, bins=41, temperature_rel=0.1, l2=1e-3)
    X = sample_params(24, seed=5)
    y = np.sin(3 * X[:, 0]) + X[:, 1]
    student = build_student(X, y, cfg)
    y_n = (y - student.y_center) / student.y_scale
    sigma_n = np.full(len(X), 0.03 / student.y_scale)
    mask = np.array([True] * 6 + [False] * (len(X) - 6))
    y_hard_n = y_n[:6]
    loss, grads = student.objective(X, y_hard_n, mask, y_n, sigma_n)
    eps = 1e-6
    for key in ("W1", "b1", "W2", "b2", "W3", "b3"):
        index = tuple(0 for _ in student.params[key].shape)
        original = student.params[key][index]
        student.params[key][index] = original + eps
        plus, _ = student.objective(X, y_hard_n, mask, y_n, sigma_n)
        student.params[key][index] = original - eps
        minus, _ = student.objective(X, y_hard_n, mask, y_n, sigma_n)
        student.params[key][index] = original
        numeric = (plus - minus) / (2 * eps)
        assert abs(numeric - grads[key][index]) <= 1e-4 * max(1.0, abs(numeric)), key


def test_student_with_plenty_of_labels_is_accurate():
    teacher = HighFidelityTeacher(grid_sizes=(96, 128, 192, 256))
    X = sample_params(160, seed=6)
    X_val = sample_params(80, seed=7)
    y, spread = teacher.evaluate_batch(X)
    y_val = teacher.reference_batch(X_val)
    result = train_student(X[:100], y[:100], spread[:100], X, y, spread, X_val, y_val,
                           DistillConfig(width=24, alpha=0.5, epochs=250, bins=61),
                           teacher_cost=100.0)
    assert result.val_accuracy > 90.0
    assert result.speedup >= 10.0


def test_negative_distillation_disabled_loses_accuracy_when_labels_are_scarce():
    """Negative control: drop the KD term and the student falls behind by >5 points."""
    teacher = HighFidelityTeacher(grid_sizes=(96, 128, 192, 256))
    X = sample_params(180, seed=8)
    X_val = sample_params(80, seed=9)
    y, spread = teacher.evaluate_batch(X)
    y_val = teacher.reference_batch(X_val)
    n_hard = 8
    common = dict(epochs=250, bins=61, width=24)
    enabled = train_student(X[:n_hard], y[:n_hard], spread[:n_hard], X, y, spread, X_val, y_val,
                            DistillConfig(alpha=0.5, **common), teacher_cost=100.0)
    disabled = train_student(X[:n_hard], y[:n_hard], spread[:n_hard], X, y, spread, X_val, y_val,
                             DistillConfig(alpha=1.0, **common), teacher_cost=100.0)
    assert enabled.val_accuracy > disabled.val_accuracy + 5.0
    assert disabled.val_rel_rmse > 1.3 * enabled.val_rel_rmse


def test_speedup_is_measured_against_the_teacher():
    teacher = HighFidelityTeacher(grid_sizes=(96, 128, 192, 256))
    X = sample_params(30, seed=10)
    y, spread = teacher.evaluate_batch(X)
    teacher_cost = cost_us(lambda Z: teacher.evaluate_batch(Z)[0], X[:8], repeats=1)
    result = train_student(X[:20], y[:20], spread[:20], X, y, spread, X, y,
                           DistillConfig(width=16, alpha=0.5, epochs=120, bins=41),
                           teacher_cost=teacher_cost)
    assert result.speedup > 10.0
    assert result.cost_us < teacher_cost / 10.0


def test_pareto_front_is_non_dominated():
    from src.multifidelity.distill import DistillResult
    points = [
        DistillResult("a", 0.5, 8, 0.10, 90.0, 1.0, 100.0, 1.0, 1.0, 1.0, 10, 100),
        DistillResult("b", 0.5, 16, 0.05, 95.0, 2.0, 50.0, 1.0, 1.0, 1.0, 10, 100),
        DistillResult("c", 0.5, 32, 0.04, 96.0, 4.0, 25.0, 1.0, 1.0, 1.0, 10, 100),
        DistillResult("worse", 1.0, 16, 0.20, 80.0, 2.5, 40.0, 1.0, 1.0, 1.0, 10, 100),
    ]
    front = pareto_front(points)
    labels = [p.label for p in front]
    assert labels == ["a", "b", "c"]
    assert "worse" not in labels


def test_invalid_alpha_combinations_are_rejected():
    X = sample_params(12, seed=11)
    y = np.linspace(0.1, 1.0, len(X))
    sigma = np.full(len(X), 0.01)
    cfg = DistillConfig(alpha=0.5, epochs=5, bins=21)
    student = build_student(X, y, cfg)
    # KD requested but no soft labels supplied
    with pytest.raises(ValueError):
        student.fit(X, (y - student.y_center) / student.y_scale,
                    np.ones(len(X), bool), np.array([]), np.array([]))
    # task term requested but no hard labels supplied
    student = build_student(X, y, cfg)
    with pytest.raises(ValueError):
        student.fit(X, np.array([]), np.zeros(len(X), bool),
                    (y - student.y_center) / student.y_scale, sigma / student.y_scale)
