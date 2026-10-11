"""
MLOps闭环服务
实现数据-训练-部署-监控的完整闭环
"""
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib

# 存储
_mlops_pipelines: Dict[str, Dict] = {}
_model_registry: Dict[str, Dict] = {}
_deployment_registry: Dict[str, Dict] = {}
_monitoring_data: Dict[str, List] = {}

# 目录
MLOPS_DIR = Path("/app/uploads/mlops")
MLOPS_DIR.mkdir(parents=True, exist_ok=True)

(MLOPS_DIR / "pipelines").mkdir(exist_ok=True)
(MLOPS_DIR / "models").mkdir(exist_ok=True)
(MLOPS_DIR / "deployments").mkdir(exist_ok=True)
(MLOPS_DIR / "monitoring").mkdir(exist_ok=True)

MODEL_DIR = Path("/app/uploads/models")
DATASET_DIR = Path("/app/uploads/datasets")


class MLOpsPipeline:
    """MLOps流水线"""

    def __init__(self, pipeline_id: str, config: Dict):
        self.pipeline_id = pipeline_id
        self.config = config
        self.name = config.get("name", "未命名流水线")
        self.status = "created"
        self.current_stage = None
        self.stages = {
            "data_preparation": {"status": "pending", "output": None},
            "feature_engineering": {"status": "pending", "output": None},
            "model_training": {"status": "pending", "output": None},
            "model_evaluation": {"status": "pending", "output": None},
            "model_registration": {"status": "pending", "output": None},
            "model_deployment": {"status": "pending", "output": None},
            "monitoring": {"status": "pending", "output": None},
        }
        self.artifacts = {}
        self.metrics = {}
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.error = None

    def to_dict(self) -> Dict:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "config": self.config,
            "status": self.status,
            "current_stage": self.current_stage,
            "stages": self.stages,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }


