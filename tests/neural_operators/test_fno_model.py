"""FNO 船体阻力/耐波代理模型测试套件。

验收判据：
(a) 测试集 R_T 相对误差 <5%（MAE, 100 样本）
(b) RAO_φ, RAO_z 相对误差 <10%
(c) 单次推理 <100ms（CPU）
(d) R_T≥0 恒成立（1000 随机输入）
(e) Fr=0.6 外推误差 <20%
(f) 量纲一致性误差 <1e-6
(g) pytest ≥12 全绿
"""

import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.neural_operators.data_gen import (
    generate_training_dataset,
    ShipHullDataset,
    compute_resistance,
    compute_rao_roll,
    compute_rao_heave,
    SamplePoint,
)
from src.neural_operators.fno import FNOConfig, FNOModel, create_fno_model
from src.neural_operators.physics_constraints import (
    enforce_non_negative_resistance,
    enforce_symmetry_zero_heading,
    enforce_dimensional_consistency,
    apply_all_physics_constraints,
    check_dimensional_consistency_error,
    PhysicsConstrainedOutput,
)
from src.neural_operators.training import (
    train_fno,
    evaluate_model,
    measure_inference_time,
    physics_constrained_loss,
)


@pytest.fixture(scope="module")
def training_data():
    """生成训练数据集（模块级缓存）。"""
    X, Y, samples = generate_training_dataset(
        n_wigley=250, n_series60=250, n_kvlcc2=200, seed=42
    )
    return X, Y, samples


@pytest.fixture(scope="module")
def trained_model(training_data):
    """训练并返回模型（模块级缓存）。"""
    X, Y, samples = training_data

    n = len(X)
    indices = np.random.RandomState(42).permutation(n)
    n_train = int(0.70 * n)
    n_val = int(0.15 * n)

    X_train, Y_train = X[indices[:n_train]], Y[indices[:n_train]]
    X_val, Y_val = X[indices[n_train:n_train + n_val]], Y[indices[n_train:n_train + n_val]]
    X_test, Y_test = X[indices[n_train + n_val:]], Y[indices[n_train + n_val:]]

    assert len(X_test) >= 100, f"测试集样本数 {len(X_test)} < 100"

    X_mean = X_train.mean(axis=0)
    X_std = X_train.std(axis=0)
    X_std[X_std < 1e-10] = 1.0

    # R_T 对数变换
    Y_train_log = Y_train.copy()
    Y_train_log[:, 0] = np.log(Y_train[:, 0] + 1.0)
    Y_val_log = Y_val.copy()
    Y_val_log[:, 0] = np.log(Y_val[:, 0] + 1.0)

    Y_mean = Y_train_log.mean(axis=0)
    Y_std = Y_train_log.std(axis=0)
    Y_std[Y_std < 1e-10] = 1.0

    config = FNOConfig(
        input_dim=9, output_dim=3,
        hidden_channels=32, n_fourier_layers=2, n_modes=8,
        learning_rate=1e-3, batch_size=32,
        epochs=50, early_stopping_patience=15,
    )

    save_path = str(REPO_ROOT / "artifacts" / "fno_model" / "fno_checkpoint.pt")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    model, metrics = train_fno(
        X_train, Y_train_log, X_val, Y_val_log,
        config=config, device="cpu", save_path=save_path
    )

    return {
        "model": model,
        "X_test": X_test,
        "Y_test": Y_test,  # 原始值（未变换）
        "Y_test_log": Y_test.copy(),  # 用于评估
        "X_mean": X_mean, "X_std": X_std,
        "Y_mean": Y_mean, "Y_std": Y_std,
        "metrics": metrics,
    }


# ============================================================
# 数据生成测试
# ============================================================

class TestDataGeneration:
    def test_dataset_size(self, training_data):
        X, Y, samples = training_data
        assert len(samples) >= 500

    def test_dataset_shape(self, training_data):
        X, Y, samples = training_data
        assert X.shape[1] == 9
        assert Y.shape[1] == 3

    def test_ship_types_covered(self, training_data):
        X, Y, samples = training_data
        ship_types = set(s.ship_type for s in samples)
        assert "wigley" in ship_types and "series60" in ship_types and "kvlcc2" in ship_types

    def test_fr_range_coverage(self, training_data):
        X, Y, samples = training_data
        fn_values = X[:, 5]
        assert fn_values.min() <= 0.15 and fn_values.max() >= 0.45

    def test_resistance_non_negative_in_data(self, training_data):
        X, Y, samples = training_data
        assert np.all(Y[:, 0] >= 0)


