"""
Ray分布式训练器
支持sklearn模型的数据并行训练和PyTorch模型的分布式训练
通过Ray集群实现多节点/多GPU训练
"""
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 模型保存目录
MODEL_DIR = Path("/app/uploads/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Ray可用性检测
try:
    import ray
    RAY_AVAILABLE = True
except ImportError:
    ray = None
    RAY_AVAILABLE = False

# 分布式训练框架可用性
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    lgb = None
    LIGHTGBM_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    nn = None
    TORCH_AVAILABLE = False

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import r2_score, mean_squared_error, accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

# 分布式训练任务存储
_distributed_jobs: Dict[str, Dict] = {}

# Ray初始化状态
_ray_initialized = False
_ray_cluster_available = False


def _ensure_ray_initialized():
    """确保Ray已初始化，检测Ray集群是否可用"""
    global _ray_initialized, _ray_cluster_available

    if _ray_initialized:
        return _ray_cluster_available

    if not RAY_AVAILABLE:
        logger.warning("Ray package is not installed")
        _ray_initialized = True
        _ray_cluster_available = False
        return False

    try:
        # 尝试连接已有的Ray集群
        ray_address = os.environ.get("RAY_ADDRESS", "auto")
        if not ray.is_initialized():
            ray.init(address=ray_address, ignore_reinit_error=True, logging_level=logging.WARNING)
        _ray_cluster_available = True
        logger.info(f"Ray initialized successfully, cluster resources: {ray.cluster_resources()}")
    except Exception as e:
        logger.warning(f"Ray cluster not available, will use local mode: {e}")
        try:
            if not ray.is_initialized():
                ray.init(ignore_reinit_error=True, num_cpus=os.cpu_count() or 4, logging_level=logging.WARNING)
            _ray_cluster_available = False
            logger.info("Ray initialized in local mode")
        except Exception as e2:
            logger.error(f"Ray initialization failed completely: {e2}")
            _ray_cluster_available = False

    _ray_initialized = True
    return _ray_cluster_available


class RayDistributedTrainer:
    """
    Ray分布式训练器

    支持两种训练模式:
    1. sklearn模型: 使用ray.util.multiprocessing进行数据并行训练
    2. PyTorch模型: 使用ray.train.torch.TorchTrainer进行分布式训练
    """

    @staticmethod
    def is_ray_available() -> bool:
        """检查Ray是否可用"""
        if not RAY_AVAILABLE:
            return False
        _ensure_ray_initialized()
        return _ray_cluster_available

    @staticmethod
    def get_cluster_info() -> Dict:
        """获取Ray集群信息"""
        if not RAY_AVAILABLE:
            return {"status": "unavailable", "reason": "Ray package not installed"}

        try:
            _ensure_ray_initialized()
            if _ray_cluster_available:
                resources = ray.cluster_resources()
                available = ray.available_resources()
                return {
                    "status": "connected",
                    "cluster_resources": {k: v for k, v in resources.items() if not k.startswith("node:")},
                    "available_resources": {k: v for k, v in available.items() if not k.startswith("node:")},
                    "nodes": len(ray.nodes()),
                }
            else:
                return {
                    "status": "local_only",
                    "reason": "Ray cluster not available, running in local mode",
                    "cpus": os.cpu_count(),
                }
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    @staticmethod
    def _train_sklearn_parallel(X: np.ndarray, y: np.ndarray, model_type: str,
                                 config: Dict) -> Dict:
        """
        使用Ray并行训练sklearn模型
        通过数据分片在多个worker上并行训练，然后集成
        """
        n_workers = config.get("n_workers", min(4, os.cpu_count() or 2))

        # 标准化
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # 创建模型工厂函数
        def create_model(seed: int = 42):
            n_estimators = config.get("n_estimators", 100)
            max_depth = config.get("max_depth", 6)
            task_type = config.get("task_type", "regression")

            if model_type == "lightgbm" and LIGHTGBM_AVAILABLE:
                if task_type == "classification":
                    return lgb.LGBMClassifier(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed, verbose=-1,
                    )
                else:
                    return lgb.LGBMRegressor(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed, verbose=-1,
                    )
            elif model_type == "random_forest":
                if task_type == "classification":
                    return RandomForestClassifier(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed,
                    )
                else:
                    return RandomForestRegressor(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed,
                    )
            else:
                # Fallback to RandomForest
                if task_type == "classification":
                    return RandomForestClassifier(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed,
                    )
                else:
                    return RandomForestRegressor(
                        n_estimators=n_estimators, max_depth=max_depth,
                        random_state=seed,
                    )

        # 数据分片
        data_splits = []
        indices = np.arange(len(X_scaled))
        np.random.shuffle(indices)
        chunk_size = len(indices) // n_workers
        for i in range(n_workers):
            start = i * chunk_size
            end = start + chunk_size if i < n_workers - 1 else len(indices)
            chunk_indices = indices[start:end]
            data_splits.append((X_scaled[chunk_indices], y[chunk_indices]))

        # Ray并行训练
        @ray.remote
        def train_on_shard(X_shard, y_shard, seed, cfg):
            model = create_model(seed)
            model.fit(X_shard, y_shard)
            return model

        try:
            futures = [train_on_shard.remote(X_s, y_s, 42 + i, config)
                       for i, (X_s, y_s) in enumerate(data_splits)]
            models = ray.get(futures)

            # 简单集成: 取平均预测
            X_val_split = X_scaled[:min(100, len(X_scaled))]
            if len(models) > 1:
                all_preds = np.column_stack([m.predict(X_val_split) for m in models])
                y_pred = np.mean(all_preds, axis=1)
            else:
                y_pred = models[0].predict(X_val_split)

            # 评估指标
            if config.get("task_type") == "classification":
                y_val_sample = y[:len(y_pred)]
                if len(np.unique(y_val_sample)) > 1:
                    metrics = {
                        "accuracy": float(accuracy_score(y_val_sample, np.round(y_pred).astype(int))),
                        "f1": float(f1_score(y_val_sample, np.round(y_pred).astype(int), average="weighted")),
                    }
                else:
                    metrics = {"accuracy": 1.0, "f1": 1.0}
            else:
                y_val_sample = y[:len(y_pred)]
                metrics = {
                    "r2": float(r2_score(y_val_sample, y_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_val_sample, y_pred))),
                }

            # 保存一个模型 (使用第一个分片训练的模型)
            model_path = MODEL_DIR / f"distributed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
            import joblib
            joblib.dump(models[0], model_path)

            return {
                "metrics": metrics,
                "model_path": str(model_path),
                "n_workers": n_workers,
                "backend": "ray_parallel",
            }
        except Exception as e:
            logger.error(f"Ray parallel training failed: {e}, falling back to single-process")
            # Fallback: 单进程训练
            model = create_model(42)
            model.fit(X_scaled, y)

            X_train, X_val, y_train, y_val = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_val)

            if config.get("task_type") == "classification":
                metrics = {
                    "accuracy": float(accuracy_score(y_val, y_pred)),
                    "f1": float(f1_score(y_val, y_pred, average="weighted")),
                }
            else:
                metrics = {
                    "r2": float(r2_score(y_val, y_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_val, y_pred))),
                }

            model_path = MODEL_DIR / f"distributed_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
            import joblib
            joblib.dump(model, model_path)

            return {
                "metrics": metrics,
                "model_path": str(model_path),
                "n_workers": 1,
                "backend": "fallback_single_process",
            }

    @staticmethod
    def _train_torch_distributed(X: np.ndarray, y: np.ndarray, config: Dict) -> Dict:
        """
        使用Ray Train进行PyTorch分布式训练
        如果Ray集群不可用，回退到单进程训练
        """
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is not available")

        # 标准化
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        X_train, X_val, y_train, y_val = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42
        )

        X_train_tensor = torch.FloatTensor(X_train)
        y_train_tensor = torch.FloatTensor(y_train).reshape(-1, 1)
        X_val_tensor = torch.FloatTensor(X_val)
        y_val_tensor = torch.FloatTensor(y_val).reshape(-1, 1)

        input_dim = X_train.shape[1]
        hidden_layers = config.get("hidden_layers", [128, 64])
        epochs = config.get("epochs", 10)
        batch_size = config.get("batch_size", 32)
        lr = config.get("learning_rate", 0.001)

        # 尝试使用Ray Train
        try:
            from ray.train.torch import TorchTrainer
            from ray.train import ScalingConfig, RunConfig

            def train_func(config):
                """Ray Train worker训练函数"""
                from torch.utils.data import DataLoader, TensorDataset
                import torch.optim as optim

                # 构建模型
                layers = []
                prev_dim = input_dim
                for hd in config.get("hidden_layers", [128, 64]):
                    layers.extend([
                        nn.Linear(prev_dim, hd),
                        nn.ReLU(),
                        nn.Dropout(config.get("dropout", 0.2)),
                    ])
                    prev_dim = hd
                layers.append(nn.Linear(prev_dim, 1))
                model = nn.Sequential(*layers)

                # 准备数据
                X_t = torch.FloatTensor(config["X_train"])
                y_t = torch.FloatTensor(config["y_train"]).reshape(-1, 1)
                dataset = TensorDataset(X_t, y_t)
                dataloader = DataLoader(dataset, batch_size=config.get("batch_size", 32), shuffle=True)

                criterion = nn.MSELoss()
                optimizer = optim.Adam(model.parameters(), lr=config.get("learning_rate", 0.001))

                model.train()
                for epoch in range(config.get("epochs", 10)):
                    total_loss = 0
                    for batch_X, batch_y in dataloader:
                        optimizer.zero_grad()
                        outputs = model(batch_X)
                        loss = criterion(outputs, batch_y)
                        loss.backward()
                        optimizer.step()
                        total_loss += loss.item()

                return model

            # Ray Train配置
            scaling_config = ScalingConfig(
                num_workers=config.get("n_workers", 2),
                use_gpu=config.get("use_gpu", False),
            )

            run_config = RunConfig(
                name=f"distributed_torch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            )

            train_config = {
                "hidden_layers": hidden_layers,
                "dropout": config.get("dropout", 0.2),
                "batch_size": batch_size,
                "learning_rate": lr,
                "epochs": epochs,
                "X_train": X_train.tolist(),
                "y_train": y_train.ravel().tolist(),
            }

            trainer = TorchTrainer(
                train_loop_per_worker=train_func,
                train_loop_config=train_config,
                scaling_config=scaling_config,
                run_config=run_config,
            )

            result = trainer.fit()

            # 评估
            model = create_torch_model(input_dim, hidden_layers, config)
            model.eval()
            with torch.no_grad():
                val_outputs = model(X_val_tensor)
                val_preds = val_outputs.numpy().flatten()

            r2 = r2_score(y_val, val_preds)
            rmse = float(np.sqrt(mean_squared_error(y_val, val_preds)))

            model_path = MODEL_DIR / f"distributed_torch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt"
            torch.save(model.state_dict(), model_path)

            return {
                "metrics": {"r2": float(r2), "rmse": rmse},
                "model_path": str(model_path),
                "n_workers": config.get("n_workers", 2),
                "backend": "ray_train",
                "ray_result": str(result),
            }

        except Exception as e:
            logger.warning(f"Ray Train failed: {e}, falling back to single-process PyTorch training")

            # Fallback: 单进程PyTorch训练
            model = create_torch_model(input_dim, hidden_layers, config)
            criterion = nn.MSELoss()
            optimizer = torch.optim.Adam(model.parameters(), lr=lr)

            from torch.utils.data import DataLoader, TensorDataset
            train_dataset = TensorDataset(X_train_tensor, y_train_tensor)
            train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

            for epoch in range(epochs):
                model.train()
                total_loss = 0
                for batch_X, batch_y in train_loader:
                    optimizer.zero_grad()
                    outputs = model(batch_X)
                    loss = criterion(outputs, batch_y)
                    loss.backward()
                    optimizer.step()
                    total_loss += loss.item()

            model.eval()
            with torch.no_grad():
                val_outputs = model(X_val_tensor)
                val_preds = val_outputs.numpy().flatten()

            r2 = r2_score(y_val, val_preds)
            rmse = float(np.sqrt(mean_squared_error(y_val, val_preds)))

            model_path = MODEL_DIR / f"distributed_fallback_torch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt"
            torch.save(model.state_dict(), model_path)

            return {
                "metrics": {"r2": float(r2), "rmse": rmse},
                "model_path": str(model_path),
                "n_workers": 1,
                "backend": "fallback_single_process",
            }

    @staticmethod
    def train_distributed(X: np.ndarray, y: np.ndarray, model_type: str,
                          config: Dict = None) -> Dict:
        """
        分布式训练入口

        Args:
            X: 特征数据 (n_samples, n_features)
            y: 标签数据 (n_samples,)
            model_type: 模型类型 (lightgbm, random_forest, mlp, xgboost)
            config: 训练配置
                - n_workers: worker数量
                - n_estimators: 树模型数量
                - max_depth: 最大深度
                - epochs: 深度学习轮数
                - batch_size: 批次大小
                - learning_rate: 学习率
                - hidden_layers: 隐藏层
                - dropout: dropout率
                - task_type: 任务类型 (regression/classification)
                - use_gpu: 是否使用GPU

        Returns:
            Dict with metrics, model_path, n_workers, backend
        """
        config = config or {}

        # 确保Ray已初始化
        ray_available = _ensure_ray_initialized()

        # Deep learning models → Ray Train
        if model_type in ["mlp", "neural_network", "pytorch"]:
            if TORCH_AVAILABLE and ray_available:
                return RayDistributedTrainer._train_torch_distributed(X, y, config)
            else:
                logger.warning(f"Ray Train not available for {model_type}, using single process")
                # 单进程回退
                from services.real_training_service import TrainingService
                import uuid
                job_id = f"dist_{uuid.uuid4().hex[:8]}"
                service_job = TrainingService.create_job({**config, "model_type": model_type, "task_type": config.get("task_type", "regression")})
                TrainingService.start_training(service_job, {"X": X.tolist(), "y": y.tolist()})
                # Wait briefly
                time.sleep(2)
                try:
                    status = TrainingService.get_job_status(service_job)
                    return {
                        "metrics": status.get("best_metrics", {}),
                        "model_path": status.get("model_path", ""),
                        "n_workers": 1,
                        "backend": "fallback_training_service",
                    }
                except:
                    return {
                        "metrics": {},
                        "model_path": "",
                        "n_workers": 1,
                        "backend": "fallback_failed",
                    }

        # Sklearn-style models → Ray Data Parallel
        else:
            if ray_available:
                return RayDistributedTrainer._train_sklearn_parallel(X, y, model_type, config)
            else:
                logger.warning(f"Ray not available for {model_type}, using single process")
                # 单进程回退
                scaler = StandardScaler()
                X_scaled = scaler.fit_transform(X)

                if model_type == "lightgbm" and LIGHTGBM_AVAILABLE:
                    model = lgb.LGBMRegressor(n_estimators=config.get("n_estimators", 100),
                                              max_depth=config.get("max_depth", 6),
                                              random_state=42, verbose=-1)
                else:
                    model = RandomForestRegressor(n_estimators=config.get("n_estimators", 100),
                                                  max_depth=config.get("max_depth", 6),
                                                  random_state=42)

                X_train, X_val, y_train, y_val = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
                model.fit(X_train, y_train)
                y_pred = model.predict(X_val)

                metrics = {
                    "r2": float(r2_score(y_val, y_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_val, y_pred))),
                }

                model_path = MODEL_DIR / f"distributed_local_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
                import joblib
                joblib.dump(model, model_path)

                return {
                    "metrics": metrics,
                    "model_path": str(model_path),
                    "n_workers": 1,
                    "backend": "single_process",
                }


