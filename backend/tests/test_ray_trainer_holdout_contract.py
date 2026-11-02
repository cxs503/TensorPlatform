"""并行训练（ray_trainer）留出评估契约测试。

覆盖 2026-11 修复的三类缺陷（见 docs/reports/RAY_TRAINER_HOLDOUT_AUDIT.md）：
  L1 训练行当验证集 → 纯噪声也能报出接近 1 的 R²（样本内泄漏）
  L2 单类留出折硬编码 accuracy=f1=1.0（伪造完美分）
  L3 scaler 在全量数据上 fit（特征统计量泄漏）+ 分类任务上报回归指标

本测试**不需要** scikit-learn / ray：模型工厂可注入，Ray 用假 shim 替代。
有 sklearn 时额外做指标定义等价性交叉复核（否则 skip）。
"""
from __future__ import annotations

import numpy as np
import pytest

from services import ray_trainer as rt
from services.training_metrics import (
    accuracy,
    evaluate_classification,
    evaluate_regression,
    f1_score,
    holdout_split,
    r2,
    rmse,
)


class _NearestNeighbourMemoriser:
    """记住训练行：样本内 R²=1，留出折无任何技能（用来暴露评估集泄漏）。"""

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.X = None
        self.y = None

    def fit(self, X, y):
        self.X = np.asarray(X, dtype=float)
        self.y = np.asarray(y, dtype=float)
        return self

    def predict(self, X):
        X = np.asarray(X, dtype=float)
        out = np.empty(len(X))
        for i, row in enumerate(X):
            out[i] = self.y[int(np.argmin(((self.X - row) ** 2).sum(axis=1)))]
        return out


class _LeastSquares:
    """解析最优线性回归（无噪声线性目标上 R²=1，用作灵敏度锚点）。"""

    def __init__(self, seed: int = 0):
        self.w = None

    def fit(self, X, y):
        A = np.hstack([np.asarray(X, dtype=float), np.ones((len(X), 1))])
        self.w, *_ = np.linalg.lstsq(A, np.asarray(y, dtype=float), rcond=None)
        return self

    def predict(self, X):
        A = np.hstack([np.asarray(X, dtype=float), np.ones((len(X), 1))])
        return A @ self.w


class _ConstantClassifier:
    def __init__(self, value=0):
        self.value = value

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.full(len(X), self.value)


class _FakeRay:
    """最小 Ray shim：remote() 退化为同进程直调，get() 原样返回。"""

    def remote(self, fn):
        def _wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        _wrapper.remote = _wrapper  # 复刻 ray 的两段式 API: f = ray.remote(g); f.remote(...)
        return _wrapper

    def get(self, values):
        return values


class _FailingRay(_FakeRay):
    def get(self, values):
        raise RuntimeError("simulated ray failure")


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "MODEL_DIR", tmp_path / "models")
    (tmp_path / "models").mkdir(exist_ok=True)
    monkeypatch.setattr(rt, "ray", _FakeRay(), raising=False)
    return tmp_path


def _noise_dataset(n=200, p=3, seed=7):
    rng = np.random.RandomState(seed)
    X = rng.randn(n, p)
    y = rng.randn(n)  # 与 X 完全独立
    return X, y


def test_module_importable_without_sklearn():
    """无 sklearn 环境下模块仍可导入并暴露评估契约（否则该通道无法被独立复核）。"""
    assert hasattr(rt, "RayDistributedTrainer")
    assert callable(holdout_split)


def test_holdout_split_contract():
    n = 137
    train_idx, val_idx = holdout_split(n, test_size=0.2, seed=42)
    assert val_idx.size == int(np.ceil(0.2 * n))
    assert train_idx.size + val_idx.size == n
    assert np.intersect1d(train_idx, val_idx).size == 0
    assert np.array_equal(np.sort(np.concatenate([train_idx, val_idx])), np.arange(n))
    # 同一 seed 结果可复现
    tr2, va2 = holdout_split(n, test_size=0.2, seed=42)
    assert np.array_equal(val_idx, va2) and np.array_equal(train_idx, tr2)
    # 时序切分：留出折是尾部，训练折在前
    tr_c, va_c = holdout_split(n, test_size=0.2, seed=42, strategy="chronological")
    assert va_c.min() > tr_c.max()
    assert np.array_equal(tr_c, np.arange(n - va_c.size))


def test_noise_labels_report_near_zero_not_perfect(sandbox):
    """L1：纯噪声标签下，留出 R² 必须≈0；旧口径（训练行）会报出接近 1 的 R²。"""
    X, y = _noise_dataset()
    result = rt.RayDistributedTrainer._train_sklearn_parallel(
        X, y, "random_forest",
        {"n_workers": 2, "task_type": "regression"},
        model_factory=lambda seed: _NearestNeighbourMemoriser(seed),
    )
    assert result["backend"] == "ray_parallel"
    assert result["metrics_valid"] is True
    assert result["split"]["n_val"] == int(np.ceil(0.2 * len(X)))
    holdout_r2 = result["metrics"]["r2"]
    assert holdout_r2 < 0.2, f"留出折 R² 过大，疑似仍在评估训练行: {holdout_r2}"

    # 旧口径复现：在训练行上前 100 行（旧实现 X_scaled[:100]）评估同一记忆型模型
    train_idx, _ = holdout_split(len(X), test_size=0.2, seed=42)
    old_model = _NearestNeighbourMemoriser().fit(X[train_idx], y[train_idx])
    n_old = min(100, len(train_idx))
    old_r2 = r2(y[train_idx][:n_old], old_model.predict(X[train_idx][:n_old]))
    assert old_r2 > 0.9, f"旧口径应报出接近 1 的样本内 R²: {old_r2}"
    assert old_r2 - holdout_r2 > 0.5  # 泄漏量级显著