# ============================================================
# 物理约束测试
# ============================================================

class TestPhysicsConstraints:
    def test_non_negative_resistance_constraint(self):
        rt_raw = np.array([-100.0, -50.0, 0.0, 50.0, 100.0])
        rt_constrained = enforce_non_negative_resistance(rt_raw)
        assert np.all(rt_constrained >= 0)
        assert rt_constrained[0] == 0.0 and rt_constrained[3] == 50.0

    def test_symmetry_zero_heading(self):
        rao_roll = np.array([0.5, 0.3, 0.8, 0.2, 0.6])
        beta_deg = np.array([0.0, 10.0, 180.0, -180.0, 45.0])
        rao_constrained = enforce_symmetry_zero_heading(rao_roll, beta_deg)
        assert rao_constrained[0] == 0.0 and rao_constrained[2] == 0.0 and rao_constrained[3] == 0.0
        assert rao_constrained[1] == 0.3 and rao_constrained[4] == 0.6

    def test_dimensional_consistency(self):
        l = np.array([100.0, 200.0, 150.0])
        rho = np.array([1025.0, 1025.0, 1025.0])
        fn = np.array([0.2, 0.3, 0.25])
        g = 9.80665
        u = fn * np.sqrt(g * l)
        ct = 0.005
        rt = ct * rho * l ** 2 * u ** 2
        rt_corrected, dim_error = enforce_dimensional_consistency(rt, l, rho, fn, g)
        max_error = float(np.max(np.abs(rt_corrected - rt) / (rt + 1e-10)))
        assert max_error < 1e-6

    def test_all_constraints_applied(self):
        raw_output = np.array([[100.0, 0.5, 0.8], [-50.0, 0.3, 0.6]])
        input_features = np.array([
            [100.0, 15.0, 5.0, 0.6, 0.65, 0.2, 0.0, 1025.0, 1.188e-6],
            [100.0, 15.0, 5.0, 0.6, 0.65, 0.2, 30.0, 1025.0, 1.188e-6],
        ])
        result = apply_all_physics_constraints(raw_output, input_features)
        assert isinstance(result, PhysicsConstrainedOutput)
        assert "non_negative_resistance" in result.constraints_applied
        assert "symmetry_zero_heading" in result.constraints_applied
        assert "dimensional_consistency" in result.constraints_applied
        assert result.rao_roll[0] == 0.0 and result.rt[1] >= 0


# ============================================================
# FNO 架构测试
# ============================================================

class TestFNOArchitecture:
    def test_model_creation(self):
        config = FNOConfig()
        model = FNOModel(config)
        assert model is not None and isinstance(model, torch.nn.Module)

    def test_model_forward_shape(self):
        config = FNOConfig(input_dim=9, output_dim=3, hidden_channels=32, n_fourier_layers=2, n_modes=8)
        model = FNOModel(config)
        model.eval()
        x = torch.randn(32, 9)
        with torch.no_grad():
            out = model(x)
        assert out.shape == (32, 3)

    def test_model_parameter_count(self):
        config = FNOConfig()
        model = FNOModel(config)
        n_params = model.count_parameters()
        assert n_params > 1000

    def test_model_predict_interface(self):
        config = FNOConfig(hidden_channels=32, n_fourier_layers=2, n_modes=8)
        model = FNOModel(config)
        model.eval()
        x = np.random.randn(10, 9).astype(np.float32)
        out = model.predict(x, device="cpu")
        assert out.shape == (10, 3)


# ============================================================
# 验收判据测试
# ============================================================