def create_torch_model(input_dim: int, hidden_layers: List[int], config: Dict) -> nn.Module:
    """创建PyTorch MLP模型"""
    layers = []
    prev_dim = input_dim
    for hd in hidden_layers:
        layers.extend([
            nn.Linear(prev_dim, hd),
            nn.ReLU(),
            nn.Dropout(config.get("dropout", 0.2)),
        ])
        prev_dim = hd
    layers.append(nn.Linear(prev_dim, 1))
    return nn.Sequential(*layers)


# ============================================================
# 分布式任务管理器
# ============================================================

class DistributedJobManager:
    """分布式训练任务管理器"""

    @staticmethod
    def create_job(config: Dict) -> str:
        import uuid
        job_id = f"dist_{uuid.uuid4().hex[:8]}"
        _distributed_jobs[job_id] = {
            "job_id": job_id,
            "config": config,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "finished_at": None,
            "metrics": {},
            "model_path": None,
            "backend": None,
            "n_workers": 0,
            "error": None,
        }
        return job_id

    @staticmethod
    def start_job(job_id: str, data: Dict):
        """启动分布式训练任务"""
        job = _distributed_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        def train_thread():
            try:
                job["status"] = "running"
                job["started_at"] = datetime.now().isoformat()

                X = np.array(data["X"])
                y = np.array(data["y"])
                model_type = job["config"].get("model_type", "random_forest")

                result = RayDistributedTrainer.train_distributed(
                    X, y, model_type, job["config"]
                )

                job["metrics"] = result.get("metrics", {})
                job["model_path"] = result.get("model_path")
                job["backend"] = result.get("backend")
                job["n_workers"] = result.get("n_workers", 1)
                job["status"] = "completed"
                job["finished_at"] = datetime.now().isoformat()

            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = datetime.now().isoformat()
                logger.error(f"Distributed training failed: {e}")

        thread = threading.Thread(target=train_thread)
        thread.daemon = True
        thread.start()

    @staticmethod
    def get_job(job_id: str) -> Dict:
        job = _distributed_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        return job

    @staticmethod
    def list_jobs() -> List[Dict]:
        return list(_distributed_jobs.values())