class DataVersionControl:
    """数据版本控制"""

    @staticmethod
    def compute_data_hash(data_path: str) -> str:
        """计算数据哈希"""
        hasher = hashlib.md5()
        with open(data_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def create_data_version(
        dataset_id: str, data_path: str, metadata: Dict = None
    ) -> Dict:
        """创建数据版本"""
        version_id = f"dv_{int(time.time())}"

        data_hash = DataVersionControl.compute_data_hash(data_path)

        version = {
            "version_id": version_id,
            "dataset_id": dataset_id,
            "data_path": data_path,
            "data_hash": data_hash,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
            "size_bytes": os.path.getsize(data_path),
        }

        # 保存版本信息
        version_file = MLOPS_DIR / "data_versions" / f"{version_id}.json"
        version_file.parent.mkdir(exist_ok=True)
        with open(version_file, "w") as f:
            json.dump(version, f, indent=2)

        return version

    @staticmethod
    def list_data_versions(dataset_id: str = None) -> List[Dict]:
        """列出数据版本"""
        versions_dir = MLOPS_DIR / "data_versions"
        if not versions_dir.exists():
            return []

        versions = []
        for f in versions_dir.glob("*.json"):
            with open(f) as fp:
                v = json.load(fp)
                if dataset_id is None or v.get("dataset_id") == dataset_id:
                    versions.append(v)

        return sorted(versions, key=lambda x: x["created_at"], reverse=True)


class ModelRegistry:
    """模型注册中心"""

    @staticmethod
    def register_model(
        model_path: str,
        model_name: str,
        version: str = None,
        metrics: Dict = None,
        data_version_id: str = None,
        training_config: Dict = None,
    ) -> Dict:
        """注册模型"""
        if version is None:
            version = f"v{int(time.time())}"

        model_id = f"model_{hashlib.md5(model_name.encode()).hexdigest()[:8]}"

        registration = {
            "model_id": model_id,
            "model_name": model_name,
            "version": version,
            "model_path": model_path,
            "metrics": metrics or {},
            "data_version_id": data_version_id,
            "training_config": training_config or {},
            "status": "registered",
            "created_at": datetime.now().isoformat(),
            "deployed_to": [],
            "tags": [],
        }

        _model_registry[model_id] = registration

        # 保存
        reg_file = MLOPS_DIR / "models" / f"{model_id}.json"
        with open(reg_file, "w") as f:
            json.dump(registration, f, indent=2)

        return registration

    @staticmethod
    def get_model(model_id: str) -> Dict:
        """获取模型信息"""
        if model_id in _model_registry:
            return _model_registry[model_id]

        reg_file = MLOPS_DIR / "models" / f"{model_id}.json"
        if reg_file.exists():
            with open(reg_file) as f:
                return json.load(f)

        return None

    @staticmethod
    def list_models(name: str = None, status: str = None) -> List[Dict]:
        """列出模型"""
        models = []
        for f in (MLOPS_DIR / "models").glob("*.json"):
            with open(f) as fp:
                m = json.load(fp)
                if name is None or name in m.get("model_name", ""):
                    if status is None or m.get("status") == status:
                        models.append(m)

        return sorted(models, key=lambda x: x["created_at"], reverse=True)

    @staticmethod
    def add_model_tag(model_id: str, tag: str) -> Dict:
        """添加模型标签"""
        model = ModelRegistry.get_model(model_id)
        if model:
            if tag not in model.get("tags", []):
                model.setdefault("tags", []).append(tag)

                reg_file = MLOPS_DIR / "models" / f"{model_id}.json"
                with open(reg_file, "w") as f:
                    json.dump(model, f, indent=2)

                _model_registry[model_id] = model

        return model


class ModelDeployment:
    """模型部署"""

    @staticmethod
    def deploy_model(
        model_id: str, deployment_name: str, deployment_config: Dict = None
    ) -> Dict:
        """部署模型"""
        model = ModelRegistry.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        deployment_id = f"deploy_{int(time.time())}"

        deployment = {
            "deployment_id": deployment_id,
            "deployment_name": deployment_name,
            "model_id": model_id,
            "model_name": model["model_name"],
            "model_version": model["version"],
            "model_path": model["model_path"],
            "config": deployment_config or {},
            "status": "deploying",
            "endpoint": f"/api/v1/inference/{deployment_id}",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "requests_count": 0,
            "avg_latency_ms": 0,
            "last_request_at": None,
        }

        _deployment_registry[deployment_id] = deployment

        # 保存
        deploy_file = MLOPS_DIR / "deployments" / f"{deployment_id}.json"
        with open(deploy_file, "w") as f:
            json.dump(deployment, f, indent=2)

        # 更新模型的部署信息
        model.setdefault("deployed_to", []).append(deployment_id)
        reg_file = MLOPS_DIR / "models" / f"{model_id}.json"
        with open(reg_file, "w") as f:
            json.dump(model, f, indent=2)

        # 模拟部署完成
        deployment["status"] = "running"
        deployment["updated_at"] = datetime.now().isoformat()
        with open(deploy_file, "w") as f:
            json.dump(deployment, f, indent=2)

        return deployment

    @staticmethod
    def get_deployment(deployment_id: str) -> Dict:
        """获取部署信息"""
        if deployment_id in _deployment_registry:
            return _deployment_registry[deployment_id]

        deploy_file = MLOPS_DIR / "deployments" / f"{deployment_id}.json"
        if deploy_file.exists():
            with open(deploy_file) as f:
                return json.load(f)

        return None

    @staticmethod
    def list_deployments(status: str = None) -> List[Dict]:
        """列出部署"""
        deployments = []
        for f in (MLOPS_DIR / "deployments").glob("*.json"):
            with open(f) as fp:
                d = json.load(fp)
                if status is None or d.get("status") == status:
                    deployments.append(d)

        return sorted(deployments, key=lambda x: x["created_at"], reverse=True)

    @staticmethod
    def predict(deployment_id: str, X: List) -> Dict:
        """推理预测"""
        deployment = ModelDeployment.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")

        if deployment["status"] != "running":
            raise ValueError(f"Deployment {deployment_id} is not running")

        import time

        import numpy as np

        # 加载模型
        model = joblib.load(deployment["model_path"])

        # 预测
        start_time = time.time()
        predictions = model.predict(np.array(X)).tolist()
        latency = (time.time() - start_time) * 1000

        # 更新统计
        deployment["requests_count"] += 1
        deployment["avg_latency_ms"] = (
            deployment["avg_latency_ms"] * (deployment["requests_count"] - 1) + latency
        ) / deployment["requests_count"]
        deployment["last_request_at"] = datetime.now().isoformat()
        deployment["updated_at"] = datetime.now().isoformat()

        deploy_file = MLOPS_DIR / "deployments" / f"{deployment_id}.json"
        with open(deploy_file, "w") as f:
            json.dump(deployment, f, indent=2)

        _deployment_registry[deployment_id] = deployment

        # 记录监控数据
        MonitoringService.log_prediction(
            deployment_id, {"latency_ms": latency, "predictions": len(predictions)}
        )

        return {
            "deployment_id": deployment_id,
            "predictions": predictions,
            "latency_ms": latency,
        }


class MonitoringService:
    """模型监控服务"""

    @staticmethod
    def log_prediction(deployment_id: str, prediction_data: Dict):
        """记录预测数据"""
        if deployment_id not in _monitoring_data:
            _monitoring_data[deployment_id] = []

        record = {"timestamp": datetime.now().isoformat(), **prediction_data}

        _monitoring_data[deployment_id].append(record)

        # 限制内存中的记录数
        if len(_monitoring_data[deployment_id]) > 1000:
            _monitoring_data[deployment_id] = _monitoring_data[deployment_id][-500:]

    @staticmethod
    def get_metrics(deployment_id: str, hours: int = 24) -> Dict:
        """获取监控指标"""
        deployment = ModelDeployment.get_deployment(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment {deployment_id} not found")

        records = _monitoring_data.get(deployment_id, [])

        # 过滤时间范围
        cutoff = datetime.now().timestamp() - hours * 3600
        recent_records = [
            r
            for r in records
            if datetime.fromisoformat(r["timestamp"]).timestamp() > cutoff
        ]

        # 计算统计
        latencies = [r.get("latency_ms", 0) for r in recent_records]

        return {
            "deployment_id": deployment_id,
            "period_hours": hours,
            "total_requests": len(recent_records),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "max_latency_ms": max(latencies) if latencies else 0,
            "min_latency_ms": min(latencies) if latencies else 0,
            "requests_per_hour": len(recent_records) / hours if hours > 0 else 0,
            "deployment_status": deployment.get("status"),
            "model_info": {
                "model_id": deployment.get("model_id"),
                "model_name": deployment.get("model_name"),
                "model_version": deployment.get("model_version"),
            },
        }

    @staticmethod
    def detect_drift(
        deployment_id: str, baseline_metrics: Dict, current_metrics: Dict
    ) -> Dict:
        """检测模型漂移"""
        drift_detected = False
        drift_details = {}

        # 检查性能下降
        for metric_name in ["accuracy", "f1", "r2"]:
            if metric_name in baseline_metrics and metric_name in current_metrics:
                baseline = baseline_metrics[metric_name]
                current = current_metrics[metric_name]
                change = (baseline - current) / baseline if baseline != 0 else 0

                if change > 0.1:  # 10%下降
                    drift_detected = True
                    drift_details[metric_name] = {
                        "baseline": baseline,
                        "current": current,
                        "change_percent": change * 100,
                        "status": "degraded",
                    }

        return {
            "deployment_id": deployment_id,
            "drift_detected": drift_detected,
            "drift_details": drift_details,
            "recommendation": "建议重新训练模型" if drift_detected else "模型表现正常",
        }


class MLOpsService:
    """MLOps主服务"""

    @staticmethod
    def create_pipeline(config: Dict) -> str:
        """创建MLOps流水线"""
        import uuid

        pipeline_id = f"pipeline_{uuid.uuid4().hex[:8]}"

        pipeline = MLOpsPipeline(pipeline_id, config)
        _mlops_pipelines[pipeline_id] = pipeline

        # 保存
        MLOpsService._save_pipeline(pipeline)

        return pipeline_id

    @staticmethod
    def run_pipeline(pipeline_id: str, data_config: Dict = None):
        """运行流水线"""
        pipeline = _mlops_pipelines.get(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        def run_thread():
            try:
                pipeline.status = "running"
                pipeline.updated_at = datetime.now().isoformat()

                # 阶段1: 数据准备
                pipeline.current_stage = "data_preparation"
                pipeline.stages["data_preparation"]["status"] = "running"
                MLOpsService._save_pipeline(pipeline)

                # 模拟数据准备
                time.sleep(1)
                pipeline.stages["data_preparation"]["status"] = "completed"
                pipeline.stages["data_preparation"]["output"] = {
                    "dataset_id": data_config.get("dataset_id", "default"),
                    "samples": data_config.get("samples", 1000),
                }

                # 阶段2: 特征工程
                pipeline.current_stage = "feature_engineering"
                pipeline.stages["feature_engineering"]["status"] = "running"
                MLOpsService._save_pipeline(pipeline)

                time.sleep(1)
                pipeline.stages["feature_engineering"]["status"] = "completed"
                pipeline.stages["feature_engineering"]["output"] = {
                    "features_count": data_config.get("features", 10)
                }

                # 阶段3: 模型训练
                pipeline.current_stage = "model_training"
                pipeline.stages["model_training"]["status"] = "running"
                MLOpsService._save_pipeline(pipeline)

                time.sleep(2)
                pipeline.stages["model_training"]["status"] = "completed"
                pipeline.stages["model_training"]["output"] = {
                    "model_type": pipeline.config.get("model_type", "lightgbm"),
                    "training_time_seconds": 2,
                }

                # 阶段4: 模型评估
                pipeline.current_stage = "model_evaluation"
                pipeline.stages["model_evaluation"]["status"] = "running"
                MLOpsService._save_pipeline(pipeline)

                time.sleep(1)
                # 演示态指标:本流水线用 time.sleep 模拟各阶段,未执行真实训练。
                # 标注 demo 以避免被误认为真实模型评估结果。
                metrics = {
                    "accuracy": 0.92,
                    "f1": 0.91,
                    "precision": 0.93,
                    "recall": 0.90,
                    "demo": True,
                    "note": "演示指标,非真实模型评估结果(流水线用 time.sleep 模拟,未执行训练)",
                }
                pipeline.stages["model_evaluation"]["status"] = "completed"
                pipeline.stages["model_evaluation"]["output"] = metrics
                pipeline.metrics = metrics

                # 阶段5: 模型注册
                pipeline.current_stage = "model_registration"
                pipeline.stages["model_registration"]["status"] = "running"
                MLOpsService._save_pipeline(pipeline)

                time.sleep(1)
                model_reg = ModelRegistry.register_model(
                    model_path="/app/uploads/models/pipeline_model.pkl",
                    model_name=pipeline.name,
                    metrics=metrics,
                    training_config=pipeline.config,
                )
                pipeline.stages["model_registration"]["status"] = "completed"
                pipeline.stages["model_registration"]["output"] = model_reg
                pipeline.artifacts["model_id"] = model_reg["model_id"]

                # 阶段6: 模型部署
                if pipeline.config.get("auto_deploy", False):
                    pipeline.current_stage = "model_deployment"
                    pipeline.stages["model_deployment"]["status"] = "running"
                    MLOpsService._save_pipeline(pipeline)

                    time.sleep(1)
                    deployment = ModelDeployment.deploy_model(
                        model_reg["model_id"], f"{pipeline.name}-deployment"
                    )
                    pipeline.stages["model_deployment"]["status"] = "completed"
                    pipeline.stages["model_deployment"]["output"] = deployment
                    pipeline.artifacts["deployment_id"] = deployment["deployment_id"]

                # 完成
                pipeline.status = "completed"
                pipeline.current_stage = None
                pipeline.updated_at = datetime.now().isoformat()
                MLOpsService._save_pipeline(pipeline)

            except Exception as e:
                pipeline.status = "failed"
                pipeline.error = str(e)
                pipeline.updated_at = datetime.now().isoformat()
                MLOpsService._save_pipeline(pipeline)

        thread = threading.Thread(target=run_thread)
        thread.daemon = True
        thread.start()

        return {"pipeline_id": pipeline_id, "status": "started"}

    @staticmethod
    def get_pipeline(pipeline_id: str) -> Dict:
        """获取流水线状态"""
        if pipeline_id in _mlops_pipelines:
            return _mlops_pipelines[pipeline_id].to_dict()

        pipeline_file = MLOPS_DIR / "pipelines" / f"{pipeline_id}.json"
        if pipeline_file.exists():
            with open(pipeline_file) as f:
                return json.load(f)

        return None

    @staticmethod
    def list_pipelines(status: str = None) -> List[Dict]:
        """列出流水线"""
        pipelines = []
        for f in (MLOPS_DIR / "pipelines").glob("*.json"):
            with open(f) as fp:
                p = json.load(fp)
                if status is None or p.get("status") == status:
                    pipelines.append(p)

        return sorted(pipelines, key=lambda x: x["created_at"], reverse=True)

    @staticmethod
    def _save_pipeline(pipeline: MLOpsPipeline):
        pipeline_file = MLOPS_DIR / "pipelines" / f"{pipeline.pipeline_id}.json"
        with open(pipeline_file, "w") as f:
            json.dump(pipeline.to_dict(), f, indent=2)

    @staticmethod
    def get_lineage(model_id: str) -> Dict:
        """获取模型血缘"""
        model = ModelRegistry.get_model(model_id)
        if not model:
            return {"error": "Model not found"}

        lineage = {
            "model": model,
            "data_version": None,
            "training_pipeline": None,
            "deployments": [],
            "upstream_datasets": [],
            "downstream_applications": [],
        }

        # 获取数据版本
        if model.get("data_version_id"):
            from pathlib import Path

            dv_file = MLOPS_DIR / "data_versions" / f"{model['data_version_id']}.json"
            if dv_file.exists():
                with open(dv_file) as f:
                    lineage["data_version"] = json.load(f)

        # 获取部署
        for deploy_id in model.get("deployed_to", []):
            deploy = ModelDeployment.get_deployment(deploy_id)
            if deploy:
                lineage["deployments"].append(deploy)

        return lineage


def get_mlops_service():
    return MLOpsService
