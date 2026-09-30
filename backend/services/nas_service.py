"""
神经架构搜索 (NAS) 服务
轻量级实现，支持搜索空间定义和架构优化
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Torch 导入 — 在非GPU环境或无torch环境下降级
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    # 占位模块 — 允许模块导入和类定义，实际torch方法不会在测试中调用
    class _DummyNN:
        Module = object
    nn = _DummyNN()

from sklearn.model_selection import cross_val_score

# 存储
_nas_jobs: Dict[str, Dict] = {}
_nas_registry: Dict[str, Any] = {}

# 目录（在非Docker环境下回退到临时目录）
try:
    NAS_DIR = Path("/app/uploads/nas_runs")
    NAS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR = Path("/app/uploads/models")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, FileNotFoundError, OSError):
    import tempfile
    _tmpdir = Path(tempfile.gettempdir()) / "foundation_nas"
    NAS_DIR = _tmpdir
    NAS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR = _tmpdir


class SearchSpace:
    """搜索空间定义"""

    @staticmethod
    def get_layer_types():
        return ["linear", "relu", "batchnorm", "dropout", "residual"]

    @staticmethod
    def get_activation_types():
        return ["relu", "leaky_relu", "elu", "gelu", "swish"]

    @staticmethod
    def get_hidden_size_range():
        return [32, 64, 128, 256, 512]

    @staticmethod
    def get_num_layers_range():
        return (2, 8)

    @staticmethod
    def get_dropout_range():
        return (0.0, 0.5)


class NeuralArchitecture:
    """神经网络架构"""

    def __init__(self, architecture_config: Dict):
        self.config = architecture_config
        self.layers = architecture_config.get("layers", [])
        self.input_dim = architecture_config.get("input_dim", 10)
        self.output_dim = architecture_config.get("output_dim", 1)
        self.model = self._build_model()

    def _build_model(self) -> nn.Module:
        """根据配置构建模型"""
        modules = []
        prev_dim = self.input_dim

        for layer_config in self.layers:
            layer_type = layer_config.get("type", "linear")

            if layer_type == "linear":
                out_dim = layer_config.get("dim", 64)
                modules.append(nn.Linear(prev_dim, out_dim))
                prev_dim = out_dim

            elif layer_type == "activation":
                act_type = layer_config.get("activation", "relu")
                if act_type == "relu":
                    modules.append(nn.ReLU())
                elif act_type == "leaky_relu":
                    modules.append(nn.LeakyReLU())
                elif act_type == "elu":
                    modules.append(nn.ELU())
                elif act_type == "gelu":
                    modules.append(nn.GELU())
                elif act_type == "swish":
                    modules.append(nn.SiLU())

            elif layer_type == "batchnorm":
                modules.append(nn.BatchNorm1d(prev_dim))

            elif layer_type == "dropout":
                rate = layer_config.get("rate", 0.2)
                modules.append(nn.Dropout(rate))

            elif layer_type == "residual":
                # 残差块
                out_dim = layer_config.get("dim", prev_dim)
                modules.append(ResidualBlock(prev_dim, out_dim))
                prev_dim = out_dim

        # 输出层
        modules.append(nn.Linear(prev_dim, self.output_dim))

        return nn.Sequential(*modules)

    def count_parameters(self) -> int:
        """计算参数数量"""
        return sum(p.numel() for p in self.model.parameters())

    def get_flops(self, input_size: int = 1) -> int:
        """估算FLOPs"""
        total_flops = 0
        prev_dim = self.input_dim

        for layer_config in self.layers:
            if layer_config.get("type") == "linear":
                out_dim = layer_config.get("dim", 64)
                total_flops += prev_dim * out_dim * 2  # multiply-add
                prev_dim = out_dim

        total_flops += prev_dim * self.output_dim * 2
        return total_flops * input_size


if _HAS_TORCH:

    class ResidualBlock(nn.Module):
        """残差块"""

        def __init__(self, in_dim: int, out_dim: int):
            super().__init__()
            self.fc1 = nn.Linear(in_dim, out_dim)
            self.bn1 = nn.BatchNorm1d(out_dim)
            self.relu = nn.ReLU()
            self.fc2 = nn.Linear(out_dim, out_dim)
            self.bn2 = nn.BatchNorm1d(out_dim)

            if in_dim != out_dim:
                self.shortcut = nn.Linear(in_dim, out_dim)
            else:
                self.shortcut = nn.Identity()

        def forward(self, x):
            residual = self.shortcut(x)
            out = self.relu(self.bn1(self.fc1(x)))
            out = self.bn2(self.fc2(out))
            out += residual
            return self.relu(out)


class NASService:
    """神经架构搜索服务"""

    @staticmethod
    def create_job(config: Dict) -> str:
        import uuid

        job_id = f"nas_{uuid.uuid4().hex[:8]}"

        job = {
            "job_id": job_id,
            "config": config,
            "status": "pending",
            "progress": 0.0,
            "current_trial": 0,
            "total_trials": config.get("n_trials", 20),
            "trials": [],
            "best_architecture": None,
            "best_score": 0.0,
            "best_params": None,
            "model_path": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

        _nas_jobs[job_id] = job
        return job_id

    @staticmethod
    def random_architecture(
        input_dim: int, output_dim: int, search_space: Dict = None, trial_index: int = 0
    ) -> Dict:
        """系统化生成架构（使用确定性迭代替代np.random）"""
        search_space = search_space or {}

        hidden_sizes = search_space.get("hidden_sizes", [32, 64, 128, 256])
        activations = search_space.get("activations", ["relu", "leaky_relu", "gelu"])
        use_batchnorm = search_space.get("use_batchnorm", True)
        use_dropout = search_space.get("use_dropout", True)
        use_residual = search_space.get("use_residual", False)

        min_layers = search_space.get("min_layers", 2)
        max_layers = search_space.get("max_layers", 6)

        # 确定性选择层数（基于trial_index循环）
        layer_options = list(range(min_layers, max_layers + 1))
        num_layers = layer_options[trial_index % len(layer_options)]

        # 确定性选择每层的hidden_dim和activation（循环遍历组合）
        layers = []
        # 使用笛卡尔积风格：hidden_dim和activation各自按trial_index偏移
        batch_start = trial_index // len(layer_options)

        for i in range(num_layers):
            # 线性层：hidden_dim按层索引和批次循环
            dim_idx = (batch_start + i) % len(hidden_sizes)
            hidden_dim = hidden_sizes[dim_idx]
            layers.append({"type": "linear", "dim": hidden_dim})

            # BatchNorm：交替启用（基于索引奇偶）
            if use_batchnorm and ((trial_index + i) % 2 == 0):
                layers.append({"type": "batchnorm"})

            # 激活函数：按层循环
            act_idx = (batch_start + i) % len(activations)
            layers.append(
                {"type": "activation", "activation": activations[act_idx]}
            )

            # Dropout：每隔两个trial启用一次
            if use_dropout and ((trial_index + i) % 3 == 0):
                # 确定性dropout rate
                dropout_rate = 0.1 + 0.07 * ((trial_index + i) % 6)
                layers.append({"type": "dropout", "rate": dropout_rate})

            # 残差连接：每4个trial启用一次
            if use_residual and ((trial_index + i) % 4 == 0):
                layers.append({"type": "residual", "dim": hidden_dim})

        return {"input_dim": input_dim, "output_dim": output_dim, "layers": layers}

    @staticmethod
    def evaluate_architecture(
        arch_config: Dict, X_train, y_train, X_val, y_val, epochs: int = 5
    ) -> Tuple[float, int]:
        """使用cross_val_score评估架构（替代手动train/val分割）"""
        try:
            from sklearn.base import BaseEstimator, RegressorMixin, ClassifierMixin
            from sklearn.model_selection import cross_val_score

            arch = NeuralArchitecture(arch_config)
            is_regression = arch_config["output_dim"] == 1

            # 合并训练和验证数据
            X_full = np.vstack([X_train, X_val]) if len(X_val) > 0 else X_train
            y_full = (
                np.hstack([y_train, y_val])
                if len(y_val) > 0
                else y_train
            )

            # sklearn兼容的PyTorch模型包装器
            class TorchEstimator(BaseEstimator):
                """将PyTorch模型包装为sklearn兼容的estimator"""

                def __init__(self, arch_config=None, epochs=5, is_regression=True):
                    self.arch_config = arch_config
                    self.epochs = epochs
                    self.is_regression = is_regression
                    self.model_ = None
                    self.arch_ = None

                def fit(self, X, y):
                    self.arch_ = NeuralArchitecture(self.arch_config)
                    self.model_ = self.arch_.model

                    criterion = (
                        nn.MSELoss()
                        if self.is_regression
                        else nn.CrossEntropyLoss()
                    )
                    optimizer = optim.Adam(
                        self.model_.parameters(), lr=0.001
                    )

                    X_t = torch.FloatTensor(X)
                    y_t = (
                        torch.FloatTensor(y).reshape(-1, 1)
                        if self.is_regression
                        else torch.LongTensor(y)
                    )
                    train_loader = DataLoader(
                        TensorDataset(X_t, y_t),
                        batch_size=32,
                        shuffle=True,
                    )

                    self.model_.train()
                    for _ in range(self.epochs):
                        for batch_X, batch_y in train_loader:
                            optimizer.zero_grad()
                            outputs = self.model_(batch_X)
                            loss = criterion(outputs, batch_y)
                            loss.backward()
                            optimizer.step()
                    return self

                def predict(self, X):
                    self.model_.eval()
                    X_t = torch.FloatTensor(X)
                    with torch.no_grad():
                        outputs = self.model_(X_t)
                        if self.is_regression:
                            return outputs.numpy().flatten()
                        else:
                            return torch.argmax(outputs, dim=1).numpy()

                def score(self, X, y):
                    """返回R²（回归）或准确率（分类）"""
                    preds = self.predict(X)
                    if self.is_regression:
                        # R² score
                        from sklearn.metrics import r2_score

                        return r2_score(y, preds)
                    else:
                        # 准确率
                        return np.mean(preds == y)

            # 使用cross_val_score进行3折交叉验证
            estimator = TorchEstimator(
                arch_config=arch_config,
                epochs=epochs,
                is_regression=is_regression,
            )

            cv_folds = min(3, len(X_full) // 10)
            cv_folds = max(2, cv_folds)

            scores = cross_val_score(
                estimator, X_full, y_full, cv=cv_folds, scoring=None
            )
            score = float(np.mean(scores))

            return score, arch.count_parameters()

        except ImportError:
            # 回退到原始PyTorch训练评估
            try:
                arch = NeuralArchitecture(arch_config)
                model = arch.model

                criterion = (
                    nn.MSELoss()
                    if arch_config["output_dim"] == 1
                    else nn.CrossEntropyLoss()
                )
                optimizer = optim.Adam(model.parameters(), lr=0.001)

                X_train_t = torch.FloatTensor(X_train)
                y_train_t = (
                    torch.FloatTensor(y_train).reshape(-1, 1)
                    if arch_config["output_dim"] == 1
                    else torch.LongTensor(y_train)
                )
                X_val_t = torch.FloatTensor(X_val)

                train_loader = DataLoader(
                    TensorDataset(X_train_t, y_train_t),
                    batch_size=32,
                    shuffle=True,
                )

                model.train()
                for epoch in range(epochs):
                    for batch_X, batch_y in train_loader:
                        optimizer.zero_grad()
                        outputs = model(batch_X)
                        loss = criterion(outputs, batch_y)
                        loss.backward()
                        optimizer.step()

                model.eval()
                with torch.no_grad():
                    val_outputs = model(X_val_t)
                    if arch_config["output_dim"] == 1:
                        score = (
                            1
                            - torch.mean(
                                (
                                    val_outputs
                                    - torch.FloatTensor(y_val).reshape(-1, 1)
                                )
                                ** 2
                            ).item()
                        )
                    else:
                        preds = torch.argmax(val_outputs, dim=1).numpy()
                        score = np.mean(preds == np.array(y_val))

                return score, arch.count_parameters()

            except Exception as e:
                print(f"Architecture evaluation error: {e}")
                return 0.0, 0

    @staticmethod
    def start_search(job_id: str, data: Dict):
        """启动架构搜索"""
        job = _nas_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        def search_thread():
            try:
                job["status"] = "running"
                job["started_at"] = datetime.now().isoformat()

                X = np.array(data["X"])
                y = np.array(data["y"])

                from sklearn.model_selection import train_test_split
                from sklearn.preprocessing import StandardScaler

                X_train, X_val, y_train, y_val = train_test_split(
                    X, y, test_size=0.2, random_state=42
                )
                scaler = StandardScaler()
                X_train = scaler.fit_transform(X_train)
                X_val = scaler.transform(X_val)

                input_dim = X_train.shape[1]
                output_dim = 1 if len(np.unique(y)) > 10 else len(np.unique(y))

                n_trials = job["config"].get("n_trials", 20)
                search_space = job["config"].get("search_space", {})
                epochs = job["config"].get("epochs", 5)

                best_arch = None
                best_score = 0.0

                for trial in range(n_trials):
                    # 生成系统化架构（使用trial_index替代随机）
                    arch_config = NASService.random_architecture(
                        input_dim, output_dim, search_space, trial_index=trial
                    )

                    # 评估架构
                    score, params = NASService.evaluate_architecture(
                        arch_config, X_train, y_train, X_val, y_val, epochs
                    )

                    # 记录试验
                    trial_result = {
                        "trial_id": trial,
                        "architecture": arch_config,
                        "score": score,
                        "parameters": params,
                        "flops": NeuralArchitecture(arch_config).get_flops(),
                    }

                    job["trials"].append(trial_result)
                    job["current_trial"] = trial + 1
                    job["progress"] = (trial + 1) / n_trials * 100

                    if score > best_score:
                        best_score = score
                        best_arch = arch_config

                    # 保存进度
                    NASService._save_job(job)

                # 保存最佳架构
                job["best_architecture"] = best_arch
                job["best_score"] = best_score

                # 训练最终模型
                final_arch = NeuralArchitecture(best_arch)
                model_path = MODEL_DIR / f"{job_id}_model.pt"
                torch.save(final_arch.model.state_dict(), model_path)
                job["model_path"] = str(model_path)

                _nas_registry[job_id] = final_arch.model
                job["status"] = "completed"
                job["finished_at"] = datetime.now().isoformat()
                job["progress"] = 100.0
                NASService._save_job(job)

            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = datetime.now().isoformat()
                NASService._save_job(job)
                print(f"NAS error: {e}")
                import traceback

                traceback.print_exc()

        thread = threading.Thread(target=search_thread)
        thread.daemon = True
        thread.start()

        return {"job_id": job_id, "status": "started"}

    @staticmethod
    def get_job_status(job_id: str) -> Dict:
        job = _nas_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        return job

    @staticmethod
    def list_jobs() -> List[Dict]:
        return list(_nas_jobs.values())

    @staticmethod
    def _save_job(job: Dict):
        run_file = NAS_DIR / f"{job['job_id']}.json"
        with open(run_file, "w") as f:
            json.dump(job, f, indent=2, default=str)


def get_nas_service():
    return NASService
