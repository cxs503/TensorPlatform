"""
分布式训练服务
支持数据并行、模型并行模拟、梯度累积
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# Torch 导入 — 在非GPU环境或无torch环境下降级
try:
    import torch
    import torch.distributed as dist
    import torch.nn as nn
    import torch.optim as optim
    from torch.nn.parallel import DistributedDataParallel as DDP
    from torch.utils.data import DataLoader, DistributedSampler, TensorDataset
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    nn = None  # 占位，使模块可导入

# 存储
_distributed_jobs: Dict[str, Dict] = {}

# 目录（在非Docker环境下回退到临时目录）
try:
    DIST_DIR = Path("/app/uploads/distributed_runs")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR = Path("/app/uploads/models")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, FileNotFoundError, OSError):
    import tempfile
    _tmpdir = Path(tempfile.gettempdir()) / "foundation_distributed"
    DIST_DIR = _tmpdir
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR = _tmpdir


class DistributedTrainingJob:
    """分布式训练任务"""

    def __init__(self, job_id: str, config: Dict):
        self.job_id = job_id
        self.config = config
        self.status = "pending"
        self.progress = 0.0
        self.current_epoch = 0
        self.total_epochs = config.get("epochs", 10)
        self.metrics_history: List[Dict] = []
        self.best_metrics: Dict = {}
        self.model_path: Optional[str] = None
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.workers = config.get("num_workers", 1)
        self.gradient_accumulation_steps = config.get("gradient_accumulation_steps", 1)
        self.mixed_precision = config.get("mixed_precision", False)


if _HAS_TORCH:

    class SimpleNN(nn.Module):
        """简单神经网络"""

        def __init__(self, input_dim: int, hidden_dims: List[int], output_dim: int):
            super().__init__()
            layers = []
            prev_dim = input_dim

            for hidden_dim in hidden_dims:
                layers.extend(
                    [
                        nn.Linear(prev_dim, hidden_dim),
                        nn.BatchNorm1d(hidden_dim),
                        nn.ReLU(),
                        nn.Dropout(0.2),
                    ]
                )
                prev_dim = hidden_dim

            layers.append(nn.Linear(prev_dim, output_dim))
            self.network = nn.Sequential(*layers)

        def forward(self, x):
            return self.network(x)


class DistributedTrainingService:
    """分布式训练服务"""

    @staticmethod
    def create_job(config: Dict) -> str:
        import uuid

        job_id = f"dist_{uuid.uuid4().hex[:8]}"

        job = DistributedTrainingJob(job_id, config)
        _distributed_jobs[job_id] = job

        return job_id

    @staticmethod
    def start_training(job_id: str, data: Dict):
        """启动分布式训练"""
        job = _distributed_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        def train_thread():
            try:
                job.status = "running"
                job.started_at = datetime.now().isoformat()

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

                # 准备PyTorch数据
                X_train_t = torch.FloatTensor(X_train)
                y_train_t = torch.FloatTensor(y_train).reshape(-1, 1)
                X_val_t = torch.FloatTensor(X_val)
                y_val_t = torch.FloatTensor(y_val).reshape(-1, 1)

                # 创建数据加载器（模拟多worker）
                batch_size = job.config.get("batch_size", 32)
                train_dataset = TensorDataset(X_train_t, y_train_t)
                train_loader = DataLoader(
                    train_dataset,
                    batch_size=batch_size,
                    shuffle=True,
                    num_workers=0,  # 单机模拟
                )

                # 创建模型
                input_dim = X_train.shape[1]
                hidden_dims = job.config.get("hidden_dims", [128, 64])
                output_dim = 1

                model = SimpleNN(input_dim, hidden_dims, output_dim)

                # 损失和优化器
                criterion = nn.MSELoss()
                optimizer = optim.Adam(
                    model.parameters(), lr=job.config.get("learning_rate", 0.001)
                )

                # 混合精度训练
                scaler_mp = None
                if job.mixed_precision and torch.cuda.is_available():
                    scaler_mp = torch.amp.GradScaler("cuda")

                # 训练循环
                epochs = job.total_epochs
                accumulation_steps = job.gradient_accumulation_steps

                for epoch in range(1, epochs + 1):
                    model.train()
                    total_loss = 0
                    optimizer.zero_grad()

                    for batch_idx, (batch_X, batch_y) in enumerate(train_loader):
                        # 前向传播
                        outputs = model(batch_X)
                        loss = criterion(outputs, batch_y)

                        # 梯度累积
                        loss = loss / accumulation_steps

                        if scaler_mp:
                            scaler_mp.scale(loss).backward()
                            if (batch_idx + 1) % accumulation_steps == 0:
                                scaler_mp.step(optimizer)
                                scaler_mp.update()
                                optimizer.zero_grad()
                        else:
                            loss.backward()
                            if (batch_idx + 1) % accumulation_steps == 0:
                                optimizer.step()
                                optimizer.zero_grad()

                        total_loss += loss.item() * accumulation_steps

                    avg_loss = total_loss / len(train_loader)

                    # 验证
                    model.eval()
                    with torch.no_grad():
                        val_outputs = model(X_val_t)
                        val_loss = criterion(val_outputs, y_val_t).item()

                        # 计算R²
                        ss_res = torch.sum((y_val_t - val_outputs) ** 2)
                        ss_tot = torch.sum((y_val_t - torch.mean(y_val_t)) ** 2)
                        r2 = 1 - ss_res / ss_tot

                    metrics = {
                        "epoch": epoch,
                        "loss": avg_loss,
                        "val_loss": val_loss,
                        "r2": r2.item(),
                    }

                    job.metrics_history.append(metrics)
                    job.current_epoch = epoch
                    job.progress = (epoch / epochs) * 100

                    if not job.best_metrics or r2.item() > job.best_metrics.get(
                        "r2", 0
                    ):
                        job.best_metrics = metrics

                    # 保存进度
                    DistributedTrainingService._save_job(job)

                # 保存模型
                model_path = MODEL_DIR / f"{job_id}_model.pt"
                torch.save(model.state_dict(), model_path)
                job.model_path = str(model_path)

                job.status = "completed"
                job.finished_at = datetime.now().isoformat()
                job.progress = 100.0
                DistributedTrainingService._save_job(job)

            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.finished_at = datetime.now().isoformat()
                DistributedTrainingService._save_job(job)
                print(f"Distributed training error: {e}")
                import traceback

                traceback.print_exc()

        thread = threading.Thread(target=train_thread)
        thread.daemon = True
        thread.start()

        return {"job_id": job_id, "status": "started"}

    @staticmethod
    def get_job_status(job_id: str) -> Dict:
        job = _distributed_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        return {
            "job_id": job.job_id,
            "status": job.status,
            "progress": job.progress,
            "current_epoch": job.current_epoch,
            "total_epochs": job.total_epochs,
            "metrics_history": job.metrics_history,
            "best_metrics": job.best_metrics,
            "model_path": job.model_path,
            "error": job.error,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "config": {
                "num_workers": job.workers,
                "gradient_accumulation_steps": job.gradient_accumulation_steps,
                "mixed_precision": job.mixed_precision,
            },
        }

    @staticmethod
    def list_jobs() -> List[Dict]:
        return [
            DistributedTrainingService.get_job_status(jid)
            for jid in _distributed_jobs.keys()
        ]

    @staticmethod
    def _save_job(job: DistributedTrainingJob):
        run_file = DIST_DIR / f"{job.job_id}.json"
        data = {
            "job_id": job.job_id,
            "config": job.config,
            "status": job.status,
            "progress": job.progress,
            "current_epoch": job.current_epoch,
            "total_epochs": job.total_epochs,
            "metrics_history": job.metrics_history,
            "best_metrics": job.best_metrics,
            "model_path": job.model_path,
            "error": job.error,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        with open(run_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @staticmethod
    def get_cluster_info() -> Dict:
        """获取集群信息"""
        import multiprocessing

        import psutil

        return {
            "num_cpus": multiprocessing.cpu_count(),
            "num_gpus": torch.cuda.device_count() if torch.cuda.is_available() else 0,
            "total_memory_gb": psutil.virtual_memory().total / (1024**3),
            "available_memory_gb": psutil.virtual_memory().available / (1024**3),
            "gpu_info": [
                {
                    "name": torch.cuda.get_device_name(i),
                    "memory_gb": torch.cuda.get_device_properties(i).total_memory
                    / (1024**3),
                }
                for i in range(torch.cuda.device_count())
            ]
            if torch.cuda.is_available()
            else [],
        }

    @staticmethod
    def estimate_training_time(
        dataset_size: int, model_params: int, epochs: int, batch_size: int = 32
    ) -> Dict:
        """估算训练时间"""
        # 基于经验公式估算
        samples_per_epoch = dataset_size
        batches_per_epoch = samples_per_epoch // batch_size

        # 假设每批次约10ms (CPU)
        time_per_batch_ms = 10
        if torch.cuda.is_available():
            time_per_batch_ms = 2  # GPU更快

        estimated_time_seconds = batches_per_epoch * time_per_batch_ms / 1000 * epochs

        return {
            "dataset_size": dataset_size,
            "model_params": model_params,
            "epochs": epochs,
            "batch_size": batch_size,
            "batches_per_epoch": batches_per_epoch,
            "estimated_time_seconds": estimated_time_seconds,
            "estimated_time_minutes": estimated_time_seconds / 60,
        }


def get_distributed_training_service():
    return DistributedTrainingService