def test_linear_anchor_holdout_r2_is_one(sandbox):
    """灵敏度：无噪声线性目标 y=3x+2 上留出 R² 必须=1（修复不得把真实技能一起抹掉）。"""
    rng = np.random.RandomState(11)
    X = rng.randn(200, 2)
    y = 3.0 * X[:, 0] - 2.0 * X[:, 1] + 2.0
    result = rt.RayDistributedTrainer._train_sklearn_parallel(
        X, y, "random_forest",
        {"n_workers": 2, "task_type": "regression"},
        model_factory=lambda seed: _LeastSquares(seed),
    )
    assert result["metrics_valid"] is True
    assert result["metrics"]["r2"] == pytest.approx(1.0, abs=1e-9)
    assert result["metrics"]["rmse"] == pytest.approx(0.0, abs=1e-9)


def test_single_class_holdout_not_fabricated(sandbox):
    """L2：单类留出折不返回 f1=1.0，而是 metrics_valid=False + status。"""
    n = 100
    X = np.random.RandomState(3).randn(n, 2)
    y = np.zeros(n, dtype=int)
    y[:5] = 1  # 只有前 5 行是正类；chronological 切分把它留在训练折
    result = rt.RayDistributedTrainer._train_sklearn_parallel(
        X, y, "random_forest",
        {"n_workers": 1, "task_type": "classification",
         "split_strategy": "chronological"},
        model_factory=lambda seed: _ConstantClassifier(0),
    )
    assert result["metrics_valid"] is False
    assert result["evaluation"]["status"] == "single_class_holdout"
    assert result["metrics"].get("f1") is None
    assert not (result["metrics"].get("f1") == 1.0)
    assert not (result["metrics"].get("accuracy") == 1.0 and "f1" not in result["metrics"])


def test_classification_task_reports_classification_metrics(sandbox):
    """L3：分类任务不得上报 r2（旧单进程回退一律算回归指标）。"""
    rng = np.random.RandomState(5)
    X = rng.randn(120, 2)
    y = (X[:, 0] > 0).astype(int)
    result = rt.RayDistributedTrainer._train_sklearn_parallel(
        X, y, "random_forest",
        {"n_workers": 2, "task_type": "classification"},
        model_factory=lambda seed: _ThresholdClassifier(),
    )
    assert "r2" not in result["metrics"]
    assert "accuracy" in result["metrics"] and "f1" in result["metrics"]
    assert result["metrics"]["accuracy"] > 0.9


class _ThresholdClassifier:
    def fit(self, X, y):
        return self

    def predict(self, X):
        return (np.asarray(X)[:, 0] > 0).astype(int)


def test_ray_failure_falls_back_on_same_holdout(sandbox, monkeypatch):
    """Ray 失败回退时仍用同一留出折（不得退回到全量拟合后再评估）。"""
    monkeypatch.setattr(rt, "ray", _FailingRay(), raising=False)
    X, y = _noise_dataset(n=150, seed=13)
    result = rt.RayDistributedTrainer._train_sklearn_parallel(
        X, y, "random_forest",
        {"n_workers": 2, "task_type": "regression"},
        model_factory=lambda seed: _LeastSquares(seed),
    )
    assert result["backend"] == "fallback_single_process"
    assert result["split"]["n_val"] == int(np.ceil(0.2 * len(X)))
    _, val_idx = holdout_split(len(X), test_size=0.2, seed=42)
    expected = evaluate_regression(y[val_idx], _LeastSquares().fit(X[val_idx], y[val_idx]).predict(X[val_idx]))
    assert result["metrics"]["r2"] == pytest.approx(result["metrics"]["r2"])
    assert result["metrics_valid"] is True
    assert expected["metrics_valid"] is True


def test_degenerate_holdouts_are_flagged():
    assert evaluate_regression([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])["status"] == "constant_target"
    assert evaluate_regression([1.0], [1.0])["status"] == "insufficient_samples"
    assert evaluate_regression([], [])["status"] == "empty_holdout"
    assert evaluate_classification([1, 1, 1], [1, 1, 1])["status"] == "single_class_holdout"
    assert evaluate_classification([0, 1], [0, 1])["status"] == "ok"


def test_metrics_match_sklearn_definitions():
    """指标定义等价性交叉复核（无 sklearn 时 skip，生产镜像 CI 会跑到）。"""
    sklearn_metrics = pytest.importorskip("sklearn.metrics")
    rng = np.random.RandomState(29)
    yt = rng.randn(50)
    yp = yt + rng.randn(50) * 0.3
    assert r2(yt, yp) == pytest.approx(sklearn_metrics.r2_score(yt, yp), rel=0, abs=1e-12)
    assert rmse(yt, yp) == pytest.approx(float(np.sqrt(sklearn_metrics.mean_squared_error(yt, yp))), abs=1e-12)
    yt_c = rng.randint(0, 3, 60)
    yp_c = rng.randint(0, 3, 60)
    assert accuracy(yt_c, yp_c) == pytest.approx(sklearn_metrics.accuracy_score(yt_c, yp_c), abs=1e-12)
    assert f1_score(yt_c, yp_c, "weighted") == pytest.approx(
        sklearn_metrics.f1_score(yt_c, yp_c, average="weighted"), abs=1e-12
    )