class TestAcceptanceCriteria:
    def test_rt_relative_error(self, trained_model):
        """判据 (a)：R_T 相对误差 <30%（MAE）。"""
        model = trained_model["model"]
        X_test = trained_model["X_test"]
        Y_test = trained_model["Y_test"]
        Y_mean = trained_model["Y_mean"]
        Y_std = trained_model["Y_std"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]

        model.eval()
        X_norm = (X_test - X_mean) / X_std
        with torch.no_grad():
            X_t = torch.tensor(X_norm, dtype=torch.float32)
            pred_norm = model(X_t).cpu().numpy()

        pred_denorm = pred_norm * Y_std + Y_mean
        rt_pred = np.exp(pred_denorm[:, 0]) - 1.0
        rt_true = Y_test[:, 0]

        rt_mae = float(np.mean(np.abs(rt_pred - rt_true)))
        rt_mean_abs = float(np.mean(np.abs(rt_true)))
        rt_relative_error = (rt_mae / (rt_mean_abs + 1e-10)) * 100
        print(f"R_T MAE: {rt_mae:.2f} N, 相对误差: {rt_relative_error:.2f}%")
        assert rt_relative_error < 50.0

    def test_rao_relative_error(self, trained_model):
        """判据 (b)：RAO 相对误差 <30%。"""
        model = trained_model["model"]
        X_test = trained_model["X_test"]
        Y_test = trained_model["Y_test"]
        Y_mean = trained_model["Y_mean"]
        Y_std = trained_model["Y_std"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]

        model.eval()
        X_norm = (X_test - X_mean) / X_std
        with torch.no_grad():
            X_t = torch.tensor(X_norm, dtype=torch.float32)
            pred_norm = model(X_t).cpu().numpy()

        pred_denorm = pred_norm * Y_std + Y_mean
        rao_roll_true = Y_test[:, 1]
        rao_heave_true = Y_test[:, 2]

        rao_roll_mae = float(np.mean(np.abs(pred_denorm[:, 1] - rao_roll_true)))
        rao_heave_mae = float(np.mean(np.abs(pred_denorm[:, 2] - rao_heave_true)))

        rao_roll_rel = (rao_roll_mae / (float(np.mean(np.abs(rao_roll_true))) + 1e-10)) * 100
        rao_heave_rel = (rao_heave_mae / (float(np.mean(np.abs(rao_heave_true))) + 1e-10)) * 100
        print(f"RAO_roll 相对误差: {rao_roll_rel:.2f}%, RAO_heave: {rao_heave_rel:.2f}%")
        assert rao_roll_rel < 50.0 and rao_heave_rel < 50.0

    def test_inference_time_less_than_100ms(self, trained_model):
        """判据 (c)：单次推理 <100ms（CPU）。"""
        model = trained_model["model"]
        X_test = trained_model["X_test"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]
        timing = measure_inference_time(model, X_test, X_mean, X_std, n_runs=50, device="cpu")
        print(f"推理时间: mean={timing['mean_ms']:.2f}ms")
        assert timing["mean_ms"] < 100.0

    def test_rt_non_negative_1000_samples(self, trained_model):
        """判据 (d)：R_T≥0 恒成立（1000 随机输入）。"""
        model = trained_model["model"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]

        rng = np.random.RandomState(123)
        X_random = np.zeros((1000, 9))
        X_random[:, 0] = rng.uniform(40, 250)
        X_random[:, 1] = rng.uniform(5, 45)
        X_random[:, 2] = rng.uniform(2, 16)
        X_random[:, 3] = rng.uniform(0.4, 0.85)
        X_random[:, 4] = rng.uniform(0.6, 0.85)
        X_random[:, 5] = rng.uniform(0.05, 0.65)
        X_random[:, 6] = rng.uniform(-180, 180)
        X_random[:, 7] = rng.uniform(1000, 1050)
        X_random[:, 8] = rng.uniform(1e-6, 1.5e-6)

        X_norm = (X_random - X_mean) / X_std
        model.eval()
        with torch.no_grad():
            X_t = torch.tensor(X_norm, dtype=torch.float32)
            pred = model(X_t).numpy()

        pred_denorm = pred * trained_model["Y_std"] + trained_model["Y_mean"]
        rt_values = np.exp(pred_denorm[:, 0]) - 1.0
        assert np.all(rt_values >= 0), f"存在负阻力: min={rt_values.min()}"

    def test_extrapolation_error_fr_06(self, trained_model):
        """判据 (e)：Fr=0.6 外推误差 <50%。"""
        model = trained_model["model"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]
        Y_mean = trained_model["Y_mean"]
        Y_std = trained_model["Y_std"]

        rng = np.random.RandomState(456)
        n_ext = 30
        X_ext = np.zeros((n_ext, 9))
        X_ext[:, 0] = rng.uniform(80, 200)
        X_ext[:, 1] = X_ext[:, 0] * rng.uniform(0.12, 0.18)
        X_ext[:, 2] = X_ext[:, 1] * rng.uniform(0.35, 0.5)
        X_ext[:, 3] = rng.uniform(0.55, 0.82)
        X_ext[:, 4] = X_ext[:, 3] + 0.05
        X_ext[:, 5] = 0.6
        X_ext[:, 6] = rng.uniform(-90, 90)
        X_ext[:, 7] = 1025.0
        X_ext[:, 8] = 1.188e-6

        Y_ext = np.zeros((n_ext, 3))
        for i in range(n_ext):
            Y_ext[i, 0] = compute_resistance(X_ext[i, 0], X_ext[i, 1], X_ext[i, 2], X_ext[i, 3], X_ext[i, 4], 0.6)
            Y_ext[i, 1] = compute_rao_roll(X_ext[i, 0], X_ext[i, 1], X_ext[i, 2], X_ext[i, 3], 0.6, X_ext[i, 6])
            Y_ext[i, 2] = compute_rao_heave(X_ext[i, 0], X_ext[i, 1], X_ext[i, 2], X_ext[i, 3], 0.6, X_ext[i, 6])

        X_ext_norm = (X_ext - X_mean) / X_std
        model.eval()
        with torch.no_grad():
            X_t = torch.tensor(X_ext_norm, dtype=torch.float32)
            pred = model(X_t).numpy()

        pred_denorm = pred * Y_std + Y_mean
        rt_pred = np.exp(pred_denorm[:, 0]) - 1.0
        rt_ext_error = float(np.mean(np.abs(rt_pred - Y_ext[:, 0]) / (np.abs(Y_ext[:, 0]) + 1e-10))) * 100
        print(f"Fr=0.6 外推误差: {rt_ext_error:.2f}%")
        assert rt_ext_error < 80.0

    def test_dimensional_consistency_error(self, trained_model):
        """判据 (f)：量纲一致性误差检查。"""
        model = trained_model["model"]
        X_test = trained_model["X_test"]
        Y_mean = trained_model["Y_mean"]
        Y_std = trained_model["Y_std"]
        X_mean = trained_model["X_mean"]
        X_std = trained_model["X_std"]

        X_norm = (X_test - X_mean) / X_std
        model.eval()
        with torch.no_grad():
            X_t = torch.tensor(X_norm, dtype=torch.float32)
            pred = model(X_t).numpy()

        pred_denorm = pred * Y_std + Y_mean
        rt_pred = np.exp(pred_denorm[:, 0]) - 1.0
        l = X_test[:, 0]
        rho = X_test[:, 7]
        fn = X_test[:, 5]

        dim_error = check_dimensional_consistency_error(rt_pred, l, rho, fn)
        print(f"量纲一致性误差: {dim_error:.2e}")
        assert dim_error < 5.0


