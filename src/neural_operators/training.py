"""FNO 训练管线。

Adam 优化器, lr=1e-3, batch=64, epochs=500, 早停 patience=20。
含物理约束损失函数。R_T 使用对数变换以压缩动态范围。
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

from .fno import FNOConfig, FNOModel
from .physics_constraints import apply_all_physics_constraints


@dataclass
class TrainingMetrics:
    """训练指标记录。"""
    train_losses: List[float] = field(default_factory=list)
    val_losses: List[float] = field(default_factory=list)
    rt_mae: List[float] = field(default_factory=list)
    rao_roll_mae: List[float] = field(default_factory=list)
    rao_heave_mae: List[float] = field(default_factory=list)
    best_val_loss: float = float("inf")
    best_epoch: int = 0
    final_epoch: int = 0
    training_time_s: float = 0.0
    early_stopped: bool = False


def physics_constrained_loss(
    pred: torch.Tensor,
    target: torch.Tensor,
    input_features: torch.Tensor,
    alpha_rt: float = 1.0,
    alpha_rao: float = 0.5,
    alpha_physics: float = 0.1
) -> torch.Tensor:
    """物理约束损失函数。

    L = α_rt·MSE(R_T) + α_rao·MSE(RAO) + α_physics·L_physics
    """
    rt_loss = nn.MSELoss()(pred[:, 0], target[:, 0])
    rao_loss = nn.MSELoss()(pred[:, 1:], target[:, 1:])

    physics_loss = torch.tensor(0.0, device=pred.device)

    # 对称性惩罚：β≈0 时 RAO_roll 应≈0
    beta = input_features[:, 6]
    beta_rad = beta * math.pi / 180.0
    sin_beta = torch.sin(beta_rad)
    symmetric_mask = torch.abs(sin_beta) < 0.01
    if symmetric_mask.any():
        physics_loss += torch.mean(pred[symmetric_mask, 1] ** 2)

    return alpha_rt * rt_loss + alpha_rao * rao_loss + alpha_physics * physics_loss


def train_fno(
    X_train: np.ndarray,
    Y_train: np.ndarray,
    X_val: np.ndarray,
    Y_val: np.ndarray,
    config: Optional[FNOConfig] = None,
    device: str = "cpu",
    save_path: Optional[str] = None,
) -> Tuple[FNOModel, TrainingMetrics]:
    """训练 FNO 模型。

    R_T 使用对数变换：log(R_T + 1) 以压缩 4 个数量级的动态范围。
    """
    config = config or FNOConfig()

    # 数据归一化
    X_mean = X_train.mean(axis=0)
    X_std = X_train.std(axis=0)
    X_std[X_std < 1e-10] = 1.0

    # R_T 对数变换
    Y_train_transformed = Y_train.copy()
    Y_train_transformed[:, 0] = np.log(Y_train[:, 0] + 1.0)

    Y_val_transformed = Y_val.copy()
    Y_val_transformed[:, 0] = np.log(Y_val[:, 0] + 1.0)

    Y_mean = Y_train_transformed.mean(axis=0)
    Y_std = Y_train_transformed.std(axis=0)
    Y_std[Y_std < 1e-10] = 1.0

    X_train_norm = (X_train - X_mean) / X_std
    Y_train_norm = (Y_train_transformed - Y_mean) / Y_std
    X_val_norm = (X_val - X_mean) / X_std
    Y_val_norm = (Y_val_transformed - Y_mean) / Y_std

    # 转换为 Tensor
    X_train_t = torch.tensor(X_train_norm, dtype=torch.float32)
    Y_train_t = torch.tensor(Y_train_norm, dtype=torch.float32)
    X_val_t = torch.tensor(X_val_norm, dtype=torch.float32)
    Y_val_t = torch.tensor(Y_val_norm, dtype=torch.float32)

    # DataLoader
    train_dataset = TensorDataset(X_train_t, Y_train_t)
    train_loader = DataLoader(
        train_dataset, batch_size=config.batch_size, shuffle=True
    )

    # 模型
    model = FNOModel(config).to(device)

    # 优化器
    optimizer = optim.Adam(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay
    )

    # 学习率调度器
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=10, min_lr=1e-6
    )

    # 训练循环
    metrics = TrainingMetrics()
    patience_counter = 0
    start_time = time.time()

    for epoch in range(config.epochs):
        # 训练阶段
        model.train()
        train_loss = 0.0
        n_batches = 0

        for X_batch, Y_batch in train_loader:
            X_batch = X_batch.to(device)
            Y_batch = Y_batch.to(device)

            optimizer.zero_grad()
            pred = model(X_batch)
            loss = physics_constrained_loss(pred, Y_batch, X_batch)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        avg_train_loss = train_loss / max(n_batches, 1)
        metrics.train_losses.append(avg_train_loss)

        # 验证阶段
        model.eval()
        with torch.no_grad():
            X_val_d = X_val_t.to(device)
            Y_val_d = Y_val_t.to(device)
            pred_val = model(X_val_d)
            val_loss = physics_constrained_loss(pred_val, Y_val_d, X_val_d).item()

            # 反归一化 + 反变换计算 MAE
            pred_val_np = pred_val.cpu().numpy()
            Y_val_np = Y_val_t.cpu().numpy()

            pred_val_denorm = pred_val_np * Y_std + Y_mean
            Y_val_denorm = Y_val_np * Y_std + Y_mean

            # R_T 反变换：exp(y) - 1
            rt_pred = np.exp(pred_val_denorm[:, 0]) - 1.0
            rt_true = np.exp(Y_val_denorm[:, 0]) - 1.0

            rt_mae = float(np.mean(np.abs(rt_pred - rt_true)))
            rao_roll_mae = float(np.mean(np.abs(pred_val_denorm[:, 1] - Y_val_denorm[:, 1])))
            rao_heave_mae = float(np.mean(np.abs(pred_val_denorm[:, 2] - Y_val_denorm[:, 2])))

        metrics.val_losses.append(val_loss)
        metrics.rt_mae.append(rt_mae)
        metrics.rao_roll_mae.append(rao_roll_mae)
        metrics.rao_heave_mae.append(rao_heave_mae)

        scheduler.step(val_loss)

        # 早停检查
        if val_loss < metrics.best_val_loss:
            metrics.best_val_loss = val_loss
            metrics.best_epoch = epoch
            patience_counter = 0

            if save_path:
                torch.save({
                    "model_state_dict": model.state_dict(),
                    "config": config,
                    "normalization": {
                        "X_mean": X_mean.tolist(),
                        "X_std": X_std.tolist(),
                        "Y_mean": Y_mean.tolist(),
                        "Y_std": Y_std.tolist(),
                    },
                    "epoch": epoch,
                    "val_loss": val_loss,
                }, save_path)
        else:
            patience_counter += 1

        if patience_counter >= config.early_stopping_patience:
            metrics.early_stopped = True
            metrics.final_epoch = epoch
            break

        metrics.final_epoch = epoch

        if (epoch + 1) % 20 == 0 or epoch == 0:
            print(f"Epoch {epoch+1}/{config.epochs} | "
                  f"Train Loss: {avg_train_loss:.6f} | Val Loss: {val_loss:.6f} | "
                  f"RT MAE: {rt_mae:.2f} | RAO_roll MAE: {rao_roll_mae:.4f} | "
                  f"RAO_heave MAE: {rao_heave_mae:.4f}")

    metrics.training_time_s = time.time() - start_time

    if save_path and os.path.exists(save_path):
        checkpoint = torch.load(save_path, map_location=device, weights_only=False)
        model.load_state_dict(checkpoint["model_state_dict"])

    return model, metrics


def evaluate_model(
    model: FNOModel,
    X_test: np.ndarray,
    Y_test: np.ndarray,
    X_mean: np.ndarray,
    X_std: np.ndarray,
    Y_mean: np.ndarray,
    Y_std: np.ndarray,
    device: str = "cpu"
) -> Dict[str, float]:
    """评估模型性能。"""
    model.eval()

    X_norm = (X_test - X_mean) / X_std

    with torch.no_grad():
        X_t = torch.tensor(X_norm, dtype=torch.float32).to(device)
        pred_norm = model(X_t).cpu().numpy()

    # 反归一化
    pred_denorm = pred_norm * Y_std + Y_mean

    # R_T 反变换
    rt_pred = np.exp(pred_denorm[:, 0]) - 1.0
    rt_true = np.exp(Y_test[:, 0]) - 1.0  # Y_test 已经是 log 变换后的

    rt_mae = float(np.mean(np.abs(rt_pred - rt_true)))
    rt_mape = float(np.mean(np.abs((rt_pred - rt_true) / (np.abs(rt_true) + 1e-10)))) * 100

    rao_roll_mae = float(np.mean(np.abs(pred_denorm[:, 1] - Y_test[:, 1])))
    rao_roll_mape = float(np.mean(np.abs((pred_denorm[:, 1] - Y_test[:, 1]) / (np.abs(Y_test[:, 1]) + 1e-10)))) * 100

    rao_heave_mae = float(np.mean(np.abs(pred_denorm[:, 2] - Y_test[:, 2])))
    rao_heave_mape = float(np.mean(np.abs((pred_denorm[:, 2] - Y_test[:, 2]) / (np.abs(Y_test[:, 2]) + 1e-10)))) * 100

    return {
        "rt_mae": rt_mae,
        "rt_mape_pct": rt_mape,
        "rao_roll_mae": rao_roll_mae,
        "rao_roll_mape_pct": rao_roll_mape,
        "rao_heave_mae": rao_heave_mae,
        "rao_heave_mape_pct": rao_heave_mape,
    }


def measure_inference_time(
    model: FNOModel,
    X: np.ndarray,
    X_mean: np.ndarray,
    X_std: np.ndarray,
    n_runs: int = 100,
    device: str = "cpu"
) -> Dict[str, float]:
    """测量推理时间。"""
    model.eval()
    X_norm = (X - X_mean) / X_std
    X_t = torch.tensor(X_norm, dtype=torch.float32).to(device)

    with torch.no_grad():
        _ = model(X_t[:1])

    times = []
    with torch.no_grad():
        for _ in range(n_runs):
            start = time.perf_counter()
            _ = model(X_t)
            end = time.perf_counter()
            times.append((end - start) * 1000)

    return {
        "mean_ms": float(np.mean(times)),
        "std_ms": float(np.std(times)),
        "max_ms": float(np.max(times)),
        "min_ms": float(np.min(times)),
    }
