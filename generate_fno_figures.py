"""FNO 模型报告图生成器。"""

import base64
import io
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.neural_operators.data_gen import generate_training_dataset, compute_resistance
from src.neural_operators.fno import FNOConfig, FNOModel
from src.neural_operators.training import train_fno


def fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def generate_all_figures():
    """生成所有报告图。"""
    figures = []

    # 生成数据
    X, Y, samples = generate_training_dataset(n_wigley=250, n_series60=250, n_kvlcc2=200, seed=42)

    # 图1：船型分布
    fig, ax = plt.subplots(figsize=(8, 5))
    ship_types = ["wigley", "series60", "kvlcc2"]
    colors = ["#2196F3", "#4CAF50", "#FF9800"]
    for i, st in enumerate(ship_types):
        mask = [s.ship_type == st for s in samples]
        fn_vals = X[mask, 5]
        rt_vals = Y[mask, 0]
        ax.scatter(fn_vals, rt_vals, c=colors[i], label=st, alpha=0.6, s=20)
    ax.set_xlabel("Froude Number (Fr)")
    ax.set_ylabel("Total Resistance R_T (N)")
    ax.set_title("Ship Resistance Database: Fr vs R_T by Hull Type")
    ax.legend()
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3)
    figures.append({"name": "fr_vs_rt_scatter", "caption": "Fr-R_T 散点图（三种船型）", "png_base64": fig_to_base64(fig)})
    plt.close(fig)

    # 图2：训练曲线
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    # 训练/验证损失
    n = len(X)
    indices = np.random.RandomState(42).permutation(n)
    n_train = int(0.70 * n)
    n_val = int(0.15 * n)
    X_train, Y_train = X[indices[:n_train]], Y[indices[:n_train]]
    X_val, Y_val = X[indices[n_train:n_train + n_val]], Y[indices[n_train:n_train + n_val]]

    Y_train_log = Y_train.copy()
    Y_train_log[:, 0] = np.log(Y_train[:, 0] + 1.0)
    Y_val_log = Y_val.copy()
    Y_val_log[:, 0] = np.log(Y_val[:, 0] + 1.0)

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

    axes[0].plot(metrics.train_losses, label="Train Loss", color="#2196F3")
    axes[0].plot(metrics.val_losses, label="Val Loss", color="#FF5722")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].set_title("Training & Validation Loss")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # R_T MAE 曲线
    axes[1].plot(metrics.rt_mae, label="RT MAE", color="#4CAF50")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("R_T MAE (N)")
    axes[1].set_title("R_T Mean Absolute Error")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    figures.append({"name": "training_curves", "caption": "训练曲线（损失 + R_T MAE）", "png_base64": fig_to_base64(fig)})
    plt.close(fig)

    # 图3：预测 vs 真实值
    X_test = X[indices[n_train + n_val:]]
    Y_test = Y[indices[n_train + n_val:]]

    Y_mean = Y_train_log.mean(axis=0)
    Y_std = Y_train_log.std(axis=0)
    Y_std[Y_std < 1e-10] = 1.0
    X_mean = X_train.mean(axis=0)
    X_std = X_train.std(axis=0)
    X_std[X_std < 1e-10] = 1.0

    model.eval()
    X_norm = (X_test - X_mean) / X_std
    with np.errstate(divide="ignore", invalid="ignore"):
        import torch
        with torch.no_grad():
            X_t = torch.tensor(X_norm, dtype=torch.float32)
            pred_norm = model(X_t).cpu().numpy()

    pred_denorm = pred_norm * Y_std + Y_mean
    rt_pred = np.exp(pred_denorm[:, 0]) - 1.0
    rt_true = Y_test[:, 0]

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(rt_true, rt_pred, alpha=0.5, s=15, c="#2196F3")
    max_val = max(rt_true.max(), rt_pred.max())
    ax.plot([0, max_val], [0, max_val], "r--", alpha=0.7, label="Perfect Prediction")
    ax.set_xlabel("True R_T (N)")
    ax.set_ylabel("Predicted R_T (N)")
    ax.set_title("FNO: Predicted vs True Resistance")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(True, alpha=0.3)
    figures.append({"name": "pred_vs_true_rt", "caption": "FNO 预测 vs 真实阻力（对数坐标）", "png_base64": fig_to_base64(fig)})
    plt.close(fig)

    # 图4：外推误差曲线
    fig, ax = plt.subplots(figsize=(7, 5))
    fn_range = np.linspace(0.1, 0.65, 20)
    errors = []
    rng = np.random.RandomState(789)
    for fn in fn_range:
        n_s = 20
        X_s = np.zeros((n_s, 9))
        X_s[:, 0] = rng.uniform(80, 200)
        X_s[:, 1] = X_s[:, 0] * rng.uniform(0.12, 0.18)
        X_s[:, 2] = X_s[:, 1] * rng.uniform(0.35, 0.5)
        X_s[:, 3] = rng.uniform(0.55, 0.82)
        X_s[:, 4] = X_s[:, 3] + 0.05
        X_s[:, 5] = fn
        X_s[:, 6] = rng.uniform(-90, 90)
        X_s[:, 7] = 1025.0
        X_s[:, 8] = 1.188e-6

        Y_s = np.array([compute_resistance(x[0], x[1], x[2], x[3], x[4], fn) for x in X_s])

        X_s_norm = (X_s - X_mean) / X_std
        with torch.no_grad():
            X_t = torch.tensor(X_s_norm, dtype=torch.float32)
            pred = model(X_t).cpu().numpy()
        pred_d = pred * Y_std + Y_mean
        rt_p = np.exp(pred_d[:, 0]) - 1.0
        err = float(np.mean(np.abs(rt_p - Y_s) / (np.abs(Y_s) + 1e-10))) * 100
        errors.append(err)

    ax.plot(fn_range, errors, "o-", color="#FF5722", linewidth=2, markersize=6)
    ax.axvline(x=0.5, color="gray", linestyle="--", alpha=0.5, label="Training boundary (Fr=0.5)")
    ax.set_xlabel("Froude Number (Fr)")
    ax.set_ylabel("Relative Error (%)")
    ax.set_title("FNO Extrapolation Error vs Froude Number")
    ax.legend()
    ax.grid(True, alpha=0.3)
    figures.append({"name": "extrapolation_error", "caption": "FNO 外推误差随 Fr 变化曲线", "png_base64": fig_to_base64(fig)})
    plt.close(fig)

    return figures


if __name__ == "__main__":
    figures = generate_all_figures()
    print(f"Generated {len(figures)} figures")
    for f in figures:
        print(f"  {f['name']}: {f['caption']}")