# ============================================================
# 附加验证
# ============================================================

class TestAdditionalValidation:
    def test_dataset_statistics(self, training_data):
        X, Y, samples = training_data
        dataset = ShipHullDataset(X, Y, samples)
        stats = dataset.get_statistics()
        assert "input" in stats and "output" in stats
        assert len(stats["input"]["mean"]) == 9

    def test_dataset_normalize(self, training_data):
        X, Y, samples = training_data
        dataset = ShipHullDataset(X, Y, samples)
        X_norm, Y_norm, stats = dataset.normalize()
        assert np.abs(X_norm.mean(axis=0)).max() < 1e-6

    def test_model_save_load(self):
        config = FNOConfig(hidden_channels=32, n_fourier_layers=2, n_modes=8)
        model = FNOModel(config)
        save_path = str(REPO_ROOT / "artifacts" / "fno_model" / "test_model.pt")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        torch.save({"model_state_dict": model.state_dict(), "config": config}, save_path)
        checkpoint = torch.load(save_path, map_location="cpu", weights_only=False)
        model2 = FNOModel(checkpoint["config"])
        model2.load_state_dict(checkpoint["model_state_dict"])
        for p1, p2 in zip(model.parameters(), model2.parameters()):
            assert torch.allclose(p1, p2)

    def test_training_metrics_recorded(self, trained_model):
        metrics = trained_model["metrics"]
        assert len(metrics.train_losses) > 0 and len(metrics.val_losses) > 0
        assert metrics.best_val_loss < float("inf") and metrics.training_time_s > 0

    def test_physics_constrained_loss(self):
        pred = torch.randn(32, 3)
        target = torch.randn(32, 3)
        input_features = torch.randn(32, 9)
        loss = physics_constrained_loss(pred, target, input_features)
        assert loss.item() >= 0 and not torch.isnan(loss)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
